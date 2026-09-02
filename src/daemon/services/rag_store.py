"""Vector Store for Retrieval Augmented Generation (RAG).

Lightweight in-memory vector store with deduplication for semantic search over documents.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
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
    """Lightweight vector store for RAG with deduplication."""

    def __init__(self, max_documents: int = 1000):
        """Initialize vector store.

        Args:
            max_documents: Maximum number of documents to keep
        """
        self.max_documents = max_documents
        self.documents: Dict[str, Document] = {}
        self.doc_vectors: Dict[str, Dict[str, float]] = {}

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

        # Add new document
        doc = Document(content=content, doc_id=doc_id or test_id, metadata=metadata)
        tokens = _simple_tokenize(content)
        vec = _build_sparse_vector(tokens)

        self.documents[doc.doc_id] = doc
        self.doc_vectors[doc.doc_id] = vec
        logger.info(f"[VectorStore] Added document {doc.doc_id}: {content[:50]}...")

        return doc.doc_id

    def search(self, query: str, top_k: int = 5, min_score: float = 0.1) -> List[Tuple[str, float]]:
        """Search for similar documents.

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
        """Remove a document from the store."""
        if doc_id in self.documents:
            del self.documents[doc_id]
            del self.doc_vectors[doc_id]
            logger.debug(f"[VectorStore] Removed document: {doc_id}")
            return True
        return False

    def get_all_documents(self) -> List[Dict[str, Any]]:
        """Get all documents."""
        return [doc.to_dict() for doc in self.documents.values()]

    def clear(self) -> None:
        """Clear all documents."""
        self.documents.clear()
        self.doc_vectors.clear()
        logger.info("[VectorStore] Cleared all documents")

    def get_size(self) -> int:
        """Get number of documents in store."""
        return len(self.documents)
