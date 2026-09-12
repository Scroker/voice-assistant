import json
import pytest
from unittest.mock import MagicMock, patch
from io import BytesIO

from gui.components.settings.model_selector import ModelSelectorController
from daemon.core.provider_manager import ProviderManager


class TestOllamaMatching:
    def test_ollama_model_matching(self):
        # Stesso tag
        assert ModelSelectorController._models_match("llama3.2", "llama3.2", "ollama") is True
        assert ModelSelectorController._models_match("llama3.2:1b", "llama3.2:1b", "ollama") is True
        
        # Tag implicito vs :latest
        assert ModelSelectorController._models_match("llama3.2", "llama3.2:latest", "ollama") is True
        assert ModelSelectorController._models_match("llama3.2:latest", "llama3.2", "ollama") is True

        # Tag diversi NON devono matchare
        assert ModelSelectorController._models_match("llama3.2", "llama3.2:1b", "ollama") is False
        assert ModelSelectorController._models_match("llama3.2:1b", "llama3.2", "ollama") is False
        assert ModelSelectorController._models_match("llama3.2:3b", "llama3.2", "ollama") is False
        assert ModelSelectorController._models_match("llama3.2:3b", "llama3.2:1b", "ollama") is False
        assert ModelSelectorController._models_match("qwen2.5:3b", "llama3.2:3b", "ollama") is False

    def test_local_file_model_matching(self):
        assert ModelSelectorController._models_match("Llama-3.2-1B-Instruct-Q4_K_M.gguf", "Llama-3.2-1B-Instruct-Q4_K_M", "gguf") is True
        assert ModelSelectorController._models_match("vosk-model-small-it-0.22", "small-it-0.22", "vosk") is True


class TestOllamaPull:
    def test_multi_layer_progress_tracking(self):
        stream_lines = [
            json.dumps({"status": "pulling manifest"}).encode("utf-8") + b"\n",
            json.dumps({"status": "pulling layer1", "digest": "sha256:layer1", "total": 1000, "completed": 500}).encode("utf-8") + b"\n",
            json.dumps({"status": "pulling layer2", "digest": "sha256:layer2", "total": 200, "completed": 200}).encode("utf-8") + b"\n",
            json.dumps({"status": "pulling layer1", "digest": "sha256:layer1", "total": 1000, "completed": 1000}).encode("utf-8") + b"\n",
            json.dumps({"status": "success"}).encode("utf-8") + b"\n",
        ]

        fake_resp = BytesIO(b"".join(stream_lines))
        fake_resp.__enter__ = MagicMock(return_value=fake_resp)
        fake_resp.__exit__ = MagicMock(return_value=None)

        owner = MagicMock()
        owner.settings.get_string.return_value = "http://localhost:11434"
        pm = ProviderManager(owner=owner)
        progress_calls = []

        with patch("urllib.request.urlopen", return_value=fake_resp):
            pm.download_ollama_model("test-model", progress_cb=lambda p: progress_calls.append(p))

        # Progresso finale deve essere 100
        assert 100 in progress_calls
        # Il progresso layer2 (200/1200 ~ 16%) aggregato a layer1 (500/1200 ~ 58%)
        # non deve essere saltato a 99% a metà download per via del manifest o layer2
        assert all(0 <= p <= 100 for p in progress_calls)

    def test_pull_error_raises_exception(self):
        stream_lines = [
            json.dumps({"status": "pulling manifest"}).encode("utf-8") + b"\n",
            json.dumps({"error": "pull model manifest: dial tcp i/o timeout"}).encode("utf-8") + b"\n",
        ]

        fake_resp = BytesIO(b"".join(stream_lines))
        fake_resp.__enter__ = MagicMock(return_value=fake_resp)
        fake_resp.__exit__ = MagicMock(return_value=None)

        owner = MagicMock()
        owner.settings.get_string.return_value = "http://localhost:11434"
        pm = ProviderManager(owner=owner)
        progress_calls = []

        with patch("urllib.request.urlopen", return_value=fake_resp):
            with pytest.raises(RuntimeError, match="Ollama pull error"):
                pm.download_ollama_model("test-model", progress_cb=lambda p: progress_calls.append(p))

        # 100 non deve essere stato emesso!
        assert 100 not in progress_calls

    def test_pull_interrupted_error_propagates(self):
        stream_lines = [
            json.dumps({"status": "pulling layer1", "digest": "sha256:layer1", "total": 1000, "completed": 100}).encode("utf-8") + b"\n",
            json.dumps({"status": "pulling layer1", "digest": "sha256:layer1", "total": 1000, "completed": 200}).encode("utf-8") + b"\n",
        ]

        fake_resp = BytesIO(b"".join(stream_lines))
        fake_resp.__enter__ = MagicMock(return_value=fake_resp)
        fake_resp.__exit__ = MagicMock(return_value=None)

        owner = MagicMock()
        owner.settings.get_string.return_value = "http://localhost:11434"
        pm = ProviderManager(owner=owner)

        def mock_progress(pct):
            raise InterruptedError("Download canceled by user")

        with patch("urllib.request.urlopen", return_value=fake_resp):
            with pytest.raises(InterruptedError):
                pm.download_ollama_model("test-model", progress_cb=mock_progress)
