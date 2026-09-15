import os
os.environ["GSETTINGS_BACKEND"] = "memory"
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add src/daemon to import path
daemon_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "daemon"))
if daemon_dir not in sys.path:
    sys.path.insert(0, daemon_dir)

import tempfile
_test_cfg_dir = tempfile.TemporaryDirectory(prefix="va_test_tts_cfg_")
os.environ.setdefault("VOICE_ASSISTANT_CONFIG_DIR", _test_cfg_dir.name)

from services.tts_service import TTSServiceManager, EspeakTTSProvider, PiperTTSProvider, OpenAITTSProvider

class TestServicesTTS(unittest.TestCase):

    def setUp(self):
        super().setUp()
        try:
            from core.cloud_config import get_cloud_config
            get_cloud_config().set_provider_config("tts", "openai", {"api_key": "", "endpoint": "https://api.openai.com/v1/audio/speech"})
            get_cloud_config().set_provider_config("llm", "openai", {"api_key": ""})
        except Exception:
            pass

    def test_espeak_provider_fallback(self):
        """Verifica la sintesi tramite espeak-ng se installato nel sistema."""
        provider = EspeakTTSProvider()
        # Se espeak-ng è installato sul sistema Linux
        wav_bytes = provider.synthesize("Test vocale", voice="it", speed=1.0)
        if wav_bytes:
            self.assertTrue(len(wav_bytes) > 44, "Il file WAV generato deve contenere intestazione e dati PCM")

    def test_tts_service_manager_routing(self):
        """Verifica che TTSServiceManager route correttamente le chiamate e gestisca le impostazioni disabilitate."""
        audio_player_mock = MagicMock()
        settings_mock = {"tts-enabled": False, "tts-provider": "espeak", "tts-voice": "it", "tts-speed": 1.0}
        
        settings_observer = MagicMock()
        settings_observer.get.side_effect = lambda k, default=None: settings_mock.get(k, default)

        manager = TTSServiceManager(
            audio_player=audio_player_mock,
            settings_observer=settings_observer
        )

        # Se disabilitato nelle impostazioni, speak() restituisce False e non chiama l'audio player
        success = manager.speak("Messaggio di prova")
        self.assertFalse(success)
        audio_player_mock.play_wav_bytes.assert_not_called()

        # Abilita sintesi nelle impostazioni
        settings_mock["tts-enabled"] = True
        
        # Mokka il provider espeak
        espeak_mock = MagicMock()
        espeak_mock.synthesize.return_value = b"RIFF....WAVEfmt...."
        manager.providers["espeak"] = espeak_mock

        success = manager.speak("Messaggio di prova", provider_name="espeak")
        self.assertTrue(success)
        espeak_mock.synthesize.assert_called_once()
        audio_player_mock.play_wav_bytes.assert_called_once_with(b"RIFF....WAVEfmt....")

    def test_tts_service_manager_engine_and_voice_switch(self):
        """Verifica che cambiare tts-provider e tts-voice in settings_observer influenzi l'engine e la voce usati."""
        audio_player_mock = MagicMock()
        settings_mock = {
            "tts-enabled": True,
            "tts-provider": "piper",
            "tts-voice": "it_IT-riccardo-x_low",
            "tts-speed": 1.0,
            "language": "it",
        }
        settings_observer = MagicMock()
        settings_observer.get.side_effect = lambda k, default=None: settings_mock.get(k, default)

        manager = TTSServiceManager(
            audio_player=audio_player_mock,
            settings_observer=settings_observer,
        )

        piper_mock = MagicMock()
        piper_mock.synthesize.return_value = b"PIPER_AUDIO"
        manager.providers["piper"] = piper_mock

        espeak_mock = MagicMock()
        espeak_mock.synthesize.return_value = b"ESPEAK_AUDIO"
        manager.providers["espeak"] = espeak_mock

        system_mock = MagicMock()
        system_mock.synthesize.return_value = b"SYSTEM_AUDIO"
        manager.providers["system"] = system_mock

        # 1. Verifica che con provider piper e voce riccardo venga chiamato piper con riccardo
        ok = manager.speak("Test con Riccardo")
        self.assertTrue(ok)
        piper_mock.synthesize.assert_called_once_with("Test con Riccardo", voice="it_IT-riccardo-x_low", speed=1.0)
        audio_player_mock.play_wav_bytes.assert_called_with(b"PIPER_AUDIO")

        # 2. Cambia voce in impostazioni
        piper_mock.reset_mock()
        settings_mock["tts-voice"] = "it_IT-paola-medium"
        ok = manager.speak("Test con Paola")
        self.assertTrue(ok)
        piper_mock.synthesize.assert_called_once_with("Test con Paola", voice="it_IT-paola-medium", speed=1.0)

        # 3. Cambia engine in espeak
        settings_mock["tts-provider"] = "espeak"
        ok = manager.speak("Test con espeak")
        self.assertTrue(ok)
        espeak_mock.synthesize.assert_called_once()
        audio_player_mock.play_wav_bytes.assert_called_with(b"ESPEAK_AUDIO")

        # 4. Cambia engine in system
        settings_mock["tts-provider"] = "system"
        ok = manager.speak("Test con system")
        self.assertTrue(ok)
        system_mock.synthesize.assert_called_once()
        audio_player_mock.play_wav_bytes.assert_called_with(b"SYSTEM_AUDIO")

    def test_tts_service_manager_synthesize_method(self):
        """Verifica che synthesize() restituisca direttamente i byte audio senza riprodurli."""
        settings_mock = {"tts-enabled": True, "tts-provider": "piper", "tts-voice": "test-voice"}
        settings_observer = MagicMock()
        settings_observer.get.side_effect = lambda k, default=None: settings_mock.get(k, default)

        manager = TTSServiceManager(settings_observer=settings_observer)
        piper_mock = MagicMock()
        piper_mock.synthesize.return_value = b"SYNTH_BYTES"
        manager.providers["piper"] = piper_mock

        data = manager.synthesize("Test synthesize")
        self.assertEqual(data, b"SYNTH_BYTES")
        piper_mock.synthesize.assert_called_once_with("Test synthesize", voice="test-voice", speed=1.0)

    def test_ensure_voice_downloaded_progress_callback(self):
        """Verifica che ensure_voice_downloaded chiami il callback con 100 se i file sono già presenti."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            provider = PiperTTSProvider(models_dir=tmpdir)
            onnx_path = os.path.join(tmpdir, "test_voice.onnx")
            json_path = os.path.join(tmpdir, "test_voice.onnx.json")
            with open(onnx_path, "w") as f:
                f.write("onnx_data")
            with open(json_path, "w") as f:
                f.write("{}")

            progress_calls = []
            res_onnx, res_json = provider.ensure_voice_downloaded(
                voice_name="test_voice",
                progress_callback=lambda pct: progress_calls.append(pct)
            )
            self.assertEqual(res_onnx, onnx_path)
            self.assertEqual(res_json, json_path)
            self.assertIn(100, progress_calls)

    def test_openai_tts_routing(self):
        """Verifica che TTSServiceManager configuri e utilizzi correttamente OpenAITTSProvider."""
        from core.cloud_config import get_cloud_config
        get_cloud_config().set_provider_config("tts", "openai", {
            "api_key": "sk-test-tts-key",
            "model": "tts-1-hd",
            "voice": "nova",
        })
        settings_mock = {
            "tts-enabled": True,
            "tts-provider": "openai",
            "tts-speed": 1.25,
        }
        settings_observer = MagicMock()
        settings_observer.get.side_effect = lambda k, default=None: settings_mock.get(k, default)

        manager = TTSServiceManager(settings_observer=settings_observer)
        openai_mock = MagicMock(spec=OpenAITTSProvider)
        openai_mock.synthesize.return_value = b"OPENAI_AUDIO"
        manager.providers["openai"] = openai_mock

        audio = manager.synthesize("Hello world")
        self.assertEqual(audio, b"OPENAI_AUDIO")
        self.assertEqual(openai_mock.api_key, "sk-test-tts-key")
        self.assertEqual(openai_mock.model, "tts-1-hd")
        openai_mock.synthesize.assert_called_once_with("Hello world", voice="nova", speed=1.25)

        get_cloud_config().set_provider_config("tts", "openai", {"api_key": ""})
        get_cloud_config().set_provider_config("llm", "openai", {"api_key": ""})
        settings_mock["llm-api-key"] = "sk-test-llm-key"
        openai_mock.reset_mock()
        audio = manager.synthesize("Fallback test")
        self.assertEqual(openai_mock.api_key, "sk-test-llm-key")

    def test_openai_tts_custom_endpoint(self):
        """Verifica che OpenAITTSProvider utilizzi l'endpoint personalizzato nella richiesta HTTP."""
        provider = OpenAITTSProvider(api_key="sk-test", model="tts-1", endpoint="https://custom.openai.proxy/v1/audio/speech")
        self.assertEqual(provider.endpoint, "https://custom.openai.proxy/v1/audio/speech")

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read.return_value = b"MOCK_AUDIO"
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            data = provider.synthesize("Test endpoint")
            self.assertEqual(data, b"MOCK_AUDIO")
            mock_urlopen.assert_called_once()
            req = mock_urlopen.call_args[0][0]
            self.assertEqual(req.full_url, "https://custom.openai.proxy/v1/audio/speech")

    def test_openai_tts_endpoint_from_cloud_config(self):
        """Verifica che TTSServiceManager configuri endpoint da cloud_config per OpenAI TTS."""
        from core.cloud_config import get_cloud_config
        cfg = get_cloud_config()
        cfg.set_provider_config("tts", "openai", {"endpoint": "https://custom.tts.endpoint/speech"})

        settings_mock = {
            "tts-enabled": True,
            "tts-provider": "openai",
            "tts-cloud-voice": "alloy",
            "tts-api-key": "sk-test",
            "tts-model": "tts-1",
        }
        settings_observer = MagicMock()
        settings_observer.get.side_effect = lambda k, default=None: settings_mock.get(k, default)

        manager = TTSServiceManager(settings_observer=settings_observer)
        openai_prov = manager.providers["openai"]
        self.assertIsInstance(openai_prov, OpenAITTSProvider)
        self.assertEqual(openai_prov.endpoint, "https://custom.tts.endpoint/speech")

        with patch.object(openai_prov, "synthesize", return_value=b"AUDIO") as mock_synth:
            res = manager.synthesize("Hello")
            self.assertEqual(res, b"AUDIO")
            self.assertEqual(openai_prov.endpoint, "https://custom.tts.endpoint/speech")

    def test_ensure_voice_downloaded_streaming_progress(self):
        """Verifica che durante il download HTTP vengano emessi aggiornamenti di progresso regolari (5..100%)."""
        import tempfile
        import io
        from unittest.mock import patch, MagicMock

        with tempfile.TemporaryDirectory() as tmpdir:
            provider = PiperTTSProvider(models_dir=tmpdir)
            progress_calls = []

            # Mock risposta per il json (piccolo) e per l'onnx (a chunk)
            def mock_urlopen(req, timeout=30):
                url = req.full_url if hasattr(req, "full_url") else str(req)
                mock_resp = MagicMock()
                if url.endswith(".json"):
                    data = b'{"dataset": "test"}'
                    mock_resp.headers = {"Content-Length": str(len(data))}
                    mock_resp.read = io.BytesIO(data).read
                    mock_resp.__enter__.return_value = io.BytesIO(data)
                else:
                    # File onnx da 10 chunk da 10000 byte
                    total = 100000
                    chunks = [b"X" * 10000 for _ in range(10)]
                    it = iter(chunks)
                    mock_resp.headers = {"Content-Length": str(total)}
                    mock_resp.read.side_effect = lambda size=None: next(it, b"")
                    mock_resp.__enter__.return_value = mock_resp
                return mock_resp

            with patch("urllib.request.urlopen", side_effect=mock_urlopen):
                onnx_p, json_p = provider.ensure_voice_downloaded(
                    voice_name="it_IT-test-medium",
                    progress_callback=lambda pct: progress_calls.append(pct)
                )

            self.assertTrue(os.path.exists(onnx_p))
            self.assertTrue(os.path.exists(json_p))
            self.assertIn(5, progress_calls)
            self.assertIn(10, progress_calls)
            self.assertIn(100, progress_calls)
            # Deve esserci progressione intermedia (non bloccata a 5%)
            intermediate = [p for p in progress_calls if 10 < p < 100]
            self.assertTrue(len(intermediate) > 0)
            self.assertEqual(progress_calls, sorted(progress_calls))

    def test_ensure_voice_downloaded_cancellation(self):
        """Verifica che l'annullamento pulisca i file temporanei e sollevi InterruptedError."""
        import tempfile
        import io
        from unittest.mock import patch, MagicMock

        with tempfile.TemporaryDirectory() as tmpdir:
            provider = PiperTTSProvider(models_dir=tmpdir)

            def mock_urlopen(req, timeout=30):
                mock_resp = MagicMock()
                data = b"chunk_data"
                mock_resp.headers = {"Content-Length": "1000"}
                mock_resp.read.side_effect = [data, data, data]
                mock_resp.__enter__.return_value = io.BytesIO(b'{"dataset": "test"}') if "json" in getattr(req, "full_url", "") else mock_resp
                return mock_resp

            def cancel_progress(pct):
                if pct > 10:
                    raise InterruptedError("Cancel by user")

            with patch("urllib.request.urlopen", side_effect=mock_urlopen):
                with self.assertRaises(InterruptedError):
                    provider.ensure_voice_downloaded(
                        voice_name="it_IT-cancel-medium",
                        progress_callback=cancel_progress
                    )

            # Verifica pulizia file temporanei
            self.assertFalse(os.path.exists(os.path.join(tmpdir, "it_IT-cancel-medium.onnx.tmp")))
            self.assertFalse(os.path.exists(os.path.join(tmpdir, "it_IT-cancel-medium.onnx")))


if __name__ == '__main__':
    unittest.main()

