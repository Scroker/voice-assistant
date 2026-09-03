"""Vector Store for Retrieval Augmented Generation (RAG).

Hybrid in-memory + SQLite vector store with deduplication for semantic search over documents.
Provides fast in-memory access (<5ms) with persistent SQLite backend.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("VoiceAssistant.VectorStore")


class Document:
    """A document in the vector store with metadata."""

    def __init__(
        self,
        content: str,
        doc_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        embedding: Optional[List[float]] = None,
    ):
        self.content = content
        self.doc_id = doc_id or self._hash_content(content)
        self.metadata = metadata or {}
        self.embedding = embedding or []
        self.timestamp = time.time()

    @staticmethod
    def _hash_content(content: str) -> str:
        """Generate a unique ID from content hash."""
        return hashlib.md5(content.encode()).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "content": self.content,
            "metadata": self.metadata,
            "embedding": self.embedding,
            "timestamp": self.timestamp,
        }


def _simple_tokenize(text: str) -> List[str]:
    """Simple tokenization for sparse vector representation."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s']", " ", text)
    tokens = [t for t in text.split() if len(t) > 1]
    return tokens


def _build_sparse_vector(tokens: List[str]) -> Dict[str, float]:
    """Build sparse vector from tokens (term frequency)."""
    vec = {}
    total = len(tokens)
    for token in tokens:
        vec[token] = vec.get(token, 0) + 1.0 / total if total > 0 else 1.0
    return vec


def _cosine_similarity(vec1: Dict[str, float], vec2: Dict[str, float]) -> float:
    """Compute cosine similarity between sparse vectors."""
    if not vec1 or not vec2:
        return 0.0

    dot = sum(vec1.get(k, 0.0) * v for k, v in vec2.items())
    norm1 = sum(v * v for v in vec1.values()) ** 0.5
    norm2 = sum(v * v for v in vec2.values()) ** 0.5

    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)


class VectorStore:
    """Hybrid in-memory + SQLite vector store for RAG.
    
    Features:
    - Fast in-memory access (<5ms search latency)
    - Persistent SQLite backend for durability
    - Asynchronous background sync every 30 seconds
    - Automatic TTL with SQL triggers (24 hours default)
    - Deduplication via content hashing
    - Same API as pure in-memory store
    """

    def __init__(
        self, 
        max_documents: int = 1000,
        db_path: Optional[str] = None,
        sync_interval: int = 30,
        ttl_seconds: int = 86400,  # 24 hours
    ):
        """Initialize hybrid vector store.

        Args:
            max_documents: Maximum number of documents in memory
            db_path: Path to SQLite database (default: ~/.local/share/voice-assistant/rag_store.db)
            sync_interval: Seconds between database syncs (default: 30)
            ttl_seconds: Document time-to-live in seconds (default: 86400 = 24h)
        """
        self.max_documents = max_documents
        self.sync_interval = sync_interval
        self.ttl_seconds = ttl_seconds
        
        # In-memory cache for fast access
        self.documents: Dict[str, Document] = {}
        self.doc_vectors: Dict[str, Dict[str, float]] = {}
        
        # SQLite backend for persistence
        if db_path is None:
            data_dir = Path.home() / ".local" / "share" / "voice-assistant"
            data_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(data_dir / "rag_store.db")
        
        self.db_path = db_path
        self._init_db()
        self._load_from_db()
        
        # Background sync thread
        self._sync_thread = threading.Thread(
            target=self._periodic_sync, daemon=True, name="VectorStoreSync"
        )
        self._sync_running = True
        self._sync_thread.start()
        
        logger.info(f"[VectorStore] Initialized: db={db_path}, in-memory docs={len(self.documents)}")

    def _init_db(self) -> None:
        """Initialize SQLite database with schema."""
        try:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            conn.isolation_level = None  # autocommit mode
            cursor = conn.cursor()
            
            # Create documents table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    doc_id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    metadata TEXT,
                    embedding TEXT,
                    timestamp REAL NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
            
            # Create index on timestamp for TTL queries
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_documents_timestamp 
                ON documents(timestamp)
            """)
            
            # Create trigger for automatic TTL cleanup
            cursor.execute(f"""
                CREATE TRIGGER IF NOT EXISTS documents_ttl_cleanup
                AFTER INSERT ON documents
                WHEN (SELECT COUNT(*) FROM documents) > {self.max_documents}
                BEGIN
                    DELETE FROM documents 
                    WHERE timestamp < unixepoch('now') - {self.ttl_seconds};
                END
            """)
            
            conn.close()
            logger.debug(f"[VectorStore] Database initialized: {self.db_path}")
        except Exception as e:
            logger.error(f"[VectorStore] Database init failed: {e}")
            raise

    def _load_from_db(self) -> None:
        """Load recent documents from SQLite on startup."""
        try:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            conn.isolation_level = None  # autocommit mode
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Load documents updated in last 24 hours
            cursor.execute("""
                SELECT doc_id, content, metadata, embedding, timestamp
                FROM documents
                WHERE timestamp > unixepoch('now') - 86400
                ORDER BY timestamp DESC
                LIMIT ?
            """, (self.max_documents,))
            
            rows = cursor.fetchall()
            for row in rows:
                try:
                    doc = Document(
                        content=row["content"],
                        doc_id=row["doc_id"],
                        metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                        embedding=json.loads(row["embedding"]) if row["embedding"] else [],
                    )
                    doc.timestamp = row["timestamp"]
                    
                    tokens = _simple_tokenize(doc.content)
                    vec = _build_sparse_vector(tokens)
                    
                    self.documents[doc.doc_id] = doc
                    self.doc_vectors[doc.doc_id] = vec
                except Exception as e:
                    logger.warning(f"[VectorStore] Failed to load doc {row['doc_id']}: {e}")
            
            conn.close()
            logger.info(f"[VectorStore] Loaded {len(self.documents)} documents from DB")
        except Exception as e:
            logger.error(f"[VectorStore] Load from DB failed: {e}")

    def add_document(
        self,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        doc_id: Optional[str] = None,
    ) -> str:
        """Add a document to the store with deduplication.

        Returns:
            The document ID (new or existing if deduplicated)
        """
        # Check for duplicates
        test_id = Document._hash_content(content)
        if test_id in self.documents:
            logger.debug(f"[VectorStore] Document already exists: {test_id}")
            return test_id

        # Evict if at capacity
        if len(self.documents) >= self.max_documents:
            oldest_id = min(
                self.documents.keys(),
                key=lambda k: self.documents[k].timestamp,
            )
            self.remove_document(oldest_id)
            logger.debug(f"[VectorStore] Evicted oldest document: {oldest_id}")

        # Add to in-memory cache
        doc = Document(content=content, doc_id=doc_id or test_id, metadata=metadata)
        tokens = _simple_tokenize(content)
        vec = _build_sparse_vector(tokens)

        self.documents[doc.doc_id] = doc
        self.doc_vectors[doc.doc_id] = vec
        logger.info(f"[VectorStore] Added document {doc.doc_id}: {content[:50]}...")

        return doc.doc_id

    def search(self, query: str, top_k: int = 5, min_score: float = 0.1) -> List[Tuple[str, float]]:
        """Search for similar documents in memory.

        Args:
            query: Search query text
            top_k: Number of results to return
            min_score: Minimum similarity score threshold

        Returns:
            List of (content, score) tuples
        """
        query_tokens = _simple_tokenize(query)
        query_vec = _build_sparse_vector(query_tokens)

        scores = []
        for doc_id, doc_vec in self.doc_vectors.items():
            score = _cosine_similarity(query_vec, doc_vec)
            if score >= min_score:
                scores.append((doc_id, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        results = []
        for doc_id, score in scores[:top_k]:
            doc = self.documents[doc_id]
            results.append((doc.content, score))

        return results

    def remove_document(self, doc_id: str) -> bool:
        """Remove a document from memory cache."""
        if doc_id in self.documents:
            del self.documents[doc_id]
            del self.doc_vectors[doc_id]
            logger.debug(f"[VectorStore] Removed document: {doc_id}")
            return True
        return False

    def get_all_documents(self) -> List[Dict[str, Any]]:
        """Get all documents from memory cache."""
        return [doc.to_dict() for doc in self.documents.values()]

    def clear(self) -> None:
        """Clear all documents from memory cache."""
        self.documents.clear()
        self.doc_vectors.clear()
        logger.info("[VectorStore] Cleared all documents from memory")

    def get_size(self) -> int:
        """Get number of documents in memory cache."""
        return len(self.documents)

    def _periodic_sync(self) -> None:
        """Background thread: periodically sync memory to SQLite."""
        while self._sync_running:
            try:
                time.sleep(self.sync_interval)
                self._sync_to_db()
            except Exception as e:
                logger.error(f"[VectorStore] Periodic sync failed: {e}")

    def _sync_to_db(self) -> None:
        """Sync all in-memory documents to SQLite."""
        try:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            conn.isolation_level = None  # autocommit mode
            cursor = conn.cursor()
            
            for doc_id, doc in self.documents.items():
                cursor.execute("""
                    INSERT OR REPLACE INTO documents 
                    (doc_id, content, metadata, embedding, timestamp, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    doc.doc_id,
                    doc.content,
                    json.dumps(doc.metadata),
                    json.dumps(doc.embedding),
                    doc.timestamp,
                    time.time(),
                ))
            
            # Cleanup expired documents
            # Use unixepoch() to properly compare Unix timestamps
            cursor.execute(f"""
                DELETE FROM documents 
                WHERE timestamp < unixepoch('now') - {self.ttl_seconds}
            """)
            
            # VACUUM to optimize DB size
            conn.execute("VACUUM")
            
            conn.close()
            logger.debug(f"[VectorStore] Synced {len(self.documents)} documents to DB")
        except Exception as e:
            logger.error(f"[VectorStore] Failed to sync to DB: {e}", exc_info=True)

    def force_sync(self) -> None:
        """Force immediate sync to SQLite (blocking)."""
        self._sync_to_db()
        logger.info("[VectorStore] Forced sync completed")

    def close(self) -> None:
        """Shutdown: sync and close database."""
        self._sync_running = False
        if self._sync_thread.is_alive():
            self._sync_thread.join(timeout=5)
        self._sync_to_db()
        logger.info("[VectorStore] Closed")
