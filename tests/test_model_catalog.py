import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

daemon_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "daemon"))
if daemon_dir not in sys.path:
    sys.path.insert(0, daemon_dir)

from core.data_loader import load_json_data
from services.catalog_manager import InstalledModelDetector, ModelCatalogService, catalog_service
from providers.vosk_provider import VoskProvider
from providers.whisper_provider import WhisperProvider
from providers.openai_cloud_provider import OpenAICloudSTTProvider
from services.tts_service import PiperTTSProvider, OpenAITTSProvider


class TestModelCatalog(unittest.TestCase):

    def test_stt_models_json_valid(self):
        data = load_json_data("catalog/stt_models.json")
        self.assertIsNotNone(data)
        self.assertIn("endpoints", data)
        self.assertIn("vosk", data["endpoints"])
        self.assertIn("whisper", data["endpoints"])
        self.assertIn("defaults", data)
        self.assertEqual(data["defaults"]["vosk"]["it"], "vosk-model-small-it-0.22")
        self.assertEqual(data["defaults"]["whisper"], "base")
        self.assertIn("cloud_models", data)
        self.assertTrue(any(m["id"] == "whisper-1" for m in data["cloud_models"]))

    def test_tts_voices_json_valid(self):
        data = load_json_data("catalog/tts_voices.json")
        self.assertIsNotNone(data)
        self.assertIn("endpoints", data)
        self.assertIn("piper", data["endpoints"])
        self.assertIn("defaults", data)
        self.assertEqual(data["defaults"]["piper"]["it"], "it_IT-paola-medium")
        self.assertEqual(data["defaults"]["openai"], "alloy")
        self.assertIn("openai_voices", data)
        self.assertIn("alloy", data["openai_voices"])

    def test_installed_model_detector(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            models_dir = Path(tmpdir)
            stt_dir = models_dir / "stt"
            stt_dir.mkdir(parents=True)
            tts_dir = models_dir / "tts"
            tts_dir.mkdir(parents=True)

            # Create mock Vosk model
            vosk_model = stt_dir / "vosk-model-small-it-0.22"
            vosk_model.mkdir()
            (vosk_model / "am").touch()

            # Create mock Whisper model
            whisper_model = stt_dir / "models--Systran--faster-whisper-base"
            whisper_model.mkdir()

            # Create mock Piper voice
            voice_onnx = tts_dir / "it_IT-paola-medium.onnx"
            voice_onnx.write_bytes(b"dummy onnx content")
            voice_json = tts_dir / "it_IT-paola-medium.onnx.json"
            voice_json.write_bytes(b"{}")

            stt_installed = InstalledModelDetector.get_installed_stt_models(models_dir)
            self.assertIn("vosk-model-small-it-0.22", stt_installed)
            self.assertIn("base", stt_installed)

            tts_installed = InstalledModelDetector.get_installed_tts_voices(models_dir)
            self.assertIn("it_IT-paola-medium", tts_installed)

    def test_installed_model_detector_removes_on_deletion(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            models_dir = Path(tmpdir)
            stt_dir = models_dir / "stt"
            stt_dir.mkdir(parents=True)

            vosk_model = stt_dir / "vosk-model-small-it-0.22"
            vosk_model.mkdir()

            stt_installed = InstalledModelDetector.get_installed_stt_models(models_dir)
            self.assertIn("vosk-model-small-it-0.22", stt_installed)

            # Delete the model
            vosk_model.rmdir()

            stt_installed_after = InstalledModelDetector.get_installed_stt_models(models_dir)
            self.assertNotIn("vosk-model-small-it-0.22", stt_installed_after)

    def test_vosk_catalog_offline_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = ModelCatalogService(cache_dir=Path(tmpdir) / "cache", models_dir=Path(tmpdir) / "models")
            # Force network failure by mocking urlopen to raise URLError
            with patch("urllib.request.urlopen", side_effect=Exception("No network")):
                models = service.get_vosk_models(user_lang="it", force_refresh=True)

            self.assertTrue(len(models) > 0)
            self.assertTrue(any(m["id"] == "vosk-model-small-it-0.22" for m in models))
            self.assertIn("installed", models[0])

    def test_whisper_catalog_offline_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = ModelCatalogService(cache_dir=Path(tmpdir) / "cache", models_dir=Path(tmpdir) / "models")
            with patch("urllib.request.urlopen", side_effect=Exception("No network")):
                models = service.get_whisper_models(force_refresh=True)

            self.assertTrue(len(models) > 0)
            base_model = next((m for m in models if m["id"] == "base"), None)
            self.assertIsNotNone(base_model)
            self.assertTrue(base_model.get("recommended"))

    def test_piper_voices_offline_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = ModelCatalogService(cache_dir=Path(tmpdir) / "cache", models_dir=Path(tmpdir) / "models")
            with patch("urllib.request.urlopen", side_effect=Exception("No network")):
                voices = service.get_piper_voices(user_lang="it", force_refresh=True)

            self.assertTrue(len(voices) > 0)
            self.assertTrue(any(v["id"] == "it_IT-paola-medium" for v in voices))

    def test_defaults_resolution(self):
        self.assertEqual(catalog_service.get_default_model("vosk", "it"), "vosk-model-small-it-0.22")
        self.assertEqual(catalog_service.get_default_model("vosk", "en"), "vosk-model-small-en-us-0.15")
        self.assertEqual(catalog_service.get_default_model("whisper"), "base")
        self.assertEqual(catalog_service.get_default_model("openai_cloud"), "whisper-1")
        self.assertEqual(catalog_service.get_default_model("groq_cloud"), "whisper-large-v3")
        self.assertEqual(catalog_service.get_default_voice("piper", "it"), "it_IT-paola-medium")
        self.assertEqual(catalog_service.get_default_voice("piper", "en"), "en_US-lessac-medium")
        self.assertEqual(catalog_service.get_default_voice("openai"), "alloy")

    def test_vosk_and_whisper_providers_integration(self):
        with patch("urllib.request.urlopen", side_effect=Exception("Offline test")):
            vosk_models = VoskProvider.get_available_models(user_lang="it")
            self.assertTrue(len(vosk_models) > 0)
            self.assertEqual(VoskProvider.get_default_model(lang="it"), "vosk-model-small-it-0.22")

            whisper_models = WhisperProvider.get_available_models()
            self.assertTrue(len(whisper_models) > 0)
            self.assertEqual(WhisperProvider.get_default_model(), "base")

            cloud_models = OpenAICloudSTTProvider.get_available_models()
            self.assertTrue(len(cloud_models) >= 2)
            self.assertEqual(OpenAICloudSTTProvider.get_default_model(provider="openai_cloud"), "whisper-1")

    def test_tts_providers_integration(self):
        self.assertEqual(PiperTTSProvider.get_default_voice("it"), "it_IT-paola-medium")
        self.assertIn("alloy", OpenAITTSProvider.get_available_voices())

    def test_vosk_catalog_filters_tts_and_spk_models(self):
        import json
        with tempfile.TemporaryDirectory() as tmpdir:
            service = ModelCatalogService(cache_dir=Path(tmpdir) / "cache", models_dir=Path(tmpdir) / "models")
            fake_payload = json.dumps([
                {"name": "vosk-model-small-it-0.22", "lang": "it", "type": "small", "size_text": "47MB"},
                {"name": "vosk-model-tts-ru-0.9-multi", "lang": "ru", "type": "tts", "size_text": "700MB"},
                {"name": "vosk-model-spk-0.4", "lang": "all", "type": "spk", "size_text": "13MB"},
                {"name": "vosk-model-uk-v3-lgraph", "lang": "uk", "type": "big-lgraph", "size_text": "300MB"},
            ]).encode("utf-8")

            mock_resp = MagicMock()
            mock_resp.read.return_value = fake_payload
            mock_resp.__enter__.return_value = mock_resp

            with patch("urllib.request.urlopen", return_value=mock_resp):
                models = service.get_vosk_models(force_refresh=True)

            model_ids = [m["id"] for m in models]
            self.assertIn("vosk-model-small-it-0.22", model_ids)
            self.assertIn("vosk-model-uk-v3-lgraph", model_ids)
            self.assertNotIn("vosk-model-tts-ru-0.9-multi", model_ids)
            self.assertNotIn("vosk-model-spk-0.4", model_ids)


    def test_provider_manager_llm_gguf_and_ollama_catalog(self):
        import json
        from core.provider_manager import ProviderManager

        with tempfile.TemporaryDirectory() as tmpdir:
            llm_dir = Path(tmpdir) / "llm"
            llm_dir.mkdir(parents=True)
            local_model = llm_dir / "Llama-3.2-1B-Instruct-Q4_K_M.gguf"
            local_model.write_bytes(b"x" * 1024 * 1024)  # 1MB dummy file

            mock_owner = MagicMock()
            mock_owner.models_dir = tmpdir
            mock_owner.settings = None

            pm = ProviderManager(mock_owner)

            # Test GGUF
            with patch("services.llm_service.fetch_huggingface_models", return_value=[]):
                raw_gguf = pm.get_available_models("gguf")
                gguf_models = json.loads(raw_gguf)
                self.assertTrue(len(gguf_models) > 0)
                llama_inst = next((m for m in gguf_models if "llama-3.2-1b" in m["file"].lower()), None)
                self.assertIsNotNone(llama_inst)
                self.assertTrue(llama_inst["installed"])

            # Test Ollama (offline)
            with patch("urllib.request.urlopen", side_effect=Exception("Ollama offline")):
                raw_ollama = pm.get_available_models("ollama")
                ollama_models = json.loads(raw_ollama)
                self.assertTrue(len(ollama_models) >= 8)
                self.assertTrue(any(m["id"] == "llama3.2:1b" for m in ollama_models))

    def test_sherpa_models_catalog_online_and_fallback(self):
        """Verifica il recupero online e il fallback locale per i modelli Sherpa-ONNX."""
        with tempfile.TemporaryDirectory() as tmpdir:
            service = ModelCatalogService(cache_dir=Path(tmpdir) / "cache", models_dir=Path(tmpdir) / "models")

            # 1. Fallback offline
            with patch("urllib.request.urlopen", side_effect=Exception("Network error")):
                models_fallback = service.get_sherpa_models(force_refresh=True)
                self.assertTrue(len(models_fallback) >= 5)
                ids = [m["id"] for m in models_fallback]
                self.assertIn("sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20", ids)
                self.assertIn("sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01", ids)
                self.assertIn("sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01", ids)

            # 2. Mock risposta online GitHub Releases
            fake_release = {
                "assets": [
                    {
                        "name": "sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20.tar.bz2",
                        "size": 32925286,
                        "browser_download_url": "https://fake.url/zh-en.tar.bz2"
                    },
                    {
                        "name": "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01.tar.bz2",
                        "size": 17616076,
                        "browser_download_url": "https://fake.url/gigaspeech.tar.bz2"
                    },
                    {
                        "name": "checksum.txt",
                        "size": 512,
                        "browser_download_url": "https://fake.url/checksum.txt"
                    }
                ]
            }
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(fake_release).encode("utf-8")
            mock_resp.__enter__.return_value = mock_resp

            with patch("urllib.request.urlopen", return_value=mock_resp):
                models_online = service.get_sherpa_models(force_refresh=True)
                self.assertEqual(len(models_online), 2)
                self.assertEqual(models_online[0]["id"], "sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20")
                self.assertIn("Bilingue", models_online[0]["lang_text"])
                self.assertNotIn("checksum.txt", [m["id"] for m in models_online])


if __name__ == "__main__":
    unittest.main()
