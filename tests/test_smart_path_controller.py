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
        self.addCleanup(controller.vector_store.close)
        controller.add_user_message("Alza il volume")

        messages = controller.memory.get_recent_messages(1)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].role, "user")

        # Check RAG
        results = controller.vector_store.search("volume")
        self.assertTrue(len(results) > 0)

    def test_add_assistant_message_records_in_memory_and_rag(self):
        controller = SmartPathController()
        self.addCleanup(controller.vector_store.close)
        controller.add_assistant_message("Volume alzato.")

        messages = controller.memory.get_recent_messages(1)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].role, "assistant")

    def test_build_smart_prompt_includes_rag_context(self):
        controller = SmartPathController()
        self.addCleanup(controller.vector_store.close)
        controller.add_user_message("Ho alzato il volume prima")
        controller.add_assistant_message("Capito")

        messages = controller.build_smart_prompt("Alza il volume di più", use_rag=True)

        # Should include system, history, and user
        self.assertGreater(len(messages), 1)
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[-1]["role"], "user")

    def test_parse_llm_response_extracts_tool_calls(self):
        controller = SmartPathController()
        self.addCleanup(controller.vector_store.close)
        response = (
            "Aumento il volume ora. "
            '{"tool": "set_volume", "args": {"direction": "up", "volume": 10.0}}'
        )

        tool_calls, text = controller.parse_llm_response(response)

        self.assertEqual(len(tool_calls), 1)
        self.assertEqual(tool_calls[0].tool_name, "set_volume")
        self.assertIn("Aumento", text)
        self.assertNotIn("tool", text.lower())

    def test_parse_llm_response_validates_args(self):
        controller = SmartPathController()
        self.addCleanup(controller.vector_store.close)
        response = (
            '{"tool": "set_volume", "args": {"volume": 150}}'
        )

        tool_calls, _ = controller.parse_llm_response(response)

        # Should be filtered out due to invalid level
        self.assertEqual(len(tool_calls), 0)

    def test_execute_smart_path_full_flow(self):
        controller = SmartPathController()
        self.addCleanup(controller.vector_store.close)

        mock_llm = MagicMock(
            return_value=[
                "Aumento il volume. ",
                '{"tool": "set_volume", "args": {"direction": "up", "volume": 10.0}}',
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

    def test_execute_smart_path_streams_visible_tokens_and_hides_tool_json(self):
        controller = SmartPathController()
        self.addCleanup(controller.vector_store.close)
        visible_tokens = []
        spoken_sentences = []

        mock_llm = MagicMock(
            return_value=[
                "Aumento il volume. ",
                '{"tool": "set_volume", "args": {"direction": "up", "volume": 10.0}}',
            ]
        )
        mock_mcp = MagicMock()
        mock_mcp.execute_tool.return_value = "Volume alzato."

        success, response, result = controller.execute_smart_path(
            "Alza il volume",
            llm_streamer=mock_llm,
            mcp_manager=mock_mcp,
            token_callback=visible_tokens.append,
            sentence_callback=spoken_sentences.append,
        )

        visible = "".join(visible_tokens)
        self.assertTrue(success)
        self.assertEqual(result, "Volume alzato.")
        self.assertIn("Aumento il volume.", response)
        self.assertIn("Volume alzato.", visible)
        self.assertNotIn('"tool"', visible)
        self.assertIn("Aumento il volume.", spoken_sentences)
        self.assertIn("Volume alzato.", spoken_sentences)
        mock_mcp.execute_tool.assert_called_once_with(
            "set_volume", {"direction": "up", "volume": 10.0}
        )

    def test_parse_llm_response_accepts_actual_quick_settings_schema(self):
        controller = SmartPathController()
        self.addCleanup(controller.vector_store.close)
        response = '{"tool": "quick_settings", "args": {"setting": "dark_style", "enabled": true}}'

        tool_calls, _ = controller.parse_llm_response(response)

        self.assertEqual(len(tool_calls), 1)
        self.assertEqual(tool_calls[0].tool_name, "quick_settings")

    def test_execute_smart_path_without_llm_fails(self):
        controller = SmartPathController()
        self.addCleanup(controller.vector_store.close)

        success, response, result = controller.execute_smart_path(
            "Alza il volume", llm_streamer=None
        )

        self.assertFalse(success)

    def test_conversation_memory_accumulates(self):
        controller = SmartPathController()
        self.addCleanup(controller.vector_store.close)

        controller.add_user_message("Messaggio 1")
        controller.add_assistant_message("Risposta 1")
        controller.add_user_message("Messaggio 2")
        controller.add_assistant_message("Risposta 2")

        summary = controller.get_conversation_summary()
        self.assertIn("Messaggio", summary)
        self.assertIn("Risposta", summary)

    def test_get_stats_returns_metrics(self):
        controller = SmartPathController()
        self.addCleanup(controller.vector_store.close)
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
        self.addCleanup(controller.vector_store.close)
        controller.add_user_message("Message 1")
        controller.add_assistant_message("Response 1")

        self.assertGreater(len(controller.memory.messages), 0)

        controller.clear_memory()

        self.assertEqual(len(controller.memory.messages), 0)

    def test_memory_enabled_toggle_clears_memory_when_disabled(self):
        controller = SmartPathController(memory_enabled=True)
        self.addCleanup(controller.vector_store.close)
        controller.add_user_message("Message 1")
        controller.add_assistant_message("Response 1")
        self.assertEqual(len(controller.memory.messages), 2)

        controller.memory_enabled = False
        self.assertEqual(len(controller.memory.messages), 0)
        self.assertFalse(controller.memory_enabled)

    def test_memory_disabled_does_not_record_or_inject_history(self):
        controller = SmartPathController(memory_enabled=False)
        self.addCleanup(controller.vector_store.close)
        controller.add_user_message("Message 1")
        controller.add_assistant_message("Response 1")
        self.assertEqual(len(controller.memory.messages), 0)

        messages = controller.build_smart_prompt("Message 2", use_history=True)
        # Should only contain system and user prompt, no previous history messages
        user_messages = [m for m in messages if m["role"] == "user"]
        self.assertEqual(len(user_messages), 1)
        self.assertEqual(user_messages[0]["content"], "Message 2")



if __name__ == "__main__":
    unittest.main()
