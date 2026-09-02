import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

daemon_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "daemon"))
if daemon_dir not in sys.path:
    sys.path.insert(0, daemon_dir)

from skills.skill_registry import SkillRegistry
from core.assistant_runtime import AssistantRuntimeController


class TestSkillMarkdownLoader(unittest.TestCase):
    def test_markdown_skill_files_are_parsed_into_registry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            markdown = Path(tmpdir) / "system_control.md"
            markdown.write_text(
                """---
name: \"System Control\"
description: \"Controls volume and theme\" 
triggers:
  - \"alza il volume\"
  - \"metti il tema scuro\"
tools_allowed:
  - \"system_volume\"
  - \"dark_mode\"
---

# System Control
Use volume and theme tools.
""",
                encoding="utf-8",
            )

            registry = SkillRegistry.from_directory(Path(tmpdir))
            self.assertEqual(len(registry.skills), 1)
            self.assertEqual(registry.skills[0]["name"], "System Control")
            self.assertIn("alza il volume", registry.skills[0]["triggers"])
            self.assertIn("system_volume", registry.skills[0]["tools_allowed"])

    def test_default_directory_supports_json_and_markdown(self):
        registry = SkillRegistry.from_default_directory()
        self.assertTrue(any(skill.get("intent") == "volume_up" for skill in registry.skills))

    def test_user_skill_directory_is_loaded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            home_dir = Path(tmpdir) / "home"
            config_dir = home_dir / ".config" / "voice-assistant" / "skills"
            config_dir.mkdir(parents=True)
            skill_path = config_dir / "custom_skill.md"
            skill_path.write_text(
                """---
name: \"Custom Skill\"
triggers:
  - \"apri la cartella\"
  - \"mostra i file\"
---

# Custom Skill
""",
                encoding="utf-8",
            )

            with patch("pathlib.Path.home", return_value=home_dir):
                registry = SkillRegistry.from_default_directory(Path(tmpdir) / "unused")

            self.assertTrue(any(skill.get("name") == "Custom Skill" for skill in registry.skills))

    def test_runtime_executes_markdown_skill_tool_mapping(self):
        calls = []

        class FakeMCPManager:
            def execute_tool(self, tool_name, params):
                calls.append((tool_name, params))
                return {"tool": tool_name, "params": params, "ok": True}

        owner = type("Owner", (), {"mcp_manager": FakeMCPManager()})()
        runtime = AssistantRuntimeController(owner)

        ok, result = runtime._handle_fast_path_intent("system_control", {"action": "volume_up"})

        self.assertTrue(ok)
        self.assertEqual(calls[0][0], "system_volume")
        self.assertEqual(calls[0][1]["action"], "increase")
        self.assertTrue(result["ok"])

    def test_runtime_executes_theme_markdown_skill(self):
        calls = []

        class FakeMCPManager:
            def execute_tool(self, tool_name, params):
                calls.append((tool_name, params))
                return {"tool": tool_name, "params": params, "ok": True}

        owner = type("Owner", (), {"mcp_manager": FakeMCPManager()})()
        runtime = AssistantRuntimeController(owner)

        ok, result = runtime._handle_fast_path_intent("theme_control", {"mode": "dark"})

        self.assertTrue(ok)
        self.assertEqual(calls[0][0], "dark_mode")
        self.assertEqual(calls[0][1]["mode"], "dark")
        self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()
