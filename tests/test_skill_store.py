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
from skills import skill_store


class TestSkillStore(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._home_patch = patch("pathlib.Path.home", return_value=Path(self._tmpdir.name))
        self._home_patch.start()

    def tearDown(self):
        self._home_patch.stop()
        self._tmpdir.cleanup()

    def test_save_marks_skill_as_custom_and_reloadable(self):
        skill = {
            "name": "Test Skill",
            "intent": "test_custom_intent",
            "tool": "quick_settings",
            "args": {"setting": "wifi", "enabled": True},
            "triggers": ["prova skill personalizzata", "attiva la prova"],
        }
        path = skill_store.save_user_skill(skill)
        self.assertTrue(path.exists())

        all_skills = skill_store.list_all_skills()
        custom = [s for s in all_skills if s["intent"] == "test_custom_intent"]
        self.assertEqual(len(custom), 1)
        self.assertTrue(custom[0]["is_custom"])
        self.assertEqual(custom[0]["triggers"], skill["triggers"])
        self.assertEqual(custom[0]["args"], skill["args"])

    def test_built_in_skills_are_not_marked_custom(self):
        all_skills = skill_store.list_all_skills()
        built_in = [s for s in all_skills if s["intent"] == "volume_up"]
        self.assertEqual(len(built_in), 1)
        self.assertFalse(built_in[0]["is_custom"])

    def test_save_requires_intent(self):
        with self.assertRaises(ValueError):
            skill_store.save_user_skill({"triggers": ["qualcosa"]})

    def test_save_requires_at_least_one_trigger(self):
        with self.assertRaises(ValueError):
            skill_store.save_user_skill({"intent": "no_triggers", "triggers": []})

    def test_delete_removes_custom_skill(self):
        skill_store.save_user_skill({"intent": "to_delete", "triggers": ["elimina questa skill"]})
        self.assertTrue(skill_store.delete_user_skill("to_delete"))
        self.assertFalse(skill_store.delete_user_skill("to_delete"))

    def test_delete_nonexistent_skill_returns_false(self):
        self.assertFalse(skill_store.delete_user_skill("never_existed"))

    def test_saved_skill_roundtrips_through_registry_parser(self):
        skill_store.save_user_skill({
            "intent": "roundtrip_intent",
            "name": 'A "quoted" name',
            "tool": "quick_settings",
            "args": {"setting": "bluetooth", "enabled": False},
            "triggers": ["frase con apostrofo l'assistente", "seconda frase"],
        })
        registry = SkillRegistry.from_default_directory()
        skill = registry.find_by_intent("roundtrip_intent")
        self.assertIsNotNone(skill)
        self.assertEqual(skill["tool"], "quick_settings")
        self.assertEqual(skill["args"], {"setting": "bluetooth", "enabled": False})
        self.assertIn("frase con apostrofo l'assistente", skill["triggers"])

    def test_save_command_skill_roundtrip(self):
        skill_store.save_user_skill({
            "intent": "cmd_intent",
            "name": "Esegui Script",
            "action_type": "command",
            "command": "sh -c 'echo 42'",
            "triggers": ["avvia script"],
        })
        registry = SkillRegistry.from_default_directory()
        skill = registry.find_by_intent("cmd_intent")
        self.assertIsNotNone(skill)
        self.assertEqual(skill["action_type"], "command")
        self.assertEqual(skill["command"], "sh -c 'echo 42'")

    def test_save_response_skill_roundtrip(self):
        skill_store.save_user_skill({
            "intent": "resp_intent",
            "name": "Password Wifi",
            "action_type": "response",
            "response": "La password è 987654321",
            "triggers": ["qual è la password"],
        })
        registry = SkillRegistry.from_default_directory()
        skill = registry.find_by_intent("resp_intent")
        self.assertIsNotNone(skill)
        self.assertEqual(skill["action_type"], "response")
        self.assertEqual(skill["response"], "La password è 987654321")

    def test_save_prompt_skill_roundtrip(self):
        skill_store.save_user_skill({
            "intent": "prompt_intent",
            "name": "Traduttore",
            "action_type": "prompt",
            "prompt": "Traduci in francese ogni messaggio",
            "triggers": ["traduci in francese"],
        })
        registry = SkillRegistry.from_default_directory()
        skill = registry.find_by_intent("prompt_intent")
        self.assertIsNotNone(skill)
        self.assertEqual(skill["action_type"], "prompt")
        self.assertEqual(skill["prompt"], "Traduci in francese ogni messaggio")


if __name__ == "__main__":
    unittest.main()
