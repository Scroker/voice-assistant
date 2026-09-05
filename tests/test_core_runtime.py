import asyncio
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
            mock_tts_service.assert_called_once_with(audio_player=mock_audio_instance)
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


if __name__ == '__main__':
    unittest.main()
