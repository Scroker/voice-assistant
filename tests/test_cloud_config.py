import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

daemon_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "daemon"))
if daemon_dir not in sys.path:
    sys.path.insert(0, daemon_dir)

from core.cloud_config import CloudConfigManager, DEFAULT_CLOUD_PROVIDERS


class TestCloudConfig(unittest.TestCase):

    def test_default_file_creation_and_permissions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_file = Path(tmpdir) / "cloud_providers.json"
            mock_settings = MagicMock()
            mock_settings.get_string.return_value = ""
            mgr = CloudConfigManager(config_path=cfg_file, settings=mock_settings)
            data = mgr.load()

            self.assertTrue(cfg_file.exists())
            self.assertEqual(data["version"], 1)
            self.assertIn("openai", data["llm"])
            self.assertIn("deepseek", data["llm"])
            self.assertIn("anthropic", data["llm"])
            self.assertIn("ollama_cloud", data["llm"])
            self.assertIn("custom", data["llm"])
            self.assertIn("openai_cloud", data["stt"])
            self.assertIn("groq_cloud", data["stt"])
            self.assertIn("openai", data["tts"])

            # Check permissions (0600)
            file_stat = os.stat(cfg_file)
            permissions = stat.S_IMODE(file_stat.st_mode)
            self.assertEqual(permissions, 0o600)

    def test_get_and_set_provider_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_file = Path(tmpdir) / "cloud_providers.json"
            mock_settings = MagicMock()
            mock_settings.get_string.return_value = ""
            mgr = CloudConfigManager(config_path=cfg_file, settings=mock_settings)

            # Initially empty
            self.assertEqual(mgr.get_api_key("llm", "deepseek"), "")

            # Set DeepSeek key and custom model
            mgr.set_provider_config("llm", "deepseek", {
                "api_key": "sk-deepseek-test-key",
                "model": "deepseek-reasoner",
            })

            # Re-read through manager
            self.assertEqual(mgr.get_api_key("llm", "deepseek"), "sk-deepseek-test-key")
            self.assertEqual(mgr.get_model("llm", "deepseek"), "deepseek-reasoner")
            self.assertEqual(mgr.get_endpoint("llm", "deepseek"), "https://api.deepseek.com/v1/chat/completions")

            # Verify on-disk JSON
            with open(cfg_file, "r", encoding="utf-8") as f:
                disk_data = json.load(f)
            self.assertEqual(disk_data["llm"]["deepseek"]["api_key"], "sk-deepseek-test-key")
            self.assertEqual(disk_data["llm"]["deepseek"]["model"], "deepseek-reasoner")

    def test_independent_provider_configs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_file = Path(tmpdir) / "cloud_providers.json"
            mock_settings = MagicMock()
            mock_settings.get_string.return_value = ""
            mgr = CloudConfigManager(config_path=cfg_file, settings=mock_settings)

            mgr.set_provider_config("llm", "openai", {"api_key": "sk-openai-123"})
            mgr.set_provider_config("llm", "anthropic", {"api_key": "sk-ant-456"})

            # Modifying Anthropic does not clobber OpenAI
            self.assertEqual(mgr.get_api_key("llm", "openai"), "sk-openai-123")
            self.assertEqual(mgr.get_api_key("llm", "anthropic"), "sk-ant-456")

    def test_tts_fallback_to_llm_openai_key(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_file = Path(tmpdir) / "cloud_providers.json"
            mock_settings = MagicMock()
            mock_settings.get_string.return_value = ""
            mgr = CloudConfigManager(config_path=cfg_file, settings=mock_settings)

            # Set OpenAI LLM key only
            mgr.set_provider_config("llm", "openai", {"api_key": "sk-openai-shared"})

            # TTS OpenAI key should fall back to LLM OpenAI key
            self.assertEqual(mgr.get_api_key("tts", "openai"), "sk-openai-shared")

            # Once explicit TTS key is set, it overrides fallback
            mgr.set_provider_config("tts", "openai", {"api_key": "sk-tts-specific"})
            self.assertEqual(mgr.get_api_key("tts", "openai"), "sk-tts-specific")

    def test_migration_from_gsettings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_file = Path(tmpdir) / "cloud_providers.json"

            mock_settings = MagicMock()
            settings_dict = {
                "llm-mode": "deepseek",
                "llm-api-key": "sk-migrated-deepseek",
                "llm-endpoint": "https://api.deepseek.com/v1/chat/completions",
                "llm-model": "deepseek-chat",
                "tts-api-key": "sk-migrated-tts",
                "tts-model": "tts-1-hd",
                "tts-cloud-voice": "nova",
                "stt-extra": json.dumps({"api_key": "sk-migrated-stt"}),
            }
            mock_settings.get_string.side_effect = lambda k: settings_dict.get(k, "")

            mgr = CloudConfigManager(config_path=cfg_file, settings=mock_settings)
            data = mgr.load()

            # Verify migrated deepseek
            self.assertEqual(data["llm"]["deepseek"]["api_key"], "sk-migrated-deepseek")
            self.assertEqual(data["llm"]["deepseek"]["model"], "deepseek-chat")

            # Verify migrated TTS
            self.assertEqual(data["tts"]["openai"]["api_key"], "sk-migrated-tts")
            self.assertEqual(data["tts"]["openai"]["model"], "tts-1-hd")
            self.assertEqual(data["tts"]["openai"]["voice"], "nova")

            # Verify migrated STT
            self.assertEqual(data["stt"]["openai_cloud"]["api_key"], "sk-migrated-stt")
            self.assertEqual(data["stt"]["groq_cloud"]["api_key"], "sk-migrated-stt")


if __name__ == "__main__":
    unittest.main()
