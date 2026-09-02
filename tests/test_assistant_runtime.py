import queue
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import sys
from pathlib import Path


daemon_dir = Path(__file__).resolve().parent.parent / 'src' / 'daemon'
sys.path.insert(0, str(daemon_dir))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

try:
    from daemon.core.assistant_runtime import AssistantRuntimeController
except ImportError:
    from core.assistant_runtime import AssistantRuntimeController


class DummyOwner:
    def __init__(self):
        self.mcp_manager = MagicMock()
        self.mcp_manager.execute_tool.return_value = "ok"
        self.settings = MagicMock()
        self._state = "idle"
        self.q = queue.Queue()
        self.audio_player = MagicMock()
        self.audio_player.is_playing = False
        self.provider = MagicMock()
        self.provider.reset = MagicMock()
        self.wakeword = "assistente"
        self.ww_model = "model"
        self.ww_recognizer = MagicMock()
        self._listening_start_time = None
        self._last_speech_time = None
        self._last_partial_text = ""
        self._last_partial_change_time = None
        self._ignore_audio_until = 0
        self._gui_window = None
        self.pipeline_controller = MagicMock()
        self.pipeline_controller.cancel_pipeline = MagicMock()
        self._reload_timer = None
        self._load_id = 1
        self.fast_path = MagicMock()
        self.fast_path.dispatch.return_value = (False, None, None, None)
        self._report_error = MagicMock()
        self.tts_manager = MagicMock()
        self.tts_manager.speak.return_value = True
        self.Provider = MagicMock()
        self.set_state = MagicMock(side_effect=lambda state: setattr(self, '_state', state))
        self.TranscriptReceived = MagicMock()
        self.ResponseTokenStreamed = MagicMock()
        self.reset_wakeword_recognizer = MagicMock()


class TestAssistantRuntime(unittest.TestCase):

    def test_fast_path_intent_routes_to_mcp(self):
        owner = DummyOwner()
        controller = AssistantRuntimeController(owner)

        matched, result = controller._handle_fast_path_intent("volume_up", {})

        self.assertTrue(matched)
        self.assertEqual(result, "ok")
        owner.mcp_manager.execute_tool.assert_called_once_with("system_volume", {"action": "increase", "level": 10})

    def test_trigger_assistant_starts_listening_and_resets_provider(self):
        owner = DummyOwner()
        owner.q.put(b"junk")
        controller = AssistantRuntimeController(owner)

        controller.trigger_assistant()

        self.assertTrue(owner.q.empty())
        self.assertEqual(owner._state, "listening")
        owner.set_state.assert_any_call("listening")
        owner.provider.reset.assert_called()
        owner.audio_player.play_wakeword_chime.assert_called_once()

    def test_on_settings_changed_updates_wakeword_and_resets_state(self):
        owner = DummyOwner()
        owner._state = "listening"
        controller = AssistantRuntimeController(owner)
        settings = MagicMock()
        settings.get_string.return_value = "anthon"

        with patch('gi.repository.GLib.idle_add', side_effect=lambda cb, *args: cb(*args)):
            controller.on_settings_changed(settings, "wakeword")

        self.assertEqual(owner.wakeword, "anthon")
        owner.provider.reset.assert_called()
        owner.set_state.assert_any_call("idle")

    def test_trigger_assistant_handles_queue_and_reload_schedule(self):
        owner = DummyOwner()
        owner.q.put(b"stale")
        controller = AssistantRuntimeController(owner)

        with patch.object(controller, '_schedule_reload') as schedule_mock:
            owner.settings.get_string.return_value = "wakeword"
            owner.settings.get_boolean.return_value = True

            controller.trigger_assistant()

        self.assertTrue(owner.q.empty())
        self.assertIsNotNone(owner._listening_start_time)
        self.assertEqual(owner._state, "listening")
        owner.audio_player.play_wakeword_chime.assert_called_once()
        schedule_mock.assert_not_called()


if __name__ == '__main__':
    unittest.main()
