import os
import sys
import unittest
from unittest.mock import MagicMock

daemon_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "daemon"))
if daemon_dir not in sys.path:
    sys.path.insert(0, daemon_dir)

from core.smart_path_controller import SmartPathController


class TestSmartPathController(unittest.TestCase):
    def test_add_user_message_records_in_memory_and_rag(self):
        controller = SmartPathController()
        controller.add_user_message("Alza il volume")

        messages = controller.memory.get_recent_messages(1)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].role, "user")

        # Check RAG
        results = controller.vector_store.search("volume")
        self.assertTrue(len(results) > 0)

    def test_add_assistant_message_records_in_memory_and_rag(self):
        controller = SmartPathController()
        controller.add_assistant_message("Volume alzato.")

        messages = controller.memory.get_recent_messages(1)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].role, "assistant")

    def test_build_smart_prompt_includes_rag_context(self):
        controller = SmartPathController()
        controller.add_user_message("Ho alzato il volume prima")
        controller.add_assistant_message("Capito")

        messages = controller.build_smart_prompt("Alza il volume di più", use_rag=True)

        # Should include system, history, and user
        self.assertGreater(len(messages), 1)
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[-1]["role"], "user")

    def test_parse_llm_response_extracts_tool_calls(self):
        controller = SmartPathController()
        response = (
            "Aumento il volume ora. "
            '{"tool": "system_volume", "args": {"action": "increase", "level": 10}}'
        )

        tool_calls, text = controller.parse_llm_response(response)

        self.assertEqual(len(tool_calls), 1)
        self.assertEqual(tool_calls[0].tool_name, "system_volume")
        self.assertIn("Aumento", text)
        self.assertNotIn("tool", text.lower())

    def test_parse_llm_response_validates_args(self):
        controller = SmartPathController()
        response = (
            '{"tool": "system_volume", "args": {"action": "set", "level": 150}}'
        )

        tool_calls, _ = controller.parse_llm_response(response)

        # Should be filtered out due to invalid level
        self.assertEqual(len(tool_calls), 0)

    def test_execute_smart_path_full_flow(self):
        controller = SmartPathController()

        mock_llm = MagicMock(
            return_value=[
                "Aumento il volume. ",
                '{"tool": "system_volume", "args": {"action": "increase", "level": 10}}',
            ]
        )
        mock_mcp = MagicMock()
        mock_mcp.execute_tool.return_value = {"ok": True}

        success, response, result = controller.execute_smart_path(
            "Alza il volume",
            llm_streamer=mock_llm,
            mcp_manager=mock_mcp,
        )

        self.assertTrue(success)
        self.assertIsNotNone(response)
        mock_mcp.execute_tool.assert_called_once()

    def test_execute_smart_path_without_llm_fails(self):
        controller = SmartPathController()

        success, response, result = controller.execute_smart_path(
            "Alza il volume", llm_streamer=None
        )

        self.assertFalse(success)

    def test_conversation_memory_accumulates(self):
        controller = SmartPathController()

        controller.add_user_message("Messaggio 1")
        controller.add_assistant_message("Risposta 1")
        controller.add_user_message("Messaggio 2")
        controller.add_assistant_message("Risposta 2")

        summary = controller.get_conversation_summary()
        self.assertIn("Messaggio", summary)
        self.assertIn("Risposta", summary)

    def test_get_stats_returns_metrics(self):
        controller = SmartPathController()
        controller.add_user_message("Test message")
        controller.add_assistant_message("Test response")

        stats = controller.get_stats()

        self.assertIn("memory_messages", stats)
        self.assertIn("rag_documents", stats)
        self.assertIn("available_skills", stats)
        self.assertGreater(stats["memory_messages"], 0)
        self.assertGreater(stats["rag_documents"], 0)

    def test_clear_memory_resets_history(self):
        controller = SmartPathController()
        controller.add_user_message("Message 1")
        controller.add_assistant_message("Response 1")

        self.assertGreater(len(controller.memory.messages), 0)

        controller.clear_memory()

        self.assertEqual(len(controller.memory.messages), 0)


if __name__ == "__main__":
    unittest.main()
