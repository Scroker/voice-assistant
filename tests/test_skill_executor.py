import os
import sys
import unittest
from unittest.mock import MagicMock, patch

daemon_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "daemon"))
if daemon_dir not in sys.path:
    sys.path.insert(0, daemon_dir)

from skills.skill_executor import SkillExecutor
from skills.skill_registry import SkillRegistry


class TestSkillExecutor(unittest.TestCase):
    def test_executor_detects_volume_action_from_text(self):
        skill = {
            "intent": "system_control",
            "name": "System Control",
            "description": "Controls volume, theme, and app launch actions",
            "triggers": ["alza il volume"],
            "tools_allowed": ["system_volume"],
            "_body": "Use native desktop tools for volume commands.",
        }
        executor = SkillExecutor(skill)

        # Test volume increase detection
        action, params = executor._infer_action_from_text("alza il volume", "system_volume")
        self.assertEqual(action, "increase")
        self.assertEqual(params["action"], "increase")

    def test_executor_detects_theme_action_from_text(self):
        skill = {
            "intent": "theme_control",
            "name": "Theme Control",
            "description": "Switches between light and dark theme",
            "triggers": ["tema scuro"],
            "tools_allowed": ["dark_mode"],
            "_body": "Switch the system theme appropriately.",
        }
        executor = SkillExecutor(skill)

        # Test dark theme detection
        action, params = executor._infer_action_from_text("metti il tema scuro", "dark_mode")
        self.assertEqual(action, "dark")
        self.assertEqual(params["mode"], "dark")

        # Test light theme detection
        action, params = executor._infer_action_from_text("attiva il tema chiaro", "dark_mode")
        self.assertEqual(action, "light")
        self.assertEqual(params["mode"], "light")

    def test_executor_generates_standardized_responses(self):
        skill = {
            "intent": "system_control",
            "name": "System Control",
            "triggers": [],
            "tools_allowed": ["system_volume"],
            "_body": "Control volume.",
        }
        executor = SkillExecutor(skill)

        response = executor._generate_response("system_volume", "increase", {})
        self.assertEqual(response, "Volume alzato.")

        response = executor._generate_response("dark_mode", "dark", {})
        self.assertEqual(response, "Tema scuro attivato.")

    def test_executor_executes_skill_with_mcp_manager(self):
        skill = {
            "intent": "system_control",
            "name": "System Control",
            "triggers": [],
            "tools_allowed": ["system_volume"],
            "_body": "Control volume.",
        }
        executor = SkillExecutor(skill)

        mock_mcp = MagicMock()
        mock_mcp.execute_tool.return_value = {"ok": True}

        success, response, result = executor.execute(
            "alza il volume", mcp_manager=mock_mcp
        )

        self.assertTrue(success)
        self.assertIsNotNone(response)
        mock_mcp.execute_tool.assert_called_once()
        call_args = mock_mcp.execute_tool.call_args
        self.assertEqual(call_args[0][0], "system_volume")
        self.assertEqual(call_args[0][1]["action"], "increase")

    def test_executor_detects_tools_from_body_keywords(self):
        skill = {
            "intent": "generic_skill",
            "name": "Generic",
            "triggers": [],
            "tools_allowed": [],
            "_body": "This skill controls volume and theme settings.",
        }
        executor = SkillExecutor(skill)

        detected_tools = executor._extract_tool_keywords_from_body()
        self.assertIn("system_volume", detected_tools)
        self.assertIn("dark_mode", detected_tools)

    def test_executor_falls_back_to_llm_when_no_tools_match(self):
        skill = {
            "intent": "complex_skill",
            "name": "Complex",
            "triggers": [],
            "tools_allowed": [],
            "_body": "This is a complex skill that needs LLM.",
        }
        executor = SkillExecutor(skill)

        mock_mcp = MagicMock()
        mock_llm = MagicMock(return_value="Compito completato dall'LLM.")

        success, response, result = executor.execute(
            "fai qualcosa di complesso", mcp_manager=mock_mcp, llm_fallback=mock_llm
        )

        self.assertTrue(success)
        self.assertIn("Compito", response)
        mock_llm.assert_called_once()

    def test_executor_handles_volume_set_with_number(self):
        skill = {
            "intent": "system_control",
            "name": "System Control",
            "triggers": [],
            "tools_allowed": ["system_volume"],
            "_body": "Control volume.",
        }
        executor = SkillExecutor(skill)

        action, params = executor._infer_action_from_text("imposta volume a 75", "system_volume")
        self.assertEqual(action, "set")
        self.assertEqual(params["level"], 75)

    def test_executor_handles_app_launch(self):
        skill = {
            "intent": "system_control",
            "name": "System Control",
            "triggers": [],
            "tools_allowed": ["app_launcher"],
            "_body": "Launch applications.",
        }
        executor = SkillExecutor(skill)

        action, params = executor._infer_action_from_text("apri firefox", "app_launcher")
        self.assertEqual(action, "launch")
        self.assertEqual(params["app_name"], "firefox")

    def test_executor_returns_false_on_empty_input(self):
        skill = {
            "intent": "test",
            "name": "Test",
            "triggers": [],
            "tools_allowed": ["system_volume"],
            "_body": "",
        }
        executor = SkillExecutor(skill)

        success, response, result = executor.execute("", mcp_manager=MagicMock())
        self.assertFalse(success)
        self.assertIn("vuoto", response.lower())


if __name__ == "__main__":
    unittest.main()
