import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

daemon_dir = Path(__file__).resolve().parent.parent / 'src' / 'daemon'
sys.path.insert(0, str(daemon_dir))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

try:
    from daemon.core.runtime_manager import DaemonRuntimeManager
    from daemon.core.assistant_runtime import AssistantRuntimeController
    from daemon.core.logger import ErrorCollector, ERROR_REPORTS_DIR
except ImportError:
    from core.runtime_manager import DaemonRuntimeManager
    from core.assistant_runtime import AssistantRuntimeController
    from core.logger import ErrorCollector, ERROR_REPORTS_DIR


class DummyOwner:
    def __init__(self):
        self.wakeword_engine = "vosk"
        self.oww_model_name = "alexa"
        self.oww_model_instance = None
        self._oww_buffer = []
        self.sherpa_spotter = None
        self.sherpa_stream = None
        self.sherpa_ww_model_dir = ""
        self.wakeword = "assistente"
        self.vosk_ww_model = "vosk-model-small-it-0.22"
        self.models_dir = "/tmp/test_models"
        self.ww_provider = None
        self.ww_model = None
        self.ww_recognizer = None
        self._state = "idle"
        self._missing_deps = []
        self.settings = MagicMock()
        self.settings.get_string.return_value = "vosk"
        self.DownloadProgress = MagicMock()
        self._listening_start_time = None
        self._last_speech_time = None
        self._last_partial_text = ""
        self._last_partial_change_time = None
        self._ignore_audio_until = 0
        self.q = MagicMock()
        self.q.empty.return_value = True
        self.set_state = MagicMock(side_effect=lambda s: setattr(self, '_state', s))
        self.audio_player = MagicMock()

    def notify_dependency_required(self, package, description, is_critical, dep_type="pip", system_packages=None, mcp_server=None):
        self._missing_deps.append({
            "package": package,
            "description": description,
            "is_critical": is_critical,
        })


class TestWakeWordRuntime(unittest.TestCase):

    def setUp(self):
        self.owner = DummyOwner()
        self.runtime = DaemonRuntimeManager(self.owner)
        self.owner.runtime_manager = self.runtime

    @patch("core.runtime_manager.notify2")
    def test_load_oww_success(self, mock_notify2):
        fake_paths = ["/opt/models/alexa_v0.1.onnx", "/opt/models/hey_jarvis_v0.1.onnx"]
        mock_oww = MagicMock()
        mock_oww.get_pretrained_model_paths.return_value = fake_paths
        mock_model_cls = MagicMock()

        with patch.dict(sys.modules, {"openwakeword": mock_oww, "openwakeword.model": MagicMock(Model=mock_model_cls)}):
            self.owner.oww_model_name = "alexa"
            self.runtime._load_oww()

            self.assertIsNotNone(self.owner.oww_model_instance)
            mock_model_cls.assert_called_once_with(
                wakeword_model_paths=["/opt/models/alexa_v0.1.onnx"],
                inference_framework="onnx",
            )
            self.assertEqual(self.owner._oww_buffer, [])

    @patch("core.runtime_manager.notify2")
    def test_load_oww_loads_from_centralized_models_dir(self, mock_notify2):
        """Verifica che _load_oww carichi prioritariamente il modello dalla cartella modelli wakeword/openwakeword."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            from core.path_utils import get_oww_models_dir
            oww_dir = get_oww_models_dir(tmpdir)
            target_model = oww_dir / "alexa_v0.1.onnx"
            target_model.write_bytes(b"dummy")

            mock_oww = MagicMock()
            mock_oww.get_pretrained_model_paths.return_value = ["/other/path/alexa_v0.1.onnx"]
            mock_model_cls = MagicMock()

            with patch.dict(sys.modules, {"openwakeword": mock_oww, "openwakeword.model": MagicMock(Model=mock_model_cls)}):
                self.owner.models_dir = tmpdir
                self.owner.oww_model_name = "alexa"
                self.runtime._load_oww()

                self.assertIsNotNone(self.owner.oww_model_instance)
                mock_model_cls.assert_called_once_with(
                    wakeword_model_paths=[str(target_model)],
                    inference_framework="onnx",
                )
                self.assertEqual(self.owner._oww_buffer, [])

    @patch("core.runtime_manager.notify2")
    def test_load_oww_legacy_openwakeword_success(self, mock_notify2):
        """openWakeWord <= 0.4.0 solleva TypeError se si passa inference_framework; verifica il fallback a Model(wakeword_model_paths=...)."""
        fake_paths = ["/opt/models/alexa_v0.1.onnx"]
        mock_oww = MagicMock()
        mock_oww.get_pretrained_model_paths.return_value = fake_paths

        created_instance = MagicMock()
        def mock_model_init(*args, **kwargs):
            if "inference_framework" in kwargs:
                raise TypeError("AudioFeatures.__init__() got an unexpected keyword argument 'inference_framework'")
            return created_instance

        mock_model_cls = MagicMock(side_effect=mock_model_init)

        with patch.dict(sys.modules, {"openwakeword": mock_oww, "openwakeword.model": MagicMock(Model=mock_model_cls)}):
            self.owner.oww_model_name = "alexa"
            self.runtime._load_oww()

            self.assertEqual(self.owner.oww_model_instance, created_instance)
            self.assertEqual(mock_model_cls.call_count, 2)
            mock_model_cls.assert_called_with(
                wakeword_model_paths=["/opt/models/alexa_v0.1.onnx"],
            )
            self.assertEqual(self.owner._oww_buffer, [])

    @patch.object(DaemonRuntimeManager, "fallback_to_vosk_wakeword")
    def test_load_oww_attribute_error_falls_back(self, mock_fallback):
        """Riproduce esattamente l'errore sollevato dall'utente e verifica il fallback."""
        mock_oww = MagicMock()
        mock_oww.utils = MagicMock()
        del mock_oww.utils.download_models
        mock_oww.get_pretrained_model_paths.side_effect = AttributeError("module 'openwakeword.utils' has no attribute 'download_models'")

        with patch.dict(sys.modules, {"openwakeword": mock_oww, "openwakeword.model": MagicMock()}):
            self.runtime._load_oww()

            mock_fallback.assert_called_once()
            args, kwargs = mock_fallback.call_args
            self.assertIn("download_models", str(kwargs.get("exc", "")))
            self.assertIsNone(self.owner.oww_model_instance)

    @patch.object(DaemonRuntimeManager, "fallback_to_vosk_wakeword")
    def test_load_oww_missing_model_falls_back(self, mock_fallback):
        mock_oww = MagicMock()
        mock_oww.get_pretrained_model_paths.return_value = ["/opt/models/hey_jarvis_v0.1.onnx"]

        with patch.dict(sys.modules, {"openwakeword": mock_oww, "openwakeword.model": MagicMock()}):
            self.owner.oww_model_name = "non_existent_model"
            self.runtime._load_oww()

            mock_fallback.assert_called_once()
            self.assertIsNone(self.owner.oww_model_instance)

    @patch.object(DaemonRuntimeManager, "fallback_to_vosk_wakeword")
    def test_load_oww_import_error_falls_back_and_notifies_dep(self, mock_fallback):
        with patch.dict(sys.modules, {"openwakeword": None}):
            with patch("builtins.__import__", side_effect=ImportError("No module named 'openwakeword'")):
                self.runtime._load_oww()

                self.assertTrue(any(d["package"] == "openwakeword" for d in self.owner._missing_deps))
                mock_fallback.assert_called_once()

    @patch.object(DaemonRuntimeManager, "fallback_to_vosk_wakeword")
    def test_load_sherpa_ww_missing_dep_falls_back(self, mock_fallback):
        with patch.dict(sys.modules, {"sherpa_onnx": None}):
            with patch("builtins.__import__", side_effect=ImportError("No module named 'sherpa_onnx'")):
                self.runtime._load_sherpa_ww()

                self.assertTrue(any(d["package"] == "sherpa-onnx" for d in self.owner._missing_deps))
                mock_fallback.assert_called_once()

    @patch.object(DaemonRuntimeManager, "fallback_to_vosk_wakeword")
    def test_load_sherpa_ww_missing_files_falls_back(self, mock_fallback):
        mock_sherpa = MagicMock()
        with patch.dict(sys.modules, {"sherpa_onnx": mock_sherpa}):
            self.owner.sherpa_ww_model_dir = "/tmp/non_existent_sherpa_dir"
            with patch("os.path.isfile", return_value=False):
                self.runtime._load_sherpa_ww()
                mock_fallback.assert_called_once()
                self.assertIsNone(self.owner.sherpa_spotter)

    @patch.object(DaemonRuntimeManager, "_download_sherpa_kws_model")
    def test_load_sherpa_ww_downloads_default_model_when_missing(self, mock_download):
        mock_sherpa = MagicMock()
        mock_spotter = MagicMock()
        mock_sherpa.KeywordSpotter.return_value = mock_spotter

        # Prima chiamata a _find_sherpa_files: file mancanti.
        # Seconda chiamata (dopo download): file presenti.
        found_files = ("/tmp/enc.onnx", "/tmp/dec.onnx", "/tmp/join.onnx", "/tmp/tokens.txt")
        missing_files = (None, None, None, None)

        with patch.dict(sys.modules, {"sherpa_onnx": mock_sherpa}):
            self.owner.sherpa_ww_model_dir = ""
            with patch.object(self.runtime, "_find_sherpa_files", side_effect=[missing_files, found_files]):
                with patch("builtins.open", unittest.mock.mock_open()):
                    self.runtime._load_sherpa_ww()

                    mock_download.assert_called_once()
                    self.assertEqual(self.owner.sherpa_spotter, mock_spotter)
                    self.assertIsNotNone(self.owner.sherpa_stream)

    @patch.object(DaemonRuntimeManager, "notify_user")
    @patch.object(DaemonRuntimeManager, "_load_vosk_ww")
    @patch("core.logger.ErrorCollector.record_error")
    def test_fallback_to_vosk_wakeword_actions(self, mock_record_error, mock_load_vosk, mock_notify):
        self.owner.wakeword_engine = "openwakeword"
        self.owner.settings.get_string.return_value = "openwakeword"
        err = ValueError("Model init failed")

        self.runtime.fallback_to_vosk_wakeword("Errore caricamento OpenWakeWord", exc=err)

        # 1. ErrorCollector chiamato
        mock_record_error.assert_called_once()
        self.assertEqual(mock_record_error.call_args[1].get("severity"), "ERROR")
        self.assertEqual(mock_record_error.call_args[1].get("component"), "VoiceAssistant.WakeWord")

        # 2. Notifica mostrata
        mock_notify.assert_called_once()
        self.assertIn("Errore Wake Word", mock_notify.call_args[0][0])

        # 3. Stato aggiornato a 'vosk'
        self.assertEqual(self.owner.wakeword_engine, "vosk")
        self.assertIsNone(self.owner.oww_model_instance)

        # 4. GSettings aggiornato
        self.owner.settings.set_string.assert_called_with("wakeword-engine", "vosk")

        # 5. Inizializzazione Vosk avviata
        mock_load_vosk.assert_called_once()

    @patch.object(DaemonRuntimeManager, "notify_user")
    @patch("core.logger.ErrorCollector.record_error")
    def test_load_vosk_ww_failure_records_critical(self, mock_record_error, mock_notify):
        with patch("providers.vosk_provider.VoskProvider", side_effect=RuntimeError("Vosk corrupted")):
            self.runtime._load_vosk_ww()

            mock_record_error.assert_called_once()
            self.assertEqual(mock_record_error.call_args[1].get("severity"), "CRITICAL")
            mock_notify.assert_called_once()
            self.assertIsNone(self.owner.ww_recognizer)


class TestWakeWordPrediction(unittest.TestCase):

    def setUp(self):
        self.owner = DummyOwner()
        self.runtime = DaemonRuntimeManager(self.owner)
        self.owner.runtime_manager = self.runtime
        self.controller = AssistantRuntimeController(self.owner)
        self.controller.trigger_assistant = MagicMock()

    def test_check_oww_wakeword_matches_versioned_key(self):
        """Verifica che un modello con prefisso/versione (es. 'alexa_v0.1') corrisponda a 'alexa'."""
        mock_oww = MagicMock()
        mock_oww.predict.return_value = {"alexa_v0.1": 0.88}
        self.owner.oww_model_instance = mock_oww
        self.owner.oww_model_name = "alexa"

        raw_pcm = b"\x00\x00" * 1280
        self.controller._check_oww_wakeword(raw_pcm)

        mock_oww.predict.assert_called_once()
        self.controller.trigger_assistant.assert_called_once()

    def test_check_oww_wakeword_below_threshold_does_not_trigger(self):
        mock_oww = MagicMock()
        mock_oww.predict.return_value = {"alexa_v0.1": 0.25}
        self.owner.oww_model_instance = mock_oww
        self.owner.oww_model_name = "alexa"

        raw_pcm = b"\x00\x00" * 1280
        self.controller._check_oww_wakeword(raw_pcm)

        mock_oww.predict.assert_called_once()
        self.controller.trigger_assistant.assert_not_called()

    def test_check_sherpa_wakeword_triggers_on_keyword(self):
        mock_spotter = MagicMock()
        mock_stream = MagicMock()
        mock_spotter.is_ready.side_effect = [True, False]
        mock_spotter.get_result.return_value = "anthon"

        self.owner.sherpa_spotter = mock_spotter
        self.owner.sherpa_stream = mock_stream

        raw_pcm = b"\x00\x00" * 1024
        self.controller._check_sherpa_wakeword(raw_pcm)

        mock_stream.accept_waveform.assert_called_once()
        mock_spotter.decode_stream.assert_called_once_with(mock_stream)
        mock_spotter.reset_stream.assert_called_once_with(mock_stream)
        self.controller.trigger_assistant.assert_called_once()

    def test_check_sherpa_wakeword_empty_result_does_not_trigger(self):
        mock_spotter = MagicMock()
        mock_stream = MagicMock()
        mock_spotter.is_ready.side_effect = [True, False]
        mock_spotter.get_result.return_value = ""

        self.owner.sherpa_spotter = mock_spotter
        self.owner.sherpa_stream = mock_stream

        raw_pcm = b"\x00\x00" * 1024
        self.controller._check_sherpa_wakeword(raw_pcm)

        mock_stream.accept_waveform.assert_called_once()
        mock_spotter.decode_stream.assert_called_once_with(mock_stream)
        mock_spotter.reset_stream.assert_not_called()
        self.controller.trigger_assistant.assert_not_called()

    def test_reset_oww_resets_buffers_and_model(self):
        """Verifica che il reset di OpenWakeWord svuoti sia il buffer audio sia i buffer interni del preprocessor."""
        import numpy as np
        mock_oww = MagicMock()
        mock_prep = MagicMock()
        mock_prep.raw_data_buffer = [1, 2, 3]
        mock_prep.accumulated_samples = 42
        mock_prep.melspectrogram_buffer = np.zeros((76, 32))
        mock_prep.feature_buffer = np.ones((116, 96), dtype=np.float32)
        mock_oww.preprocessor = mock_prep

        self.owner.wakeword_engine = "openwakeword"
        self.owner.oww_model_instance = mock_oww
        self.owner._oww_buffer = [100, 200, 300]

        self.controller.reset_wakeword_recognizer()

        self.assertEqual(self.owner._oww_buffer, [])
        mock_oww.reset.assert_called_once()
        self.assertEqual(mock_prep.accumulated_samples, 0)
        self.assertEqual(mock_prep.feature_buffer.shape, (116, 96))
        self.assertTrue(np.all(mock_prep.feature_buffer == 0))

    def test_reset_sherpa_resets_stream(self):
        """Verifica che il reset di Sherpa-ONNX azzeri lo stream attivo."""
        mock_spotter = MagicMock()
        mock_stream = MagicMock()
        self.owner.wakeword_engine = "sherpa-onnx"
        self.owner.sherpa_spotter = mock_spotter
        self.owner.sherpa_stream = mock_stream

        self.controller.reset_wakeword_recognizer()

        mock_spotter.reset_stream.assert_called_once_with(mock_stream)

    def test_on_settings_changed_wakeword_reloads_sherpa(self):
        """Verifica che cambiare la wakeword con sherpa-onnx attivo re-inizializzi il motore."""
        self.owner.wakeword_engine = "sherpa-onnx"
        mock_runtime = MagicMock()
        self.owner.runtime_manager = mock_runtime
        mock_settings = MagicMock()
        mock_settings.get_string.return_value = "nuovaparola"

        self.controller.on_settings_changed(mock_settings, "wakeword")

        self.assertEqual(self.owner.wakeword, "nuovaparola")
        self.assertIsNone(self.owner.sherpa_spotter)
        self.assertIsNone(self.owner.sherpa_stream)
        mock_runtime.initialize_wakeword.assert_called_once()

    def test_encode_sherpa_keyword_phonetic_variants(self):
        """Verifica che 'anthon' generi sia la forma TH che la forma fonetica T."""
        fake_tokens = "tokens.txt"
        with patch("builtins.open", unittest.mock.mock_open(read_data="AN 1\nTH 2\nON 3\nT 4\n")):
            with patch("os.path.isfile", return_value=True):
                res = self.owner.runtime_manager._encode_sherpa_keyword("anthon", fake_tokens)
                lines = res.split("\n")
                self.assertGreaterEqual(len(lines), 2)
                # Verifica presenza di AN TH ON e della variante AN T ON
                self.assertTrue(any("TH" in line for line in lines))
                self.assertTrue(any("T" in line and "TH" not in line for line in lines))

    def test_sherpa_model_path_uses_sherpa_model_setting(self):
        """Verifica che _load_sherpa_ww utilizzi il nome del modello in self.owner.sherpa_model."""
        mock_sherpa = MagicMock()
        mock_spotter = MagicMock()
        mock_sherpa.KeywordSpotter.return_value = mock_spotter

        self.owner.wakeword_engine = "sherpa-onnx"
        self.owner.sherpa_model = "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01"
        self.owner.sherpa_ww_model_dir = ""

        with patch.dict(sys.modules, {"sherpa_onnx": mock_sherpa}):
            with patch.object(self.runtime, "_find_sherpa_files", return_value=("/p/enc.onnx", "/p/dec.onnx", "/p/join.onnx", "/p/tok.txt")):
                with patch("builtins.open", unittest.mock.mock_open()):
                    self.runtime._load_sherpa_ww()

        self.assertIsNotNone(self.owner.sherpa_spotter)

    def test_settings_changed_vosk_ww_and_sherpa(self):
        """Verifica che on_settings_changed gestisca 'vosk-ww-model' e 'sherpa-model'."""
        controller = AssistantRuntimeController(self.owner)
        self.owner.runtime_manager = MagicMock()

        # 1. Test vosk-ww-model
        self.owner.wakeword_engine = "vosk"
        mock_settings = MagicMock()
        mock_settings.get_string.return_value = "vosk-model-en-us-0.22"
        controller.on_settings_changed(mock_settings, "vosk-ww-model")

        self.assertEqual(self.owner.vosk_ww_model, "vosk-model-en-us-0.22")
        self.owner.runtime_manager.initialize_wakeword.assert_called_once()

        # 2. Test sherpa-model
        self.owner.wakeword_engine = "sherpa-onnx"
        self.owner.runtime_manager.initialize_wakeword.reset_mock()
        mock_settings.get_string.return_value = "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01"
        controller.on_settings_changed(mock_settings, "sherpa-model")

        self.assertEqual(self.owner.sherpa_model, "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01")
        self.owner.runtime_manager.initialize_wakeword.assert_called_once()


class TestGuiRadioGroupSync(unittest.TestCase):

    def test_bind_radio_group_bidirectional_sync(self):
        try:
            from gui.components.settings.base import bind_radio_group
        except ImportError:
            from src.gui.components.settings.base import bind_radio_group

        callbacks = {}
        settings_store = {"wakeword-engine": "openwakeword"}

        mock_settings = MagicMock()
        mock_settings.get_string.side_effect = lambda k: settings_store.get(k, "")
        mock_settings.set_string.side_effect = lambda k, v: settings_store.update({k: v})

        def fake_connect(signal, cb):
            callbacks[signal] = cb

        mock_settings.connect.side_effect = fake_connect

        radio_states = {
            "ww_engine_vosk_radio": False,
            "ww_engine_oww_radio": False,
            "ww_engine_sherpa_radio": False,
        }
        radio_objs = {}
        radio_callbacks = {}

        for wid in radio_states:
            r = MagicMock()
            r.get_active.side_effect = lambda wid=wid: radio_states[wid]

            def make_set_active(wid):
                def _set_active(val):
                    radio_states[wid] = val
                    if "notify::active" in radio_callbacks.get(wid, {}):
                        radio_callbacks[wid]["notify::active"](r, None)
                return _set_active

            r.set_active.side_effect = make_set_active(wid)

            def make_connect(wid):
                def _r_connect(sig, cb):
                    radio_callbacks.setdefault(wid, {})[sig] = cb
                return _r_connect

            r.connect.side_effect = make_connect(wid)
            radio_objs[wid] = r

        mock_builder = MagicMock()
        mock_builder.get_object.side_effect = lambda wid: radio_objs.get(wid)

        mapping = {
            "ww_engine_vosk_radio": "vosk",
            "ww_engine_oww_radio": "openwakeword",
            "ww_engine_sherpa_radio": "sherpa-onnx",
        }

        # 1. Inizializzazione binding: deve attivare radio OWW
        bind_radio_group(mock_settings, "wakeword-engine", mock_builder, mapping)
        self.assertTrue(radio_states["ww_engine_oww_radio"])
        self.assertFalse(radio_states["ww_engine_vosk_radio"])

        # 2. Simulazione fallback o cambio esterno di GSettings a 'vosk'
        settings_store["wakeword-engine"] = "vosk"
        self.assertIn("changed::wakeword-engine", callbacks)
        callbacks["changed::wakeword-engine"](mock_settings, "wakeword-engine")

        # La UI si aggiorna automaticamente a vosk!
        self.assertTrue(radio_states["ww_engine_vosk_radio"])


if __name__ == "__main__":
    unittest.main()
