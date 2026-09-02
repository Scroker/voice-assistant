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


    @patch('urllib.request.urlopen')
    @patch('os.makedirs')
    def test_download_llm_model_helper(self, mock_makedirs, mock_urlopen):
        """Verifica che download_llm_model scarichi correttamente il file con report sul progresso."""
        from services.llm_service import download_llm_model
        
        mock_stream = MagicMock()
        mock_stream.headers.get.return_value = "100"
        mock_stream.read.side_effect = [b"a"*50, b"b"*50, b""]
        mock_response = MagicMock()
        mock_response.__enter__.return_value = mock_stream
        mock_urlopen.return_value = mock_response

        progress_reports = []
        def progress_cb(model_id, pct):
            progress_reports.append((model_id, pct))

        with patch('builtins.open', unittest.mock.mock_open()):
            dest = download_llm_model("Llama-3.2-1B-Instruct-Q4_K_M.gguf", progress_callback=progress_cb)

        self.assertTrue(dest.endswith("Llama-3.2-1B-Instruct-Q4_K_M.gguf"))
        self.assertTrue(len(progress_reports) > 0)
        self.assertEqual(progress_reports[-1][1], 100)

    @patch('urllib.request.urlopen')
    def test_fetch_huggingface_models(self, mock_urlopen):
        """Verifica la ricerca dinamica dei modelli GGUF tramite Hugging Face API."""
        from services.llm_service import fetch_huggingface_models
        mock_response = MagicMock()
        mock_response.__enter__.return_value.read.return_value = b'''[
            {"id": "TheBloke/Llama-2-7B-GGUF", "downloads": 5000, "likes": 100}
        ]'''
        mock_urlopen.return_value = mock_response

        models = fetch_huggingface_models("Llama-2")
        self.assertTrue(any(m["name"] == "TheBloke/Llama-2-7B-GGUF" for m in models))

    @patch('urllib.request.urlopen')
    @patch('os.makedirs')
    def test_download_custom_huggingface_model(self, mock_makedirs, mock_urlopen):
        """Verifica il download di un modello arbitrario con notazione repo:filename."""
        from services.llm_service import download_llm_model

        mock_stream = MagicMock()
        mock_stream.headers.get.return_value = "100"
        mock_stream.read.side_effect = [b"x"*100, b""]
        mock_response = MagicMock()
        mock_response.__enter__.return_value = mock_stream
        mock_urlopen.return_value = mock_response

        with patch('builtins.open', unittest.mock.mock_open()):
            dest = download_llm_model("TheBloke/Llama-2-7B-GGUF:llama-2-7b.Q4_K_M.gguf")

        self.assertTrue(dest.endswith("llama-2-7b.Q4_K_M.gguf"))

if __name__ == '__main__':
    unittest.main()


