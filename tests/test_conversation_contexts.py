import os
import sys
import tempfile
import unittest

daemon_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "daemon"))
if daemon_dir not in sys.path:
    sys.path.insert(0, daemon_dir)

from services.conversation_contexts import ConversationContextManager, ConversationContext


class TestConversationContexts(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.manager = ConversationContextManager(base_dir=self.temp_dir)

    def test_path_traversal_protection(self):
        # Create a sentinel file outside the conversations directory
        sentinel_path = os.path.join(self.temp_dir, "victim.json")
        with open(sentinel_path, "w", encoding="utf-8") as f:
            f.write('{"victim": true}')

        # Attempt to delete using traversal IDs or invalid IDs
        self.assertFalse(self.manager.delete("../victim"))
        self.assertFalse(self.manager.delete("voice"))
        self.assertFalse(self.manager.delete("x/../../victim"))
        self.assertFalse(self.manager.delete("non-uuid"))
        self.assertFalse(self.manager.save("../victim"))
        self.assertFalse(self.manager.clear("../victim"))

        # Sentinel file must remain completely intact
        self.assertTrue(os.path.exists(sentinel_path))
        with open(sentinel_path, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), '{"victim": true}')

    def test_crud_and_reload(self):
        # Create
        cid = self.manager.create(title="Test Chat")
        self.assertTrue(self.manager.is_valid_chat_id(cid))
        ctx = self.manager.get(cid)
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx.title, "Test Chat")

        # Add message & Save
        ctx.add_message("user", "Ciao assistente")
        ctx.add_message("assistant", "Ciao! Come posso aiutarti?")
        self.assertTrue(self.manager.save(cid))

        # Reload with a new manager instance on the same directory
        new_manager = ConversationContextManager(base_dir=self.temp_dir)
        reloaded_ctx = new_manager.get(cid)
        self.assertIsNotNone(reloaded_ctx)
        self.assertEqual(len(reloaded_ctx.messages), 2)
        self.assertEqual(reloaded_ctx.messages[0]["content"], "Ciao assistente")

        # Delete
        self.assertTrue(new_manager.delete(cid))
        self.assertIsNone(new_manager.get(cid))
        # Second delete should return False as it no longer exists
        self.assertFalse(new_manager.delete(cid))

    def test_invalid_json_files_ignored_on_load(self):
        # Place a non-UUID JSON file in the conversations store
        invalid_file = os.path.join(self.manager.store_dir, "pippo.json")
        with open(invalid_file, "w", encoding="utf-8") as f:
            f.write('{"title": "Pippo"}')

        # Create another manager to trigger _load_all
        mgr2 = ConversationContextManager(base_dir=self.temp_dir)
        self.assertNotIn("pippo", mgr2.contexts)

    def test_clear_nonexistent_and_valid(self):
        # clear on non-existent or invalid should not raise and return False
        self.assertFalse(self.manager.clear("nonexistent"))
        self.assertFalse(self.manager.clear("12345678-1234-1234-1234-123456789012"))

        # clear on existing chat
        cid = self.manager.create("To Clear")
        ctx = self.manager.get(cid)
        ctx.add_message("user", "test")
        self.assertTrue(self.manager.clear(cid))
        self.assertEqual(len(ctx.messages), 0)


if __name__ == "__main__":
    unittest.main()
