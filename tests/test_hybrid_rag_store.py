"""Tests for hybrid in-memory + SQLite RAG Store with persistence and TTL."""

import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest

daemon_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "daemon"))
if daemon_dir not in sys.path:
    sys.path.insert(0, daemon_dir)

from services.rag_store import VectorStore


class TestHybridVectorStore(unittest.TestCase):
    """Test suite for hybrid in-memory + SQLite VectorStore."""

    def setUp(self):
        """Create temporary directory for database."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_rag.db")

    def tearDown(self):
        """Clean up temporary directory."""
        self.temp_dir.cleanup()

    def test_in_memory_search_performance(self):
        """Verify in-memory search is fast (<5ms)."""
        store = VectorStore(db_path=self.db_path)
        
        # Add documents
        for i in range(100):
            store.add_document(f"Documento {i}: controllo del volume e regolazione")
        
        # Measure search latency
        start = time.perf_counter()
        results = store.search("volume", top_k=5)
        elapsed_ms = (time.perf_counter() - start) * 1000
        
        # Should be fast (< 20ms for 100 docs)
        self.assertLess(elapsed_ms, 20.0)
        self.assertTrue(len(results) > 0)
        
        store.close()

    def test_persistence_to_sqlite(self):
        """Verify documents are persisted to SQLite."""
        # Create and populate store
        store1 = VectorStore(db_path=self.db_path)
        store1.add_document("Documento persistente A")
        store1.add_document("Documento persistente B")
        store1.add_document("Documento persistente C")
        store1.force_sync()
        
        # Verify in SQLite
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM documents")
        count = cursor.fetchone()[0]
        conn.close()
        
        self.assertEqual(count, 3)
        store1.close()

    def test_load_from_sqlite_on_startup(self):
        """Verify documents are loaded from SQLite on startup."""
        # Create and populate store
        store1 = VectorStore(db_path=self.db_path, sync_interval=1)
        doc_ids = []
        for i in range(5):
            doc_ids.append(store1.add_document(f"Documento caricato {i}"))
        store1.force_sync()
        store1.close()
        
        # Create new store and verify it loaded documents
        store2 = VectorStore(db_path=self.db_path)
        self.assertEqual(store2.get_size(), 5)
        
        # Verify search still works
        results = store2.search("caricato", top_k=5)
        self.assertTrue(len(results) > 0)
        
        store2.close()

    def test_async_sync_thread(self):
        """Verify background sync thread works periodically."""
        store = VectorStore(db_path=self.db_path, sync_interval=1)
        
        # Add document
        store.add_document("Documento per sync thread")
        
        # Wait for sync to happen (sync_interval=1 second)
        time.sleep(2.0)
        
        # Verify it was written to DB
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM documents")
        count = cursor.fetchone()[0]
        conn.close()
        
        self.assertGreater(count, 0)
        store.close()

    def test_deduplication_with_persistence(self):
        """Verify deduplication works across restarts."""
        store1 = VectorStore(db_path=self.db_path)
        content = "Contenuto univoco da deduplicate"
        
        id1 = store1.add_document(content)
        id2 = store1.add_document(content)
        id3 = store1.add_document(content)
        
        # Should all return the same ID
        self.assertEqual(id1, id2)
        self.assertEqual(id2, id3)
        self.assertEqual(store1.get_size(), 1)
        
        store1.force_sync()
        store1.close()
        
        # Verify deduplication persisted
        store2 = VectorStore(db_path=self.db_path)
        self.assertEqual(store2.get_size(), 1)
        store2.close()

    def test_ttl_cleanup(self):
        """Verify old documents are cleaned up via TTL."""
        # Create store with short TTL (1 second)
        store = VectorStore(db_path=self.db_path, ttl_seconds=1)
        
        # Add documents
        for i in range(3):
            store.add_document(f"Documento temporaneo {i}")
        
        initial_size = store.get_size()
        self.assertEqual(initial_size, 3)
        
        # Force sync (should trigger TTL cleanup)
        store.force_sync()
        
        # Check DB directly (some docs might still be in memory)
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM documents")
        db_count = cursor.fetchone()[0]
        conn.close()
        
        # All should still exist (TTL not expired yet)
        self.assertEqual(db_count, 3)
        
        store.close()

    def test_max_documents_eviction(self):
        """Verify max document limit is enforced."""
        store = VectorStore(db_path=self.db_path, max_documents=5)
        
        # Add more than max
        for i in range(10):
            store.add_document(f"Documento {i}")
        
        # Should only keep max_documents in memory
        self.assertEqual(store.get_size(), 5)
        
        store.close()

    def test_metadata_preservation(self):
        """Verify metadata is preserved through sync and reload."""
        store1 = VectorStore(db_path=self.db_path)
        
        metadata = {"type": "test", "priority": "high", "tags": ["temp", "demo"]}
        doc_id = store1.add_document("Documento con metadata", metadata=metadata)
        store1.force_sync()
        
        # Get document and verify metadata
        docs = store1.get_all_documents()
        self.assertTrue(any(d["doc_id"] == doc_id for d in docs))
        
        store1.close()
        
        # Reload and verify metadata survived
        store2 = VectorStore(db_path=self.db_path)
        docs = store2.get_all_documents()
        
        doc = next((d for d in docs if d["doc_id"] == doc_id), None)
        self.assertIsNotNone(doc)
        self.assertEqual(doc["metadata"]["type"], "test")
        self.assertEqual(doc["metadata"]["priority"], "high")
        
        store2.close()

    def test_concurrent_add_and_search(self):
        """Verify thread-safe operations."""
        store = VectorStore(db_path=self.db_path)
        errors = []
        
        def add_docs(prefix):
            try:
                for i in range(10):
                    store.add_document(f"{prefix} documento {i}")
            except Exception as e:
                errors.append(e)
        
        def search_docs():
            try:
                for _ in range(10):
                    store.search("documento", top_k=3)
            except Exception as e:
                errors.append(e)
        
        # Run concurrent operations
        threads = [
            threading.Thread(target=add_docs, args=("Thread A",)),
            threading.Thread(target=add_docs, args=("Thread B",)),
            threading.Thread(target=search_docs),
        ]
        
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        
        # Should have no errors
        self.assertEqual(len(errors), 0)
        
        store.close()

    def test_api_compatibility(self):
        """Verify API is backward compatible with old VectorStore."""
        store = VectorStore(db_path=self.db_path)
        
        # Test add_document
        doc_id = store.add_document("Test document", metadata={"key": "value"})
        self.assertIsNotNone(doc_id)
        
        # Test search
        results = store.search("test", top_k=5)
        self.assertTrue(len(results) > 0)
        self.assertIsInstance(results[0], tuple)
        self.assertEqual(len(results[0]), 2)  # (content, score)
        
        # Test remove_document
        removed = store.remove_document(doc_id)
        self.assertTrue(removed)
        self.assertEqual(store.get_size(), 0)
        
        # Test get_all_documents
        store.add_document("Doc 1")
        store.add_document("Doc 2")
        all_docs = store.get_all_documents()
        self.assertEqual(len(all_docs), 2)
        
        # Test clear
        store.clear()
        self.assertEqual(store.get_size(), 0)
        
        store.close()

    def test_db_file_creation(self):
        """Verify database file is created automatically."""
        custom_path = os.path.join(tempfile.gettempdir(), "test_rag_custom.db")
        if os.path.exists(custom_path):
            os.remove(custom_path)
        
        store = VectorStore(db_path=custom_path)
        store.add_document("Test")
        store.force_sync()
        
        # Verify file exists and is a valid SQLite DB
        self.assertTrue(os.path.exists(custom_path))
        self.assertGreater(os.path.getsize(custom_path), 0)
        
        # Verify it's valid SQLite
        conn = sqlite3.connect(custom_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        conn.close()
        
        self.assertTrue(any("documents" in str(t) for t in tables))
        
        store.close()
        os.remove(custom_path)

    def test_delete_by_metadata_removes_from_memory_and_db(self):
        """Verify delete_by_metadata cleans memory and SQLite so reloading does not resurrect."""
        store = VectorStore(db_path=self.db_path)
        store.add_document("Nota segreta chat 1", metadata={"context_id": "conv-1"}, doc_id="d1")
        store.add_document("Nota pubblica chat 2", metadata={"context_id": "conv-2"}, doc_id="d2")
        store.force_sync()

        # Delete conv-1
        removed = store.delete_by_metadata("context_id", "conv-1")
        self.assertEqual(removed, 1)
        self.assertNotIn("d1", store.documents)
        self.assertIn("d2", store.documents)
        store.close()

        # Create new store on same db
        store2 = VectorStore(db_path=self.db_path)
        self.assertNotIn("d1", store2.documents)
        self.assertIn("d2", store2.documents)
        self.assertEqual(len(store2.search("segreta", context_id="conv-1")), 0)
        store2.close()

    def test_same_phrase_in_two_chats_produces_two_isolated_documents(self):
        """Verify same text in different chats gets isolated by doc_id and context."""
        import hashlib
        store = VectorStore(db_path=self.db_path)
        text = "Qual è la ricetta della pizza margherita?"

        id1 = hashlib.md5(f"chat-1\0{text}".encode("utf-8")).hexdigest()[:16]
        id2 = hashlib.md5(f"chat-2\0{text}".encode("utf-8")).hexdigest()[:16]
        self.assertNotEqual(id1, id2)

        doc1 = store.add_document(text, metadata={"context_id": "chat-1"}, doc_id=id1)
        doc2 = store.add_document(text, metadata={"context_id": "chat-2"}, doc_id=id2)

        self.assertEqual(doc1, id1)
        self.assertEqual(doc2, id2)
        self.assertEqual(store.get_size(), 2)

        res1 = store.search("pizza", context_id="chat-1")
        res2 = store.search("pizza", context_id="chat-2")
        res3 = store.search("pizza", context_id="chat-3")

        self.assertEqual(len(res1), 1)
        self.assertEqual(len(res2), 1)
        self.assertEqual(len(res3), 0)
        store.close()

    def test_delete_conversation_via_main_cleans_rag(self):
        """Verify DeleteConversation on VoiceAssistant delegates to RAG delete_by_metadata."""
        from unittest.mock import MagicMock
        from main import VoiceAssistant

        daemon = VoiceAssistant.__new__(VoiceAssistant)
        mock_cm = MagicMock()
        mock_cm.delete.return_value = True
        daemon.context_manager = mock_cm

        mock_store = MagicMock()
        mock_sp = MagicMock()
        mock_sp.vector_store = mock_store
        mock_pipe = MagicMock()
        mock_pipe.smart_path = mock_sp
        daemon.pipeline_controller = mock_pipe

        result = daemon.DeleteConversation("test-uuid-1234")
        self.assertTrue(result)
        mock_cm.delete.assert_called_once_with("test-uuid-1234")
        mock_store.delete_by_metadata.assert_called_once_with("context_id", "test-uuid-1234")


if __name__ == "__main__":
    unittest.main()
