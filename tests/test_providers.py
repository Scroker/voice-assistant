import os
import sys
import glob
import unittest
from unittest.mock import patch, MagicMock

# Aggiunge venv site-packages se presente
venv_sites = glob.glob(os.path.expanduser("~/.local/share/gnome-shell/extensions/voice-assistant@scroker.github.io/daemon/venv/lib/python*/site-packages"))
if venv_sites:
    sys.path.insert(0, venv_sites[0])

# Add src/daemon to import path
daemon_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "daemon"))
if daemon_dir not in sys.path:
    sys.path.insert(0, daemon_dir)

from providers.base import STTProvider

class TestProviders(unittest.TestCase):

    def test_base_provider_abstract(self):
        """Verifica che STTProvider sia una classe base astratta valida."""
        class DummyProvider(STTProvider):
            def __init__(self, model_name: str, hardware: str, extra: dict):
                pass
            def process_chunk(self, data: bytes) -> tuple[str, str]:
                return ("", "")
            def flush_and_transcribe(self) -> str:
                return ""
            def reset(self):
                pass
            @classmethod
            def get_available_models(cls) -> list[dict]:
                return []

        provider = DummyProvider("model", "cpu", {})
        self.assertIsNotNone(provider)

    def test_whisper_provider_init(self):
        """Verifica che WhisperProvider sia istanziabile in modalità mock senza rete."""
        mock_fw = MagicMock()
        with patch.dict(sys.modules, {'faster_whisper': mock_fw}):
            from providers.whisper_provider import WhisperProvider
            provider = WhisperProvider(model_size="tiny", hardware="cpu", extra={}, download_only=True)
            self.assertIsNotNone(provider)

    @patch("providers.vosk_provider.VoskProvider._download_model")
    def test_vosk_provider_init(self, mock_download):
        """Verifica che VoskProvider sia istanziabile in modalità mock senza rete."""
        from providers.vosk_provider import VoskProvider
        provider = VoskProvider(model_name="vosk-model-small-it-0.22", hardware="cpu", extra={}, download_only=True)
        self.assertIsNotNone(provider)

if __name__ == '__main__':
    unittest.main()
