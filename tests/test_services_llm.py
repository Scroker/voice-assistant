import os
import sys
import glob
import unittest
from unittest.mock import MagicMock, patch

# Aggiunge venv site-packages se presente
venv_sites = glob.glob(os.path.expanduser("~/.local/share/gnome-shell/extensions/voice-assistant@scroker.github.io/daemon/venv/lib/python*/site-packages"))
if venv_sites:
    sys.path.insert(0, venv_sites[0])

# Add src/daemon to import path
daemon_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "daemon"))
if daemon_dir not in sys.path:
    sys.path.insert(0, daemon_dir)

from services.llm_service import LLMServiceManager

class TestServicesLLM(unittest.TestCase):

    @patch('urllib.request.urlopen')
    def test_ollama_streaming(self, mock_urlopen):
        """Verifica che lo streaming dei token da un server Ollama venga decodificato correttamente."""
        mock_response = MagicMock()
        mock_response.__enter__.return_value = [
            b'{"response": "Ciao! ", "done": false}\n',
            b'{"response": "Sono il tuo assistente.", "done": true}\n'
        ]
        mock_urlopen.return_value = mock_response

        manager = LLMServiceManager()
        tokens = list(manager.stream_tokens("Ciao"))

        self.assertEqual(tokens, ["Ciao! ", "Sono il tuo assistente."])

    @patch('urllib.request.urlopen')
    def test_openai_compatible_streaming(self, mock_urlopen):
        """Verifica che lo streaming nel formato OpenAI SSE delta venga decodificato correttamente."""
        settings_observer = MagicMock()
        settings_observer.get.side_effect = lambda k, d=None: "http://localhost:1234/v1/chat/completions" if k == "llm-endpoint" else d

        mock_response = MagicMock()
        mock_response.__enter__.return_value = [
            b'data: {"choices": [{"delta": {"content": "Ecco "}}]}\n',
            b'data: {"choices": [{"delta": {"content": "la risposta."}}]}\n',
            b'data: [DONE]\n'
        ]
        mock_urlopen.return_value = mock_response

        manager = LLMServiceManager(settings_observer=settings_observer)
        tokens = list(manager.stream_tokens("Dimmi qualcosa"))

        self.assertEqual(tokens, ["Ecco ", "la risposta."])

if __name__ == '__main__':
    unittest.main()
