import os
import sys
import unittest

daemon_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "daemon"))
if daemon_dir not in sys.path:
    sys.path.insert(0, daemon_dir)

from services.memory_manager import ConversationMemory
from services.rag_store import VectorStore
from services.prompt_builder import PromptBuilder
from services.tool_call_parser import ToolCallParser, ToolCall


class TestConversationMemory(unittest.TestCase):
    def test_add_and_retrieve_messages(self):
        memory = ConversationMemory()
        memory.add_user_message("Ciao")
        memory.add_assistant_message("Ciao! Come stai?")

        messages = memory.get_recent_messages(2)
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0].role, "user")
        self.assertEqual(messages[1].role, "assistant")

    def test_context_window_format(self):
        memory = ConversationMemory()
        memory.add_user_message("Alza il volume")
        memory.add_assistant_message("Volume alzato")

        context = memory.get_context_window()
        self.assertEqual(len(context), 2)
        self.assertEqual(context[0]["role"], "user")
        self.assertEqual(context[1]["role"], "assistant")

    def test_max_messages_limit(self):
        memory = ConversationMemory(max_messages=5)
        for i in range(10):
            memory.add_user_message(f"Message {i}")

        self.assertEqual(len(memory.get_recent_messages()), 5)

    def test_memory_summary(self):
        memory = ConversationMemory()
        memory.add_user_message("Che ore sono?")
        memory.add_assistant_message("Sono le 15:30")

        summary = memory.get_summary()
        self.assertIn("Utente", summary)
        self.assertIn("Assistente", summary)


class TestVectorStore(unittest.TestCase):
    def test_add_and_search_documents(self):
        store = VectorStore()
        doc_id = store.add_document("Il volume è impostato a 50%")
        store.add_document("Il tema scuro è attivato")

        results = store.search("volume", top_k=1)
        self.assertTrue(len(results) > 0)
        self.assertIn("volume", results[0][0].lower())

    def test_deduplication(self):
        store = VectorStore()
        content = "Questo è un documento di prova"
        id1 = store.add_document(content)
        id2 = store.add_document(content)

        self.assertEqual(id1, id2)
        self.assertEqual(store.get_size(), 1)

    def test_search_with_similarity_threshold(self):
        store = VectorStore()
        store.add_document("Controllo volume")
        store.add_document("Tema scuro")

        results = store.search("volume", min_score=0.5)
        self.assertTrue(len(results) > 0)

    def test_max_documents_eviction(self):
        store = VectorStore(max_documents=3)
        store.add_document("Doc 1")
        store.add_document("Doc 2")
        store.add_document("Doc 3")
        store.add_document("Doc 4")

        self.assertEqual(store.get_size(), 3)


class TestPromptBuilder(unittest.TestCase):
    def test_build_system_prompt(self):
        builder = PromptBuilder()
        prompt = builder.build_prompt("Alza il volume")

        self.assertIn("assistente vocale", prompt.lower())
        self.assertIn("sistema", prompt.lower())

    def test_inject_rag_context(self):
        builder = PromptBuilder(include_rag_context=True)
        rag_results = [("Il volume è impostato a 50%", 0.85)]

        prompt = builder.build_prompt("Che volume c'è?", rag_results=rag_results)
        self.assertIn("Memoria rilevante", prompt)

    def test_inject_chat_history(self):
        builder = PromptBuilder(include_chat_history=True)
        history = [
            {"role": "user", "content": "Ciao"},
            {"role": "assistant", "content": "Ciao!"},
        ]

        prompt = builder.build_prompt("Come stai?", chat_history=history)
        self.assertIn("Conversazione recente", prompt)

    def test_build_conversation_messages(self):
        builder = PromptBuilder()
        messages = builder.build_conversation_messages("Test message")

        self.assertGreater(len(messages), 0)
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[-1]["role"], "user")


class TestToolCallParser(unittest.TestCase):
    def test_parse_simple_tool_call(self):
        parser = ToolCallParser()
        text = 'Eseguo il comando: {"tool": "system_volume", "args": {"action": "increase", "level": 10}}'

        tool_call, remaining = parser.parse(text)
        self.assertIsNotNone(tool_call)
        self.assertEqual(tool_call.tool_name, "system_volume")
        self.assertEqual(tool_call.args["action"], "increase")

    def test_parse_multiple_tool_calls(self):
        parser = ToolCallParser()
        text = (
            'Primo: {"tool": "system_volume", "args": {"action": "increase"}} '
            'Secondo: {"tool": "dark_mode", "args": {"action": "set", "mode": "dark"}}'
        )

        tool_calls, remaining = parser.parse_all(text)
        self.assertEqual(len(tool_calls), 2)
        self.assertEqual(tool_calls[0].tool_name, "system_volume")
        self.assertEqual(tool_calls[1].tool_name, "dark_mode")

    def test_extract_text_response(self):
        parser = ToolCallParser()
        text = (
            'Aumento il volume. {"tool": "system_volume", "args": {"action": "increase"}} '
            'Fatto!'
        )

        response = parser.extract_text_response(text)
        self.assertNotIn("tool", response.lower())
        self.assertIn("Aumento", response)

    def test_validate_tool_args(self):
        parser = ToolCallParser()

        valid = parser.validate_args("system_volume", {"action": "set", "level": 50})
        self.assertTrue(valid)

        invalid = parser.validate_args("system_volume", {"action": "set", "level": 150})
        self.assertFalse(invalid)

    def test_unknown_tool_rejected(self):
        parser = ToolCallParser()
        text = '{"tool": "unknown_tool", "args": {}}'

        tool_call, _ = parser.parse(text)
        self.assertIsNone(tool_call)

    def test_tool_call_from_json_code_block(self):
        parser = ToolCallParser()
        text = """
Ecco il comando:
```json
{"tool": "dark_mode", "args": {"action": "set", "mode": "dark"}}
```
"""
        tool_call, remaining = parser.parse(text)
        self.assertIsNotNone(tool_call)
        self.assertEqual(tool_call.tool_name, "dark_mode")


if __name__ == "__main__":
    unittest.main()
