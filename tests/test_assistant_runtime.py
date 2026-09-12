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
        owner.mcp_manager.execute_tool.assert_called_once_with("set_volume", {"direction": "up"})

    def test_fast_path_intent_resolves_async_mcp_tool(self):
        owner = DummyOwner()

        async def execute_tool(tool_name, args):
            return f"{tool_name}:{args.get('direction', '')}"

        owner.mcp_manager.execute_tool.side_effect = execute_tool
        controller = AssistantRuntimeController(owner)

        matched, result = controller._handle_fast_path_intent("volume_up", {})

        self.assertTrue(matched)
        self.assertEqual(result, "set_volume:up")

    def test_mcp_enabled_setting_updates_manager(self):
        owner = DummyOwner()
        controller = AssistantRuntimeController(owner)
        settings = MagicMock()
        settings.get_boolean.return_value = False

        controller.on_settings_changed(settings, "mcp-enabled")

        self.assertFalse(owner.mcp_manager.enabled)

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


    def test_process_text_fast_path_emits_complete_token(self):
        owner = DummyOwner()
        owner.pipeline_controller.process_text_input.return_value = {
            "fast_path": True,
            "response": "Apro il calendario."
        }
        controller = AssistantRuntimeController(owner)

        controller._process_text("apri il calendario", is_voice=False)

        owner.ResponseTokenStreamed.assert_called_once_with("Apro il calendario.", True)

    def test_process_text_medium_path_emits_complete_token(self):
        owner = DummyOwner()
        owner.pipeline_controller.process_text_input.return_value = {
            "fast_path": False,
            "medium_path": True,
            "response": "Volume impostato."
        }
        controller = AssistantRuntimeController(owner)

        controller._process_text("imposta volume a 50", is_voice=False)

        owner.ResponseTokenStreamed.assert_called_once_with("Volume impostato.", True)

    def test_process_text_smart_path_emits_complete_token(self):
        owner = DummyOwner()
        owner.pipeline_controller.process_text_input.return_value = {
            "fast_path": False,
            "smart_path": True,
            "response": "Risposta generata."
        }
        controller = AssistantRuntimeController(owner)

        controller._process_text("Raccontami una storia", is_voice=False)

        owner.ResponseTokenStreamed.assert_called_once_with("", True)

    def test_launch_app_fallback_native_when_mcp_fails(self):
        owner = DummyOwner()
        owner.mcp_manager.execute_tool.return_value = "Errore nell'esecuzione del tool 'launch_application'"
        controller = AssistantRuntimeController(owner)

        # Force no AppSlotMatcher resolution so this test exercises the raw MCP-then-native
        # fallback chain regardless of which .desktop files happen to be installed on the
        # machine running the test.
        with patch.object(controller.app_matcher, 'match', return_value=None), \
             patch.object(controller, '_launch_desktop_app_native', return_value=True) as mock_native:
            matched, resp = controller._handle_fast_path_intent("launch_app", {"app": "calendario"})
            self.assertTrue(matched)
            self.assertEqual(resp, "Apro calendario.")
            mock_native.assert_called_once_with("calendario")

    def test_launch_app_uses_app_slot_matcher_resolution_when_mcp_succeeds(self):
        owner = DummyOwner()
        owner.mcp_manager.execute_tool.return_value = '{"success": true, "message": "Successfully launched"}'
        controller = AssistantRuntimeController(owner)

        resolved = {"name": "Calcolatrice", "exec": "flatpak", "desktop_id": "org.gnome.Calculator.desktop", "score": 100.0}
        with patch.object(controller.app_matcher, 'match', return_value=resolved) as mock_match:
            matched, resp = controller._handle_fast_path_intent("launch_app", {"app": "calcolatrice"})
            self.assertTrue(matched)
            self.assertEqual(resp, "Apro Calcolatrice.")
            mock_match.assert_called_once_with("calcolatrice")
            owner.mcp_manager.execute_tool.assert_called_once_with(
                "launch_application", {"app_name": "org.gnome.Calculator.desktop"}
            )


if __name__ == '__main__':
    unittest.main()
