import os
import sys
import unittest
from unittest.mock import MagicMock

DAEMON_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "daemon"))
if DAEMON_DIR not in sys.path:
    sys.path.insert(0, DAEMON_DIR)

from core.model_manager import ModelManager


class TestModelManager(unittest.TestCase):
    def test_idle_purge_releases_registered_owner(self):
        manager = ModelManager(idle_timeout_sec=1)
        instance = object()
        unload = MagicMock()
        manager.register_instance("llm", instance, unload)
        manager.last_active_time -= 2

        self.assertTrue(manager.check_idle_and_purge())
        unload.assert_called_once_with()
        self.assertIsNone(manager.llm_instance)

    def test_purge_only_releases_selected_model_kinds(self):
        manager = ModelManager()
        stt_unload = MagicMock()
        llm_unload = MagicMock()
        manager.register_instance("stt", object(), stt_unload)
        manager.register_instance("llm", object(), llm_unload)

        self.assertTrue(manager.purge_vram_and_ram(unload_stt=False))
        stt_unload.assert_not_called()
        llm_unload.assert_called_once_with()
        self.assertIsNotNone(manager.stt_instance)
        self.assertIsNone(manager.llm_instance)

    def test_stt_unload_callback_releases_daemon_owner(self):
        owner = MagicMock()
        provider = object()
        owner.provider = provider
        manager = ModelManager()
        manager.register_instance("stt", provider, lambda: setattr(owner, "provider", None))

        manager.purge_vram_and_ram(unload_llm=False, unload_tts=False)

        self.assertIsNone(owner.provider)
        self.assertIsNone(manager.stt_instance)

    def test_per_model_timeout_only_purges_expired_models(self):
        manager = ModelManager(idle_timeout_sec=300, idle_timeouts={"llm": 1})
        stt_unload = MagicMock()
        llm_unload = MagicMock()
        manager.register_instance("stt", object(), stt_unload)
        manager.register_instance("llm", object(), llm_unload)
        manager.last_active_time -= 2

        self.assertTrue(manager.check_idle_and_purge())
        stt_unload.assert_not_called()
        llm_unload.assert_called_once_with()

    def test_resource_metrics_include_loaded_model_status(self):
        manager = ModelManager()
        manager.register_instance("tts", object())

        metrics = manager.get_resource_metrics()

        self.assertIn("rss_bytes", metrics)
        self.assertTrue(metrics["loaded_models"]["tts"])
        self.assertIn("llm", metrics["idle_timeouts"])


if __name__ == "__main__":
    unittest.main()