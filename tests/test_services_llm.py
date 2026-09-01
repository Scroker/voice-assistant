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

        settings_observer = MagicMock()
        settings_observer.get.side_effect = lambda k, d=None: "ollama" if k == "llm-mode" else d

        manager = LLMServiceManager(settings_observer=settings_observer)
        tokens = list(manager.stream_tokens("Ciao"))

        self.assertEqual(tokens, ["Ciao! ", "Sono il tuo assistente."])

    @patch('urllib.request.urlopen')
    def test_openai_compatible_streaming(self, mock_urlopen):
        """Verifica che lo streaming nel formato OpenAI SSE delta venga decodificato correttamente."""
        settings_observer = MagicMock()
        settings_observer.get.side_effect = lambda k, d=None: "http://localhost:1234/v1/chat/completions" if k == "llm-endpoint" else ("ollama" if k == "llm-mode" else d)

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

    @patch('services.llm_service.LocalGGUFProvider.stream_tokens')
    def test_local_gguf_provider(self, mock_stream):
        """Verifica che la modalità locale GGUF richiami il provider in-daemon."""
        mock_stream.return_value = iter(["Risposta ", "locale."])
        settings_observer = MagicMock()
        settings_observer.get.side_effect = lambda k, d=None: "local" if k == "llm-mode" else d

        manager = LLMServiceManager(settings_observer=settings_observer)
        tokens = list(manager.stream_tokens("Ciao"))

        self.assertEqual(tokens, ["Risposta ", "locale."])

    @patch('urllib.request.urlopen')
    def test_anthropic_streaming(self, mock_urlopen):
        """Verifica che lo streaming dei token da Anthropic Claude API venga decodificato correttamente."""
        settings_observer = MagicMock()
        settings_observer.get.side_effect = lambda k, d=None: "anthropic" if k == "llm-mode" else ("test-key" if k == "llm-api-key" else d)

        mock_response = MagicMock()
        mock_response.__enter__.return_value = [
            b'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Ciao da "}}\n',
            b'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Claude!"}}\n',
        ]
        mock_urlopen.return_value = mock_response

        manager = LLMServiceManager(settings_observer=settings_observer)
        tokens = list(manager.stream_tokens("Ciao"))

        self.assertEqual(tokens, ["Ciao da ", "Claude!"])

if __name__ == '__main__':
    unittest.main()

