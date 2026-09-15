import os
import sys
import unittest
from unittest.mock import MagicMock, patch

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

    def test_truncated_local_file_is_resumed_not_treated_as_complete(self):
        """Un download interrotto non deve essere scambiato per completo solo perché supera i 10 MB."""
        import tempfile
        import services.llm_service as llm_service

        llm_service._VERIFIED_COMPLETE.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "modello.gguf")
            with open(target, "wb") as f:
                f.write(b"x" * 35_000_000)  # troncato, ma oltre la vecchia soglia dei 10 MB

            resumed = {}

            def _fake_urlopen(req, *args, **kwargs):
                # La HEAD di verifica dichiara un file remoto molto più grande di quello locale.
                if req.get_method() == "HEAD":
                    head = MagicMock()
                    head.headers = {"x-linked-size": "2000000000"}
                    ctx = MagicMock()
                    ctx.__enter__.return_value = head
                    return ctx
                resumed["range"] = req.headers.get("Range")
                # Il server chiude la connessione senza inviare i byte mancanti.
                body = MagicMock()
                body.status = 206
                body.headers = {"Content-Range": "bytes 35000000-1999999999/2000000000"}
                body.read.side_effect = [b"", b""]
                ctx = MagicMock()
                ctx.__enter__.return_value = body
                return ctx

            with patch('urllib.request.urlopen', side_effect=_fake_urlopen):
                with self.assertRaises(IOError):
                    llm_service.download_llm_model("modello.gguf", models_dir=tmpdir)

            self.assertEqual(
                resumed.get("range"), "bytes=35000000-",
                "Il download deve riprendere dal punto in cui era stato interrotto",
            )

    def test_complete_local_file_skips_download(self):
        """Un file già completo non deve essere riscaricato, e la verifica va fatta una sola volta."""
        import tempfile
        import services.llm_service as llm_service

        llm_service._VERIFIED_COMPLETE.clear()
        head_calls = []

        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "modello.gguf")
            with open(target, "wb") as f:
                f.write(b"x" * 1000)

            def _fake_urlopen(req, *args, **kwargs):
                head_calls.append(req.full_url)
                head = MagicMock()
                head.headers = {"x-linked-size": "1000"}
                ctx = MagicMock()
                ctx.__enter__.return_value = head
                return ctx

            with patch('urllib.request.urlopen', side_effect=_fake_urlopen):
                reports = []
                dest = llm_service.download_llm_model("modello.gguf", progress_callback=reports.append, models_dir=tmpdir)
                self.assertEqual(dest, target)
                self.assertEqual(reports[-1], 100)

                # La seconda chiamata usa la cache: nessuna nuova richiesta di verifica.
                llm_service.download_llm_model("modello.gguf", models_dir=tmpdir)
                self.assertEqual(len(head_calls), 1, "La dimensione remota va verificata una sola volta per file")

    def test_part_marker_detects_incomplete_file_without_network(self):
        """Il marcatore .part deve bastare a riconoscere un file incompleto anche offline."""
        import tempfile
        import services.llm_service as llm_service

        llm_service._VERIFIED_COMPLETE.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "modello.gguf")
            with open(target, "wb") as f:
                f.write(b"x" * 20_000_000)
            llm_service._write_part_marker(target, 2_000_000_000)

            # Nessuna HEAD deve essere necessaria: la dimensione attesa è già sul disco.
            with patch('services.llm_service._remote_content_length', side_effect=AssertionError("verifica remota non necessaria")), \
                 patch('urllib.request.urlopen', side_effect=OSError("rete non disponibile")):
                with self.assertRaises(OSError):
                    llm_service.download_llm_model("modello.gguf", models_dir=tmpdir)

    def test_offline_falls_back_to_existing_large_file(self):
        """Senza rete, un modello già presente e plausibilmente completo resta utilizzabile."""
        import tempfile
        import services.llm_service as llm_service

        llm_service._VERIFIED_COMPLETE.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "modello.gguf")
            with open(target, "wb") as f:
                f.write(b"x" * 20_000_000)

            with patch('urllib.request.urlopen', side_effect=OSError("rete non disponibile")):
                dest = llm_service.download_llm_model("modello.gguf", models_dir=tmpdir)

            self.assertEqual(dest, target)

    @patch('urllib.request.urlopen')
    @patch('os.makedirs')
    def test_download_llm_model_single_arg_callback(self, mock_makedirs, mock_urlopen):
        """Verifica che download_llm_model funzioni correttamente con callback a 1 parametro (pct)."""
        from services.llm_service import download_llm_model
        
        mock_stream = MagicMock()
        mock_stream.headers.get.return_value = "100"
        mock_stream.read.side_effect = [b"a"*50, b"b"*50, b""]
        mock_response = MagicMock()
        mock_response.__enter__.return_value = mock_stream
        mock_urlopen.return_value = mock_response

        progress_reports = []
        def progress_cb(pct):
            progress_reports.append(pct)

        with patch('builtins.open', unittest.mock.mock_open()):
            dest = download_llm_model("Llama-3.2-1B-Instruct-Q4_K_M.gguf", progress_callback=progress_cb)

        self.assertTrue(dest.endswith("Llama-3.2-1B-Instruct-Q4_K_M.gguf"))
        self.assertTrue(len(progress_reports) > 0)
        self.assertEqual(progress_reports[-1], 100)

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
        self.assertTrue(any(m["repo"] == "TheBloke/Llama-2-7B-GGUF" for m in models))
        self.assertTrue(any(m["name"] == "Llama 2 7B (GGUF)" for m in models))
        self.assertTrue(any(m["size_text"] != "GGUF" and ("GB" in m["size_text"] or "MB" in m["size_text"]) for m in models))

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

    def test_format_clock_context_locales(self):
        """Verifica la formattazione dell'orologio sia in lingua italiana che inglese."""
        import datetime
        from services.llm_service import format_clock_context

        # 2026-09-06 is a Sunday (index 6)
        fixed_dt = datetime.datetime(2026, 9, 6, 15, 30)

        clock_it = format_clock_context(lang="it", now=fixed_dt)
        self.assertIn("Domenica", clock_it)
        self.assertIn("Settembre", clock_it)
        self.assertIn("15:30", clock_it)

        clock_en = format_clock_context(lang="en", now=fixed_dt)
        self.assertIn("Sunday", clock_en)
        self.assertIn("September", clock_en)
        self.assertIn("15:30", clock_en)

    def test_apply_prompt_template(self):
        """Verifica la corretta interpolazione dei placeholder nel prompt."""
        from services.llm_service import apply_prompt_template

        tmpl = "Pre:\n{tools_definition}\n\n{context}\n\n{clock}\nPost"
        res = apply_prompt_template(
            tmpl,
            clock_context="Clock: 12:00",
            mcp_tools_prompt="Tools: set_volume",
            context="Context: Memory"
        )
        self.assertIn("Clock: 12:00", res)
        self.assertIn("Tools: set_volume", res)
        self.assertIn("Context: Memory", res)
        self.assertNotIn("{clock}", res)
        self.assertNotIn("{tools_definition}", res)
        self.assertNotIn("{context}", res)

    def test_apply_prompt_template_legacy_fallback(self):
        """Verifica che un prompt legacy senza placeholder riceva comunque i contesti in append."""
        from services.llm_service import apply_prompt_template

        legacy_prompt = "Sei un assistente GNOME."
        res = apply_prompt_template(
            legacy_prompt,
            clock_context="Clock: 12:00",
            mcp_tools_prompt="Tools: set_volume"
        )
        self.assertTrue(res.startswith("Sei un assistente GNOME."))
        self.assertIn("Clock: 12:00", res)
        self.assertIn("Tools: set_volume", res)

    def test_normalize_endpoint(self):
        """Verifica la normalizzazione degli endpoint noti."""
        from services.llm_service import normalize_endpoint

        self.assertEqual(normalize_endpoint("http://localhost:11434"), "http://localhost:11434/v1/chat/completions")
        self.assertEqual(normalize_endpoint("http://localhost:11434/"), "http://localhost:11434/v1/chat/completions")
        self.assertEqual(normalize_endpoint("https://api.openai.com"), "https://api.openai.com/v1/chat/completions")
        self.assertEqual(normalize_endpoint("https://api.openai.com/v1/chat/completions"), "https://api.openai.com/v1/chat/completions")

    @patch('urllib.request.urlopen')
    def test_openai_compatible_native_tool_call(self, mock_urlopen):
        """Verifica lo streaming di native tool calls e la loro corretta emissione."""
        from services.llm_service import OpenAICompatibleClient

        mock_response = MagicMock()
        mock_response.__enter__.return_value = [
            b'data: {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "type": "function", "function": {"name": "set_volume", "arguments": "{\\"volume\\": "}}]}}]}\n',
            b'data: {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "80}"}}]}}]}\n',
            b'data: [DONE]\n'
        ]
        mock_urlopen.return_value = mock_response

        client = OpenAICompatibleClient(endpoint="https://api.openai.com/v1/chat/completions")
        events = list(client.stream_chat(
            messages=[{"role": "user", "content": "imposta il volume a 80"}],
            tools=[{"type": "function", "function": {"name": "set_volume"}}]
        ))

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "tool_call")
        self.assertEqual(events[0]["tool"], "set_volume")
        self.assertEqual(events[0]["args"], {"volume": 80})

    def test_load_default_providers(self):
        """Verifica che la configurazione dei provider di default venga caricata correttamente."""
        from services.llm_service import load_default_providers

        providers = load_default_providers()
        self.assertIn("openai", providers)
        self.assertIn("ollama", providers)
        self.assertIn("groq", providers)
        self.assertIn("deepseek", providers)
        self.assertIn("anthropic", providers)
        self.assertIn("ollama_cloud", providers)
        self.assertIn("local", providers)

    def test_normalize_endpoint_deepseek_and_ollama_cloud(self):
        """Verifica che normalize_endpoint gestisca correttamente DeepSeek e Ollama Cloud."""
        from services.llm_service import normalize_endpoint

        self.assertEqual(
            normalize_endpoint("https://api.deepseek.com"),
            "https://api.deepseek.com/v1/chat/completions"
        )
        self.assertEqual(
            normalize_endpoint("https://ollama.com"),
            "https://ollama.com/v1/chat/completions"
        )
        self.assertEqual(
            normalize_endpoint("https://ollama.com/v1"),
            "https://ollama.com/v1/chat/completions"
        )

    @patch('urllib.request.urlopen')
    def test_deepseek_streaming(self, mock_urlopen):
        """Verifica che lo streaming con provider deepseek utilizzi il formato OpenAI-compatible."""
        settings_observer = MagicMock()
        settings_observer.get.side_effect = lambda k, d=None: "deepseek" if k == "llm-mode" else ("test-key" if k == "llm-api-key" else d)

        mock_response = MagicMock()
        mock_response.__enter__.return_value = [
            b'data: {"choices": [{"delta": {"content": "Risposta da "}}]}\n',
            b'data: {"choices": [{"delta": {"content": "DeepSeek."}}]}\n',
            b'data: [DONE]\n'
        ]
        mock_urlopen.return_value = mock_response

        manager = LLMServiceManager(settings_observer=settings_observer)
        tokens = list(manager.stream_tokens("Ciao"))
        self.assertEqual(tokens, ["Risposta da ", "DeepSeek."])

    @patch('urllib.request.urlopen')
    def test_ollama_cloud_streaming(self, mock_urlopen):
        """Verifica che lo streaming con provider ollama_cloud utilizzi il formato OpenAI-compatible."""
        settings_observer = MagicMock()
        settings_observer.get.side_effect = lambda k, d=None: "ollama_cloud" if k == "llm-mode" else ("test-key" if k == "llm-api-key" else d)

        mock_response = MagicMock()
        mock_response.__enter__.return_value = [
            b'data: {"choices": [{"delta": {"content": "Risposta da "}}]}\n',
            b'data: {"choices": [{"delta": {"content": "Ollama Cloud."}}]}\n',
            b'data: [DONE]\n'
        ]
        mock_urlopen.return_value = mock_response

        manager = LLMServiceManager(settings_observer=settings_observer)
        tokens = list(manager.stream_tokens("Ciao"))
        self.assertEqual(tokens, ["Risposta da ", "Ollama Cloud."])

    @patch('urllib.request.urlopen')
    def test_http_401_error_handling(self, mock_urlopen):
        """Verifica che un errore HTTP 401 Unauthorized restituisca un messaggio chiaro di autenticazione."""
        import urllib.error
        import io
        err = urllib.error.HTTPError(
            url="https://ollama.com/v1/chat/completions",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=io.BytesIO(b'{"error": {"message": "Unauthorized"}}')
        )
        mock_urlopen.side_effect = err

        settings_observer = MagicMock()
        settings_observer.get.side_effect = lambda k, d=None: "ollama_cloud" if k == "llm-mode" else ("bad-key" if k == "llm-api-key" else d)

        manager = LLMServiceManager(settings_observer=settings_observer)
        tokens = list(manager.stream_tokens("Ciao"))
        self.assertTrue(len(tokens) > 0)
        self.assertIn("401 Unauthorized", tokens[0])
        self.assertIn("chiave API", tokens[0])

    @patch('urllib.request.urlopen')
    def test_http_404_error_handling(self, mock_urlopen):
        """Verifica che un errore HTTP 404 Not Found restituisca un messaggio chiaro sul modello non trovato."""
        import urllib.error
        import io
        err = urllib.error.HTTPError(
            url="https://ollama.com/v1/chat/completions",
            code=404,
            msg="Not Found",
            hdrs={},
            fp=io.BytesIO(b'{"error": "model not found"}')
        )
        mock_urlopen.side_effect = err

        settings_observer = MagicMock()
        settings_observer.get.side_effect = lambda k, d=None: "ollama_cloud" if k == "llm-mode" else d

        manager = LLMServiceManager(settings_observer=settings_observer)
        tokens = list(manager.stream_tokens("Ciao"))
        self.assertTrue(len(tokens) > 0)
        self.assertIn("404 Not Found", tokens[0])


if __name__ == '__main__':
    unittest.main()


