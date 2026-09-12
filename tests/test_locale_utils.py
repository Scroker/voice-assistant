import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src/daemon")))

from core.locale_utils import get_system_language, get_system_locale


class TestLocaleUtils(unittest.TestCase):
    def test_get_system_language_from_env(self):
        """Verifica l'estrazione corretta della lingua dalle variabili d'ambiente POSIX."""

        with patch.dict(os.environ, {"LC_ALL": "fr_FR.UTF-8", "LANG": "it_IT.UTF-8"}):
            self.assertEqual(get_system_language(), "fr")

        with patch.dict(os.environ, {"LC_ALL": "", "LC_MESSAGES": "de_DE.UTF-8", "LANG": "it_IT.UTF-8"}):
            self.assertEqual(get_system_language(), "de")

        with patch.dict(os.environ, {"LC_ALL": "", "LC_MESSAGES": "", "LANG": "es_ES.UTF-8"}):
            self.assertEqual(get_system_language(), "es")

        with patch.dict(os.environ, {"LC_ALL": "", "LC_MESSAGES": "", "LANG": "it_IT.UTF-8"}):
            self.assertEqual(get_system_language(), "it")

    def test_get_system_language_fallback_default(self):
        """Verifica il fallback sul valore predefinito quando l'ambiente non contiene locale valido."""
        clean_env = {k: v for k, v in os.environ.items() if k not in ("LC_ALL", "LC_MESSAGES", "LANG")}
        clean_env["LC_ALL"] = "C"
        clean_env["LANG"] = "C"

        with patch.dict(os.environ, clean_env, clear=True):
            with patch("locale.getlocale", return_value=(None, None)):
                with patch("gi.repository.GLib.get_language_names", return_value=["C", "POSIX"]):
                    self.assertEqual(get_system_language(default="en"), "en")

    def test_get_system_locale_extraction(self):
        """Verifica l'estrazione del locale completo (es. de_DE, it_IT)."""
        with patch.dict(os.environ, {"LANG": "de_DE.UTF-8"}):
            self.assertEqual(get_system_locale(), "de_DE")

        with patch.dict(os.environ, {"LANG": "it_IT.UTF-8"}):
            self.assertEqual(get_system_locale(), "it_IT")

if __name__ == '__main__':
    unittest.main()
