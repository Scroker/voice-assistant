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
            @classmethod
            def get_default_model(cls, lang: str = "it", **kwargs) -> str:
                return "dummy-model"

        provider = DummyProvider("model", "cpu", {})
        self.assertIsNotNone(provider)

    def test_whisper_provider_init(self):
        """Verifica che WhisperProvider sia istanziabile in modalità mock senza rete."""
        mock_fw = MagicMock()
        mock_ort = MagicMock()
        with patch.dict(sys.modules, {'faster_whisper': mock_fw, 'onnxruntime': mock_ort}):
            from providers.whisper_provider import WhisperProvider
            provider = WhisperProvider(model_size="tiny", hardware="cpu", extra={}, download_only=True)
            self.assertIsNotNone(provider)

    def test_vosk_provider_invalid_model_fallback(self):
        """Verifica che un modello non valido per Vosk (es. whisper-1) faccia il fallback sul modello Vosk predefinito."""
        mock_vosk = MagicMock()
        with patch.dict(sys.modules, {'vosk': mock_vosk}):
            with patch("providers.vosk_provider.KaldiRecognizer", return_value=MagicMock()):
                with patch("providers.vosk_provider.VoskProvider._load_or_download_model") as mock_load:
                    mock_load.return_value = MagicMock()
                    from providers.vosk_provider import VoskProvider
                    provider = VoskProvider(model_name="whisper-1", hardware="cpu", extra={"language": "it"})
                    self.assertIsNotNone(provider)
                    mock_load.assert_called_once()
                    target_name = mock_load.call_args[0][0]
                    self.assertEqual(target_name, "vosk-model-small-it-0.22")

    def test_whisper_provider_invalid_model_fallback(self):
        """Verifica che un modello Vosk passato a WhisperProvider faccia il fallback su 'base'."""
        mock_fw = MagicMock()
        mock_ort = MagicMock()
        with patch.dict(sys.modules, {'faster_whisper': mock_fw, 'onnxruntime': mock_ort}):
            from providers.whisper_provider import WhisperProvider
            provider = WhisperProvider(model_size="vosk-model-small-it-0.22", hardware="cpu", extra={}, download_only=True)
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

    def test_vosk_provider_get_default_model(self):
        """Verifica l'estrazione dinamica dell'ultimo modello Vosk 'small' per lingua."""
        from providers.vosk_provider import VoskProvider

        fake_models = [
            {"id": "vosk-model-it-0.22", "lang": "it", "type": "big"},
            {"id": "vosk-model-small-it-0.4", "lang": "it", "type": "small"},
            {"id": "vosk-model-small-it-0.22", "lang": "it", "type": "small"},
            {"id": "vosk-model-small-en-us-0.15", "lang": "en", "type": "small"},
            {"id": "vosk-model-en-us-0.22", "lang": "en", "type": "big"},
        ]
        with patch.object(VoskProvider, "get_available_models", return_value=fake_models):
            self.assertEqual(VoskProvider.get_default_model("it"), "vosk-model-small-it-0.22")
            self.assertEqual(VoskProvider.get_default_model("it_IT"), "vosk-model-small-it-0.22")
            self.assertEqual(VoskProvider.get_default_model("en"), "vosk-model-small-en-us-0.15")
            self.assertEqual(VoskProvider.get_default_model("en_US"), "vosk-model-small-en-us-0.15")

    def test_whisper_provider_get_default_model(self):
        """Verifica che WhisperProvider ritorni il modello raccomandato/bilanciato (base)."""
        from providers.whisper_provider import WhisperProvider
        self.assertEqual(WhisperProvider.get_default_model("it"), "base")
        self.assertEqual(WhisperProvider.get_default_model("en"), "base")

    def test_cloud_stt_provider_get_default_model(self):
        """Verifica il mapping dinamico per provider cloud OpenAI e Groq."""
        from providers.openai_cloud_provider import OpenAICloudSTTProvider
        self.assertEqual(OpenAICloudSTTProvider.get_default_model(provider="openai_cloud"), "whisper-1")
        self.assertEqual(OpenAICloudSTTProvider.get_default_model(provider="groq_cloud"), "whisper-large-v3")

    def test_providers_module_get_default_model_dispatcher(self):
        """Verifica che il modulo providers deleghi correttamente la risoluzione dei default."""
        import providers
        self.assertTrue(providers.get_default_model("vosk", "it").startswith("vosk-model-small-it-"))
        self.assertEqual(providers.get_default_model("whisper"), "base")
        self.assertEqual(providers.get_default_model("openai_cloud"), "whisper-1")
        self.assertEqual(providers.get_default_model("groq_cloud"), "whisper-large-v3")

    def test_vosk_provider_multi_language_support(self):
        """Verifica che VoskProvider risolva modelli predefiniti per lingue arbitrarie (es. fr, de)."""
        from providers.vosk_provider import VoskProvider

        fake_models = [
            {"id": "vosk-model-small-fr-0.22", "lang": "fr", "type": "small"},
            {"id": "vosk-model-small-de-0.15", "lang": "de", "type": "small"},
            {"id": "vosk-model-de-0.21", "lang": "de", "type": "big"},
        ]
        with patch.object(VoskProvider, "get_available_models", return_value=fake_models):
            self.assertEqual(VoskProvider.get_default_model("fr"), "vosk-model-small-fr-0.22")
            self.assertEqual(VoskProvider.get_default_model("fr_FR"), "vosk-model-small-fr-0.22")
            self.assertEqual(VoskProvider.get_default_model("de"), "vosk-model-small-de-0.15")
            self.assertEqual(VoskProvider.get_default_model("de_DE"), "vosk-model-small-de-0.15")

    def test_whisper_transcribe_passes_configured_language(self):
        """Verifica che WhisperProvider passi la lingua configurata al metodo transcribe."""
        mock_fw = MagicMock()
        mock_ort = MagicMock()
        with patch.dict(sys.modules, {'faster_whisper': mock_fw, 'onnxruntime': mock_ort}):
            from providers.whisper_provider import WhisperProvider
            provider = WhisperProvider(model_size="base", hardware="cpu", extra={"language": "de_DE"}, download_only=True)
            self.assertEqual(provider.language, "de")

            mock_model = MagicMock()
            mock_model.transcribe.return_value = ([], None)
            provider.model = mock_model
            provider.audio_buffer.extend(b"\x00\x00" * 16000)

            provider.flush_and_transcribe()
            mock_model.transcribe.assert_called_once()
            _, kwargs = mock_model.transcribe.call_args
            self.assertEqual(kwargs.get("language"), "de")

    def test_piper_tts_dynamic_voice_path(self):
        """Verifica la risoluzione deterministica dei percorsi Hugging Face per voci Piper arbitrarie."""
        from services.tts_service import PiperTTSProvider

        onnx_rel, json_rel = PiperTTSProvider.get_hf_voice_path("fr_FR-siwis-low")
        self.assertEqual(onnx_rel, "fr/fr_FR/siwis/low/fr_FR-siwis-low.onnx")
        self.assertEqual(json_rel, "fr/fr_FR/siwis/low/fr_FR-siwis-low.onnx.json")

        onnx_de, json_de = PiperTTSProvider.get_hf_voice_path("de_DE-thorsten-high")
        self.assertEqual(onnx_de, "de/de_DE/thorsten/high/de_DE-thorsten-high.onnx")
        self.assertEqual(json_de, "de/de_DE/thorsten/high/de_DE-thorsten-high.onnx.json")

    def test_get_default_model_auto_system_language(self):
        """Verifica che get_default_model rilevi automaticamente la lingua di sistema se non fornita."""
        import providers
        from providers.vosk_provider import VoskProvider

        fake_models = [
            {"id": "vosk-model-small-fr-0.22", "lang": "fr", "type": "small"},
            {"id": "vosk-model-small-it-0.22", "lang": "it", "type": "small"},
        ]
        with patch.object(VoskProvider, "get_available_models", return_value=fake_models):
            with patch("core.locale_utils.get_system_language", return_value="fr"):
                self.assertEqual(providers.get_default_model("vosk"), "vosk-model-small-fr-0.22")
            with patch("core.locale_utils.get_system_language", return_value="it"):
                self.assertEqual(providers.get_default_model("vosk"), "vosk-model-small-it-0.22")

    def test_piper_tts_default_voice(self):
        """Verifica la risoluzione della voce predefinita per lingua o sistema."""
        from services.tts_service import PiperTTSProvider

        self.assertEqual(PiperTTSProvider.get_default_voice("it"), "it_IT-paola-medium")
        self.assertEqual(PiperTTSProvider.get_default_voice("en"), "en_US-lessac-medium")
        self.assertEqual(PiperTTSProvider.get_default_voice("fr"), "fr_FR-siwis-medium")
        self.assertEqual(PiperTTSProvider.get_default_voice("de"), "de_DE-thorsten-medium")
        with patch("core.locale_utils.get_system_language", return_value="de"):
            self.assertEqual(PiperTTSProvider.get_default_voice(), "de_DE-thorsten-medium")

if __name__ == '__main__':
    unittest.main()

