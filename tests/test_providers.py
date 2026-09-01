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

    @patch("urllib.request.urlopen")
    def test_cloud_stt_provider(self, mock_urlopen):
        """Verifica l'elaborazione dei chunk e l'invio HTTP di OpenAICloudSTTProvider."""
        from providers.openai_cloud_provider import OpenAICloudSTTProvider
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"text": "Ciao assistente"}'
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        provider = OpenAICloudSTTProvider(model="whisper-1", hardware="cloud", extra={"api_key": "test_key"})
        res, partial = provider.process_chunk(b"\x00\x00" * 3200)
        self.assertEqual(res, "")
        self.assertEqual(partial, "")

        transcription = provider.flush_and_transcribe()
        self.assertEqual(transcription, "Ciao assistente")

if __name__ == '__main__':
    unittest.main()

