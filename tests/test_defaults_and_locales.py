"""
Test suite per la Fase 6: Validazione file JSON di configurazione/locales,
risoluzione dei percorsi XDG (path_utils) e internazionalizzazione (locale_utils).
"""

import os
import sys
import json
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src/daemon")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))


class TestDefaultsAndLocales(unittest.TestCase):

    def test_supported_languages_json_schema(self):
        """Verifica che data/locales/supported_languages.json sia valido e contenga tutte le 15 lingue."""
        json_path = Path(__file__).resolve().parent.parent / "data" / "locales" / "supported_languages.json"
        self.assertTrue(json_path.is_file(), f"File non trovato: {json_path}")

        with open(json_path, "r", encoding="utf-8") as f:
            languages = json.load(f)

        self.assertIsInstance(languages, list)
        self.assertGreaterEqual(len(languages), 15)

        codes = set()
        for item in languages:
            self.assertIn("code", item)
            self.assertIn("name", item)
            self.assertIn("english_name", item)
            codes.add(item["code"])

        self.assertIn("it", codes)
        self.assertIn("en", codes)
        self.assertIn("de", codes)
        self.assertIn("fr", codes)
        self.assertIn("es", codes)

    def test_defaults_json_schema(self):
        """Verifica la validità dello schema data/config/defaults.json."""
        json_path = Path(__file__).resolve().parent.parent / "data" / "config" / "defaults.json"
        self.assertTrue(json_path.is_file(), f"File non trovato: {json_path}")

        with open(json_path, "r", encoding="utf-8") as f:
            defaults = json.load(f)

        self.assertIn("paths", defaults)
        self.assertIn("logging", defaults)
        self.assertIn("audio", defaults)
        self.assertIn("network", defaults)

        paths = defaults["paths"]
        self.assertIn("models_dir", paths)
        self.assertIn("logs_dir", paths)
        self.assertIn("cache_dir", paths)

        logging_cfg = defaults["logging"]
        self.assertGreater(logging_cfg.get("max_bytes", 0), 0)
        self.assertGreater(logging_cfg.get("backup_count", 0), 0)

        audio_cfg = defaults["audio"]
        self.assertEqual(audio_cfg.get("sample_rate"), 16000)
        self.assertEqual(audio_cfg.get("channels"), 1)

    def test_locale_utils_get_supported_languages(self):
        """Verifica che locale_utils.get_supported_languages() restituisca le lingue corrette."""
        from core.locale_utils import get_supported_languages, get_language_label

        langs = get_supported_languages()
        self.assertIsInstance(langs, list)
        self.assertGreaterEqual(len(langs), 15)

        self.assertEqual(get_language_label("it"), "Italiano (it)")
        self.assertEqual(get_language_label("en"), "English (en)")
        self.assertEqual(get_language_label("xx_unknown"), "XX_UNKNOWN (xx_unknown)")

    def test_path_utils_xdg_resolution(self):
        """Verifica la risoluzione dei percorsi di path_utils rispettando XDG."""
        from core.path_utils import (
            get_data_dir,
            get_models_dir,
            get_stt_models_dir,
            get_tts_models_dir,
            get_llm_models_dir,
            get_wakeword_models_dir,
            get_oww_models_dir,
            get_logs_dir,
            get_cache_dir,
            get_logging_config,
            get_audio_config,
            get_network_config,
        )

        test_xdg_data = "/tmp/test_va_data"
        test_xdg_cache = "/tmp/test_va_cache"

        with patch.dict(os.environ, {"XDG_DATA_HOME": test_xdg_data, "XDG_CACHE_HOME": test_xdg_cache}):
            data_dir = get_data_dir()
            self.assertEqual(str(data_dir), f"{test_xdg_data}/voice-assistant")

            models_dir = get_models_dir()
            self.assertEqual(str(models_dir), f"{test_xdg_data}/voice-assistant/models")

            stt_dir = get_stt_models_dir()
            self.assertEqual(str(stt_dir), f"{test_xdg_data}/voice-assistant/models/stt")

            tts_dir = get_tts_models_dir()
            self.assertEqual(str(tts_dir), f"{test_xdg_data}/voice-assistant/models/tts")

            llm_dir = get_llm_models_dir()
            self.assertEqual(str(llm_dir), f"{test_xdg_data}/voice-assistant/models/llm")

            ww_dir = get_wakeword_models_dir()
            self.assertEqual(str(ww_dir), f"{test_xdg_data}/voice-assistant/models/wakeword")

            oww_dir = get_oww_models_dir()
            self.assertEqual(str(oww_dir), f"{test_xdg_data}/voice-assistant/models/wakeword/openwakeword")

            logs_dir = get_logs_dir()
            self.assertEqual(str(logs_dir), f"{test_xdg_data}/voice-assistant/logs")

            cache_dir = get_cache_dir("catalog")
            self.assertEqual(str(cache_dir), f"{test_xdg_cache}/voice-assistant/catalog")

        # Verifica override personalizzato per i modelli
        custom_dir = "/tmp/custom_models_dir"
        self.assertEqual(str(get_models_dir(custom_dir)), custom_dir)
        self.assertEqual(str(get_stt_models_dir(custom_dir)), f"{custom_dir}/stt")
        self.assertEqual(str(get_wakeword_models_dir(custom_dir)), f"{custom_dir}/wakeword")
        self.assertEqual(str(get_oww_models_dir(custom_dir)), f"{custom_dir}/wakeword/openwakeword")

        # Verifica dizionari configurazione
        self.assertEqual(get_audio_config().get("sample_rate"), 16000)
        self.assertEqual(get_network_config().get("catalog_cache_ttl_hours"), 24)
        self.assertGreater(get_logging_config().get("max_bytes", 0), 1000)

    def test_gui_settings_backward_compatibility(self):
        """Verifica che le esportazioni retrocompatibili della GUI continuino a funzionare."""
        from gui.components.settings.general import SUPPORTED_LANGUAGES, get_language_label
        from gui.components.settings.models import _DEFAULT_MODELS_DIR

        self.assertIsInstance(SUPPORTED_LANGUAGES, list)
        self.assertGreaterEqual(len(SUPPORTED_LANGUAGES), 15)
        self.assertIn("Italiano (it)", get_language_label("it"))
        self.assertTrue(isinstance(_DEFAULT_MODELS_DIR, str) and len(_DEFAULT_MODELS_DIR) > 0)


if __name__ == "__main__":
    unittest.main()
