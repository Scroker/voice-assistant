import asyncio
import time
import unittest
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


daemon_dir = Path(__file__).resolve().parent.parent / 'src' / 'daemon'
sys.path.insert(0, str(daemon_dir))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

try:
    from daemon.core.lifecycle import DaemonLifecycle
    from daemon.core.provider_manager import ProviderManager
    from daemon.core.runtime_manager import DaemonRuntimeManager
except ImportError:
    from core.lifecycle import DaemonLifecycle
    from core.provider_manager import ProviderManager
    from core.runtime_manager import DaemonRuntimeManager


class DummyOwner:
    def __init__(self):
        self._state = "idle"
        self._downloading_models = {}
        self._cancel_requests = set()
        self._active_notifs = {}
        self._load_id = 1
        self.provider_name = "vosk"
        self.model_name = "vosk-model-small-it-0.22"
        self.hardware = "cpu"
        self.extra_config = {}
        self.models_dir = "/tmp"
        self.settings = MagicMock()
        self.settings.get_boolean.return_value = True
        self.provider = None
        self._inhibitor = MagicMock()
        self.q = MagicMock()
        self.q.empty.return_value = True
        self._stream = MagicMock()
        self._stream.active = False
        self._settings_observer = MagicMock()
        self._settings_observer.get.side_effect = lambda key, default=None: default
        self.on_settings_changed = MagicMock()

        self._missing_deps = []
        self._deps_notif = None
        self.ShowWindow = MagicMock()
        self._launch_gui = MagicMock()

    def notify_dependency_required(self, package, description, is_critical, dep_type="pip", system_packages=None, mcp_server=None):
        self._missing_deps.append({
            "package": package,
            "description": description,
            "is_critical": is_critical,
            "type": dep_type,
            "system_packages": system_packages or {},
            "mcp_server": mcp_server,
        })

    def set_state(self, state):
        self._state = state

    def emit_download_progress(self, provider, model_name, percent):
        return None

    def _close_stream(self):
        self._stream = None

    def _create_stream(self):
        self._stream = MagicMock()
        self._stream.active = False

    def _start_speaking_watchdog(self):
        return None

    def _on_playback_finished(self):
        return None

    def _report_initial_context(self):
        return None


class TestCoreRuntimeModules(unittest.TestCase):

    def test_get_boolean_setting_falls_back_on_stale_schema(self):
        """Una chiave assente dallo schema installato non deve abortire il processo."""
        from core.settings import get_boolean_setting

        class _Schema:
            def __init__(self, keys):
                self._keys = set(keys)

            def has_key(self, key):
                return key in self._keys

        class _Settings:
            def __init__(self, values):
                self._values = values
                self.settings_schema = _Schema(values.keys())

            def get_boolean(self, key):
                return self._values[key]

        settings = _Settings({"fast-path-enabled": True})
        self.assertTrue(get_boolean_setting(settings, "fast-path-enabled", False))
        # Chiave non presente nello schema: ritorna il default invece di crashare.
        self.assertTrue(get_boolean_setting(settings, "medium-path-enabled", True))
        self.assertFalse(get_boolean_setting(settings, "medium-path-enabled", False))
        self.assertTrue(get_boolean_setting(None, "fast-path-enabled", True))

    def test_provider_manager_load_provider_sets_owner_provider(self):
        owner = DummyOwner()
        manager = ProviderManager(owner)

        with patch('providers.get_provider') as mock_get_provider, \
             patch('notify2.Notification') as mock_notification:
            provider = MagicMock()
            mock_get_provider.return_value = provider
            mock_notification.return_value = MagicMock()

            result = manager.load_provider(1)

            self.assertIs(result, provider)
            self.assertIs(owner.provider, provider)
            self.assertNotIn('vosk:vosk-model-small-it-0.22', owner._downloading_models)

    def test_load_provider_progress_skips_unchanged_percent(self):
        """La notifica di avanzamento non va riaggiornata quando la percentuale non cambia."""
        owner = DummyOwner()
        owner.emit_download_progress = MagicMock()
        manager = ProviderManager(owner)

        captured = {}

        def _fake_get_provider(*args, **kwargs):
            captured["cb"] = kwargs.get("progress_callback") or (args[4] if len(args) > 4 else None)
            return MagicMock()

        with patch("notify2.Notification") as mock_notif_cls, \
             patch("providers.get_provider", side_effect=_fake_get_provider), \
             patch("gi.repository.GLib.idle_add", side_effect=lambda cb, *a: cb(*a)):
            mock_notif = MagicMock()
            mock_notif._is_closed = False
            mock_notif_cls.return_value = mock_notif

            manager.load_provider(1)
            progress_cb = captured.get("cb")
            self.assertIsNotNone(progress_cb, "load_provider deve passare un progress_callback a get_provider")

            mock_notif.update.reset_mock()
            owner.emit_download_progress.reset_mock()

            for pct in (10, 10, 10, 11, 11, 12):
                progress_cb(pct)

            self.assertEqual(
                mock_notif.update.call_count, 3,
                "La notifica deve essere aggiornata una sola volta per ogni percentuale distinta",
            )
            self.assertEqual(
                owner.emit_download_progress.call_count, 3,
                "In rapida successione il segnale non va ripetuto a percentuale invariata",
            )

            # Trascorso il battito, il segnale riparte anche senza avanzamento (così una GUI
            # aperta a download già in corso si riallinea), ma la notifica resta invariata.
            mock_notif.update.reset_mock()
            owner.emit_download_progress.reset_mock()
            with patch("time.monotonic", return_value=time.monotonic() + 60):
                progress_cb(12)

            self.assertEqual(owner.emit_download_progress.call_count, 1)
            self.assertEqual(
                mock_notif.update.call_count, 0,
                "Il battito del segnale non deve riscrivere la notifica",
            )

    def test_provider_manager_delete_model(self):
        import tempfile
        import os
        owner = DummyOwner()
        manager = ProviderManager(owner)

        with tempfile.TemporaryDirectory() as tmpdir:
            owner.models_dir = tmpdir
            tts_dir = os.path.join(tmpdir, "tts")
            os.makedirs(tts_dir, exist_ok=True)
            onnx_file = os.path.join(tts_dir, "it_IT-riccardo-x_low.onnx")
            json_file = os.path.join(tts_dir, "it_IT-riccardo-x_low.onnx.json")
            with open(onnx_file, "w") as f:
                f.write("dummy")
            with open(json_file, "w") as f:
                f.write("{}")

            self.assertTrue(os.path.exists(onnx_file))
            self.assertTrue(os.path.exists(json_file))

            res = manager.delete_model("piper", "it_IT-riccardo-x_low")
            self.assertTrue(res)
            self.assertFalse(os.path.exists(onnx_file))
            self.assertFalse(os.path.exists(json_file))

    def test_provider_manager_handles_missing_settings_observer(self):
        owner = DummyOwner()
        delattr(owner, '_settings_observer')
        manager = ProviderManager(owner)

        with patch('providers.get_provider') as mock_get_provider, \
             patch('notify2.Notification') as mock_notification:
            provider = MagicMock()
            mock_get_provider.return_value = provider
            mock_notification.return_value = MagicMock()

            result = manager.load_provider(1)

            self.assertIs(result, provider)
            self.assertIs(owner.provider, provider)

    def test_lifecycle_set_state_remains_idempotent(self):
        owner = DummyOwner()
        lifecycle = DaemonLifecycle(owner)
        calls = []
        owner.StateChanged = lambda state: calls.append(state)
        owner._inhibitor.inhibit = MagicMock()
        owner._inhibitor.uninhibit = MagicMock()
        owner.q.empty = lambda: True

        with patch('gi.repository.GLib.idle_add', side_effect=lambda cb: cb()):
            lifecycle.set_state('listening')
            lifecycle.set_state('listening')
            lifecycle.set_state('idle')

        self.assertEqual(calls, ['listening', 'idle'])
        self.assertEqual(owner._state, 'idle')

    def test_runtime_manager_initialize_services_builds_dependencies(self):
        owner = DummyOwner()
        owner.settings = MagicMock()
        owner.settings.get_boolean.return_value = True
        runtime = DaemonRuntimeManager(owner)
        original_asyncio_run = asyncio.run

        with patch('daemon.core.runtime_manager.AudioPlayer') as mock_audio_player, \
             patch('daemon.core.runtime_manager.TTSServiceManager') as mock_tts_service, \
             patch('daemon.core.runtime_manager.LLMServiceManager') as mock_llm_service, \
             patch('daemon.core.runtime_manager.MCPManager', create=True) as mock_mcp_manager, \
             patch('daemon.core.runtime_manager.asyncio.run', side_effect=lambda coro: original_asyncio_run(coro)):
            mock_audio_instance = MagicMock()
            mock_audio_player.return_value = mock_audio_instance
            mock_mcp_manager_instance = MagicMock()
            mock_mcp_manager_instance.initialize = AsyncMock(return_value=None)
            mock_mcp_manager.return_value = mock_mcp_manager_instance

            runtime.initialize_services()

            mock_audio_player.assert_called_once_with(on_playback_finished=owner._on_playback_finished)
            self.assertEqual(mock_tts_service.call_args.kwargs["audio_player"], mock_audio_instance)
            self.assertEqual(mock_tts_service.call_args.kwargs["settings_observer"], owner)
            mock_llm_service.assert_called_once_with(settings_observer=owner, mcp_manager=owner.mcp_manager)
            self.assertIsNotNone(owner.mcp_manager)

    def test_runtime_manager_load_settings_sanitizes_mismatch(self):
        owner = DummyOwner()
        owner.model_manager = MagicMock()
        mock_settings = MagicMock()

        def get_string_mock(key):
            if key == "stt-provider":
                return "vosk"
            elif key == "stt-model":
                return "whisper-1"
            elif key == "language":
                return "it"
            return ""

        mock_settings.get_string.side_effect = get_string_mock
        mock_settings.get_int.return_value = 60
        mock_settings.get_boolean.return_value = True
        owner.settings = mock_settings

        with patch("gi.repository.Gio.Settings.new", return_value=mock_settings):
            runtime = DaemonRuntimeManager(owner)
            runtime.load_settings()

            self.assertEqual(owner.provider_name, "vosk")
            self.assertEqual(owner.model_name, "vosk-model-small-it-0.22")
            mock_settings.set_string.assert_called_with("stt-model", "vosk-model-small-it-0.22")

    def test_runtime_manager_load_settings_detects_system_language(self):
        """Verifica che con impostazione 'language' vuota, venga usata la lingua di sistema."""
        owner = DummyOwner()
        owner.model_manager = MagicMock()
        mock_settings = MagicMock()

        def get_string_mock(key):
            if key == "stt-provider":
                return "vosk"
            elif key == "stt-model":
                return ""
            elif key == "language":
                return ""
            return ""

        mock_settings.get_string.side_effect = get_string_mock
        mock_settings.get_int.return_value = 60
        mock_settings.get_boolean.return_value = True
        owner.settings = mock_settings

        with patch("gi.repository.Gio.Settings.new", return_value=mock_settings):
            with patch("core.locale_utils.get_system_language", return_value="fr"):
                with patch("providers.vosk_provider.VoskProvider.get_default_model", return_value="vosk-model-small-fr-0.22"):
                    runtime = DaemonRuntimeManager(owner)
                    runtime.load_settings()

                    self.assertEqual(owner.language, "fr")
                    self.assertEqual(owner.vosk_ww_model, "vosk-model-small-fr-0.22")

    def test_runtime_manager_load_settings_connects_speaker_id_signals(self):
        """Bug: speaker-id-mode/speaker-id-threshold non erano mai collegati a on_settings_changed,
        quindi le modifiche a runtime non venivano mai applicate al controller."""
        owner = DummyOwner()
        owner.model_manager = MagicMock()
        mock_settings = MagicMock()

        def get_string_mock(key):
            if key == "stt-provider":
                return "vosk"
            elif key == "stt-model":
                return "vosk-model-small-it-0.22"
            elif key == "language":
                return "it"
            return ""

        mock_settings.get_string.side_effect = get_string_mock
        mock_settings.get_int.return_value = 60
        mock_settings.get_boolean.return_value = True
        owner.settings = mock_settings

        with patch("gi.repository.Gio.Settings.new", return_value=mock_settings):
            runtime = DaemonRuntimeManager(owner)
            runtime.load_settings()

        mock_settings.connect.assert_any_call("changed::speaker-id-mode", owner.on_settings_changed)
        mock_settings.connect.assert_any_call("changed::speaker-id-threshold", owner.on_settings_changed)

    def test_initialize_notifications_with_glib(self):
        owner = DummyOwner()
        runtime = DaemonRuntimeManager(owner)
        with patch("notify2.init") as mock_init:
            runtime.initialize_notifications()
            mock_init.assert_called_once_with("Voice Assistant", mainloop='glib')

    def test_notify_missing_deps_summary_actions(self):
        owner = DummyOwner()
        owner._missing_deps = [{"package": "sherpa-onnx", "type": "pip"}]
        runtime = DaemonRuntimeManager(owner)

        with patch("notify2.Notification") as mock_notif_cls, \
             patch("gi.repository.GLib.idle_add") as mock_idle_add, \
             patch("daemon.core.runtime_manager._write_persisted_deps_notif_id") as mock_write_id:
            mock_notif = MagicMock()
            mock_notif.id = 55
            mock_notif_cls.return_value = mock_notif

            runtime._notify_missing_deps_summary()

            # Verifica aggiunta hint desktop-entry per raggruppamento GNOME Shell
            mock_notif.set_hint_string.assert_called_with("desktop-entry", "org.local.VoiceAssistant.GUI")

            # Verifica aggiunta azioni
            mock_notif.add_action.assert_any_call("default", "Apri", unittest.mock.ANY)
            mock_notif.add_action.assert_any_call("install", "Installa", unittest.mock.ANY)
            mock_notif.show.assert_called_once()
            self.assertIs(owner._deps_notif, mock_notif)
            mock_write_id.assert_called_with(55)

            # Trova il callback dell'azione 'default' e invocalo
            calls = mock_notif.add_action.call_args_list
            action_cb = None
            for call in calls:
                if call[0][0] == "default":
                    action_cb = call[0][2]
                    break
            self.assertIsNotNone(action_cb)

            # Esegui il callback
            action_cb(mock_notif, "default")
            mock_idle_add.assert_called_once_with(owner._launch_gui, "--install-deps")

    def test_notify_missing_deps_reuses_replaces_id(self):
        owner = DummyOwner()
        owner._missing_deps = [{"package": "sherpa-onnx", "type": "pip"}]
        owner._last_deps_notif_id = 99
        runtime = DaemonRuntimeManager(owner)

        with patch("notify2.Notification") as mock_notif_cls, \
             patch("daemon.core.runtime_manager._write_persisted_deps_notif_id") as mock_write_id:
            mock_notif = MagicMock()
            mock_notif.id = 99
            mock_notif_cls.return_value = mock_notif

            runtime._notify_missing_deps_summary()

            # Verifica che replaces_id (notif.id) sia impostato prima di show()
            self.assertEqual(mock_notif.id, 99)
            mock_notif.show.assert_called_once()
            mock_write_id.assert_called_with(99)

    def test_notify_missing_deps_closes_when_resolved(self):
        owner = DummyOwner()
        owner._missing_deps = []
        owner._last_deps_notif_id = 77
        runtime = DaemonRuntimeManager(owner)

        with patch.object(runtime, "_close_deps_notification") as mock_close, \
             patch("daemon.core.runtime_manager._write_persisted_deps_notif_id") as mock_write_id:
            runtime._notify_missing_deps_summary()

            mock_close.assert_called_once_with(77)
            self.assertEqual(owner._last_deps_notif_id, 0)
            mock_write_id.assert_called_with(0)

    def test_on_gdbus_notification_closed_clears_persisted_id(self):
        owner = DummyOwner()
        owner._last_deps_notif_id = 42
        runtime = DaemonRuntimeManager(owner)

        with patch("daemon.core.runtime_manager._write_persisted_deps_notif_id") as mock_write_id:
            params = MagicMock()
            params.unpack.return_value = (42, 2)  # id=42, reason=dismissed

            runtime._on_gdbus_notification_closed(None, None, None, None, None, params)

            self.assertEqual(owner._last_deps_notif_id, 0)
            mock_write_id.assert_called_with(0)

    def test_provider_manager_notifications_include_desktop_entry_hint(self):
        owner = DummyOwner()
        manager = ProviderManager(owner)

        with patch("notify2.Notification") as mock_notif_cls:
            mock_notif = MagicMock()
            mock_notif_cls.return_value = mock_notif

            # Test cancel_download
            with patch("gi.repository.GLib.idle_add", side_effect=lambda cb, arg: cb(arg)):
                manager.cancel_download("vosk", "model-test")
                mock_notif.set_hint_string.assert_called_with("desktop-entry", "org.local.VoiceAssistant.GUI")

    def test_system_deps_does_not_contain_cargo(self):
        from daemon.core.runtime_manager import _SYSTEM_DEPS
        cargo_dep = next((d for d in _SYSTEM_DEPS if d.get("package") == "cargo"), None)
        self.assertIsNone(cargo_dep, "cargo non deve essere nelle dipendenze generali di sistema")

    def test_probe_mcp_deps_reports_cargo_and_server(self):
        owner = DummyOwner()
        owner.settings = MagicMock()
        owner.settings.get_boolean.return_value = True

        mock_mcp = MagicMock()
        mock_mcp.config_loader.load.return_value = {
            "mcpServers": {
                "gnome-mcp-server": {
                    "command": "gnome-mcp-server",
                    "enabled": True,
                    "description": "Server GNOME MCP"
                }
            }
        }
        mock_mcp.is_server_installed.return_value = False
        owner.mcp_manager = mock_mcp

        runtime = DaemonRuntimeManager(owner)

        with patch("shutil.which", return_value=None):
            runtime._probe_mcp_deps()

            reported_packages = [d["package"] for d in owner._missing_deps]
            self.assertIn("cargo", reported_packages)
            self.assertIn("gnome-mcp-server", reported_packages)

            cargo_entry = next(d for d in owner._missing_deps if d["package"] == "cargo")
            self.assertEqual(cargo_entry.get("mcp_server"), "gnome-mcp-server")
            self.assertEqual(cargo_entry.get("type"), "system")

            mcp_entry = next(d for d in owner._missing_deps if d["package"] == "gnome-mcp-server")
            self.assertEqual(mcp_entry.get("type"), "mcp")

    def test_probe_mcp_deps_disabled_reports_nothing(self):
        owner = DummyOwner()
        owner.settings = MagicMock()
        owner.settings.get_boolean.return_value = False

        runtime = DaemonRuntimeManager(owner)
        runtime._probe_mcp_deps()

        self.assertEqual(owner._missing_deps, [])

    def test_refresh_missing_deps(self):
        owner = DummyOwner()
        runtime = DaemonRuntimeManager(owner)

        with patch.object(runtime, "_probe_optional_deps") as mock_probe_opt, \
             patch.object(runtime, "_probe_system_deps") as mock_probe_sys, \
             patch.object(runtime, "_probe_mcp_deps") as mock_probe_mcp:
            runtime.refresh_missing_deps()
            mock_probe_opt.assert_called_once()
            mock_probe_sys.assert_called_once()
            mock_probe_mcp.assert_called_once()


if __name__ == '__main__':
    unittest.main()
