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

    def test_on_settings_changed_speaker_id_mode_updates_controller(self):
        """Bug: speaker-id-mode non era mai collegato al segnale changed, quindi il
        cambio a runtime dalle preferenze non raggiungeva mai il controller."""
        owner = DummyOwner()
        owner.speaker_id_controller = MagicMock()
        owner.speaker_id_controller.backend.is_available.return_value = True
        controller = AssistantRuntimeController(owner)
        settings = MagicMock()
        settings.get_string.return_value = "gate"

        controller.on_settings_changed(settings, "speaker-id-mode")

        owner.speaker_id_controller.set_mode.assert_called_once_with("gate")

    def test_on_settings_changed_speaker_id_threshold_updates_controller(self):
        owner = DummyOwner()
        owner.speaker_id_controller = MagicMock()
        controller = AssistantRuntimeController(owner)
        settings = MagicMock()
        settings.get_double.return_value = 0.82

        controller.on_settings_changed(settings, "speaker-id-threshold")

        owner.speaker_id_controller.set_threshold.assert_called_once_with(0.82)

    def test_trigger_assistant_passes_noise_floor_getter_when_audio_filter_present(self):
        """Bug: trigger_assistant creava la sessione speaker senza noise_floor_getter,
        anche quando owner.audio_filter era disponibile."""
        owner = DummyOwner()
        owner.speaker_id_controller = MagicMock()
        owner.speaker_id_controller.mode = "informative"
        owner.audio_filter = MagicMock()
        owner.audio_filter.get_noise_floor.return_value = 123.0
        controller = AssistantRuntimeController(owner)

        controller.trigger_assistant()

        _, kwargs = owner.speaker_id_controller.create_session.call_args
        noise_floor_getter = kwargs.get("noise_floor_getter")
        self.assertTrue(callable(noise_floor_getter))
        self.assertEqual(noise_floor_getter(), 123.0)

    def test_trigger_assistant_noise_floor_getter_none_without_audio_filter(self):
        """owner.audio_filter può essere assente/None nei test: create_session non deve fallire."""
        owner = DummyOwner()
        owner.speaker_id_controller = MagicMock()
        owner.speaker_id_controller.mode = "informative"
        controller = AssistantRuntimeController(owner)

        controller.trigger_assistant()

        _, kwargs = owner.speaker_id_controller.create_session.call_args
        self.assertIsNone(kwargs.get("noise_floor_getter"))

    def test_enqueue_typed_request_does_not_consume_was_interrupting_flag(self):
        """Bug: una richiesta testuale (D-Bus/GUI) consumava/azzerava il flag
        _was_interrupting destinato alla prossima richiesta vocale."""
        owner = DummyOwner()
        owner._was_interrupting = True
        controller = AssistantRuntimeController(owner)

        controller.enqueue_request("richiesta digitata", is_voice=False, context_id="voice")

        # Il flag non deve essere stato consumato dalla richiesta testuale
        self.assertTrue(owner._was_interrupting)
        item = controller.request_queue.get_nowait()
        was_interrupting = item[6]
        self.assertFalse(was_interrupting)

        # La successiva richiesta vocale deve ancora vedere il flag True
        controller.enqueue_request("richiesta vocale", is_voice=True, context_id="voice")
        self.assertFalse(owner._was_interrupting)
        item_voice = controller.request_queue.get_nowait()
        self.assertTrue(item_voice[6])

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
                "launch_application", {"app_name": "Calcolatrice"}
            )

    def test_launch_app_recovers_from_mcp_json_error_to_native(self):
        owner = DummyOwner()
        owner.mcp_manager.execute_tool.return_value = '{"error":"App \'org.gnome.Software.desktop\' not found among 217 total apps","success":false}'
        controller = AssistantRuntimeController(owner)

        resolved = {"name": "Software", "exec": "gnome-software", "desktop_id": "org.gnome.Software.desktop", "score": 100.0}
        with patch.object(controller.app_matcher, 'match', return_value=resolved), \
             patch.object(controller, '_launch_desktop_app_native', return_value=True) as mock_native:
            matched, resp = controller._handle_fast_path_intent("launch_app", {"app": "software"})
            self.assertTrue(matched)
            self.assertEqual(resp, "Apro Software.")
            mock_native.assert_called_once_with("software", desktop_id="org.gnome.Software.desktop", desktop_path=None)

    def test_launch_app_never_returns_raw_json_on_failure(self):
        owner = DummyOwner()
        owner.mcp_manager.execute_tool.return_value = '{"error":"App \'unknown\' not found","success":false}'
        controller = AssistantRuntimeController(owner)

        resolved = {"name": "Sconosciuta", "exec": "unknown", "desktop_id": "unknown.desktop", "score": 100.0}
        with patch.object(controller.app_matcher, 'match', return_value=resolved), \
             patch.object(controller, '_launch_desktop_app_native', return_value=False):
            matched, resp = controller._handle_fast_path_intent("launch_app", {"app": "sconosciuta"})
            self.assertTrue(matched)
            self.assertEqual(resp, "Non sono riuscito ad avviare Sconosciuta.")
            self.assertNotIn("error", resp)
            self.assertNotIn("{", resp)


    def test_build_mailto_uri(self):
        controller = AssistantRuntimeController(DummyOwner())
        self.assertEqual(controller._build_mailto_uri(), "mailto:")
        self.assertEqual(controller._build_mailto_uri(to="mario@example.com"), "mailto:mario@example.com")
        self.assertEqual(
            controller._build_mailto_uri(to="mario@example.com", body="ci vediamo"),
            "mailto:mario@example.com?body=ci%20vediamo"
        )
        self.assertEqual(
            controller._build_mailto_uri(to="info@test.it", subject="saluti", body="ciao"),
            "mailto:info@test.it?subject=saluti&body=ciao"
        )

    def test_compose_mail_intent_with_mcp(self):
        owner = DummyOwner()
        owner.mcp_manager.execute_tool.return_value = '{"success": true}'
        controller = AssistantRuntimeController(owner)

        matched, resp = controller._handle_fast_path_intent(
            "compose_mail",
            {"to": "mario@example.com", "text": "ci vediamo domani"}
        )
        self.assertTrue(matched)
        self.assertIn("mario@example.com", resp)
        owner.mcp_manager.execute_tool.assert_called_once_with(
            "open_file",
            {"path": "mailto:mario@example.com?body=ci%20vediamo%20domani"}
        )

    def test_compose_mail_intent_native_when_no_mcp(self):
        owner = DummyOwner()
        owner.mcp_manager = None
        controller = AssistantRuntimeController(owner)

        with patch("gi.repository.Gio.AppInfo.launch_default_for_uri", return_value=True) as mock_launch:
            matched, resp = controller._handle_fast_path_intent(
                "compose_mail",
                {"to": "luca@test.it", "subject": "prova", "body": "ciao"}
            )
            self.assertTrue(matched)
            self.assertIn("luca@test.it", resp)
            mock_launch.assert_called_once_with("mailto:luca@test.it?subject=prova&body=ciao", None)


    def test_speaker_id_gate_rejection(self):
        from daemon.services.speaker_id.policy import SpeakerDecision
        owner = DummyOwner()
        owner.speaker_id_controller = MagicMock()
        owner.speaker_id_controller.mode = "gate"
        owner.speaker_id_controller.threshold = 0.75
        owner.speaker_id_controller.evaluate_policy.return_value = SpeakerDecision(
            allow=False,
            reason="unknown",
            speaker_name=None,
        )
        owner.SpeakerRejected = MagicMock()

        mock_verdict = MagicMock()
        mock_verdict.display_name = None
        mock_verdict.score = 0.2
        mock_verdict.status = "unknown"
        mock_verdict.overlap_detected = False

        mock_session = MagicMock()
        mock_session.finalize.return_value = mock_verdict

        controller = AssistantRuntimeController(owner)
        controller._process_text("apri firefox", is_voice=True, speaker_session=mock_session)

        owner.SpeakerRejected.assert_called_once()
        owner.tts_manager.speak.assert_called_once()
        owner.pipeline_controller.process_text_input.assert_not_called()

    def test_speaker_id_informative_context_passed_to_pipeline(self):
        from daemon.services.speaker_id.policy import SpeakerDecision
        owner = DummyOwner()
        owner.speaker_id_controller = MagicMock()
        owner.speaker_id_controller.mode = "informative"
        owner.speaker_id_controller.evaluate_policy.return_value = SpeakerDecision(
            allow=True,
            reason="recognized",
            speaker_name="Mario",
            llm_context="Parlante identificato: Mario.",
        )
        owner.SpeakerIdentified = MagicMock()

        mock_verdict = MagicMock()
        mock_verdict.display_name = "Mario"
        mock_verdict.score = 0.88
        mock_verdict.status = "recognized"
        mock_verdict.overlap_detected = False

        mock_session = MagicMock()
        mock_session.finalize.return_value = mock_verdict

        controller = AssistantRuntimeController(owner)
        controller._process_text("che tempo fa", is_voice=True, speaker_session=mock_session)

        owner.SpeakerIdentified.assert_called_once_with("Mario", 0.88, "recognized", False)
        owner.pipeline_controller.process_text_input.assert_called_once()
        call_kwargs = owner.pipeline_controller.process_text_input.call_args[1]
        self.assertIn("Mario", call_kwargs.get("extra_context", ""))

    def test_speaker_id_stop_command_cancels_pipeline_when_interrupting(self):
        # A2: When interrupting (speaking/processing), pure stop command cancels pipeline and does NOT call process_text_input
        from daemon.services.speaker_id.policy import SpeakerDecision, SpeakerPolicy
        owner = DummyOwner()
        owner.speaker_id_controller = MagicMock()
        owner.speaker_id_controller.mode = "gate"
        owner.speaker_id_controller.evaluate_policy.side_effect = lambda verdict, is_voice, is_stop_command: SpeakerPolicy.evaluate(
            "gate", verdict, is_voice=is_voice, is_stop_command=is_stop_command
        )
        owner.SpeakerRejected = MagicMock()

        mock_verdict = MagicMock()
        mock_verdict.display_name = None
        mock_verdict.score = 0.1
        mock_verdict.status = "unknown"
        mock_verdict.overlap_detected = False

        mock_session = MagicMock()
        mock_session.finalize.return_value = mock_verdict

        controller = AssistantRuntimeController(owner)
        controller._process_text("stop", is_voice=True, speaker_session=mock_session, was_interrupting=True)

        owner.pipeline_controller.cancel_pipeline.assert_called_once()
        owner.pipeline_controller.process_text_input.assert_not_called()
        owner.SpeakerRejected.assert_not_called()

    def test_speaker_id_malicious_commands_rejected_in_gate(self):
        # A2: Commands containing old stop words like 'blocca', 'cancella', 'annulla' must be rejected for unknown voice
        from daemon.services.speaker_id.policy import SpeakerPolicy
        for cmd in ["blocca lo schermo", "cancella la cartella documenti", "annulla la riunione"]:
            owner = DummyOwner()
            owner.speaker_id_controller = MagicMock()
            owner.speaker_id_controller.mode = "gate"
            owner.speaker_id_controller.evaluate_policy.side_effect = lambda verdict, is_voice, is_stop_command: SpeakerPolicy.evaluate(
                "gate", verdict, is_voice=is_voice, is_stop_command=is_stop_command
            )
            owner.SpeakerRejected = MagicMock()

            mock_verdict = MagicMock()
            mock_verdict.display_name = None
            mock_verdict.score = 0.1
            mock_verdict.status = "unknown"
            mock_verdict.overlap_detected = False

            mock_session = MagicMock()
            mock_session.finalize.return_value = mock_verdict

            controller = AssistantRuntimeController(owner)
            controller._process_text(cmd, is_voice=True, speaker_session=mock_session, was_interrupting=True)

            owner.SpeakerRejected.assert_called_once()
            owner.pipeline_controller.process_text_input.assert_not_called()

    def test_speaker_id_stop_rejected_when_idle(self):
        # A2: 'stop' from unknown voice when idle (was_interrupting=False) is rejected because there's nothing to stop
        from daemon.services.speaker_id.policy import SpeakerPolicy
        owner = DummyOwner()
        owner.speaker_id_controller = MagicMock()
        owner.speaker_id_controller.mode = "gate"
        owner.speaker_id_controller.evaluate_policy.side_effect = lambda verdict, is_voice, is_stop_command: SpeakerPolicy.evaluate(
            "gate", verdict, is_voice=is_voice, is_stop_command=is_stop_command
        )
        owner.SpeakerRejected = MagicMock()

        mock_verdict = MagicMock()
        mock_verdict.display_name = None
        mock_verdict.score = 0.1
        mock_verdict.status = "unknown"
        mock_verdict.overlap_detected = False

        mock_session = MagicMock()
        mock_session.finalize.return_value = mock_verdict

        controller = AssistantRuntimeController(owner)
        controller._process_text("stop", is_voice=True, speaker_session=mock_session, was_interrupting=False)

        owner.SpeakerRejected.assert_called_once()
        owner.pipeline_controller.process_text_input.assert_not_called()

    def test_audio_loop_checks_enrollment_timeout_even_when_disabled(self):
        """Bug: check_timeout() dell'enrollment non veniva mai chiamato dal loop audio,
        quindi una registrazione senza più audio in arrivo restava bloccata per sempre.
        Verifica anche che il blocco enrollment venga gestito PRIMA del check 'disabled',
        dato che l'enrollment può restare attivo anche ad assistente disabilitato."""
        owner = DummyOwner()
        owner._state = "disabled"
        owner.audio_filter = MagicMock()
        owner._report_error = MagicMock()

        spk_ctrl = MagicMock()
        spk_ctrl.is_enrollment_active = True
        stop_loop = Exception("test-stop-audio-loop")
        spk_ctrl.check_enrollment_timeout = MagicMock(side_effect=stop_loop)
        owner.speaker_id_controller = spk_ctrl

        # Safety valve: se check_enrollment_timeout() non viene mai chiamato (bug non
        # corretto), non lasciare che il test giri all'infinito. Dopo un numero
        # ragionevole di iterazioni solleva un'eccezione non-Exception (non
        # catturata dai vari `except Exception` del loop) che fa fallire il test
        # rapidamente invece di farlo restare appeso.
        class _SafetyStop(BaseException):
            pass

        call_count = {"n": 0}

        def _get_side_effect(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] > 50:
                raise _SafetyStop("audio loop non ha mai chiamato check_enrollment_timeout()")
            raise queue.Empty()

        owner.q = MagicMock()
        owner.q.get.side_effect = _get_side_effect

        controller = AssistantRuntimeController(owner)

        with patch('core.assistant_runtime.time.sleep'):
            try:
                controller._audio_loop()
            except _SafetyStop as exc:
                self.fail(str(exc))

        spk_ctrl.check_enrollment_timeout.assert_called_once()
        spk_ctrl.feed_enrollment_audio.assert_not_called()
        owner._report_error.assert_called_once_with(stop_loop)

    def test_audio_loop_feeds_enrollment_audio_and_checks_timeout(self):
        owner = DummyOwner()
        # Stato "disabled" solo per evitare che il loop tenti di riavviare un
        # nuovo thread dopo l'eccezione di stop usata per uscire dal `while True`.
        owner._state = "disabled"
        owner.audio_filter = MagicMock()
        owner._report_error = MagicMock()

        spk_ctrl = MagicMock()
        spk_ctrl.is_enrollment_active = True
        stop_loop = Exception("test-stop-audio-loop")
        spk_ctrl.check_enrollment_timeout = MagicMock(side_effect=stop_loop)
        owner.speaker_id_controller = spk_ctrl

        owner.q = MagicMock()
        owner.q.get.return_value = b"some-audio-chunk"

        controller = AssistantRuntimeController(owner)

        with patch('core.assistant_runtime.time.sleep'):
            controller._audio_loop()

        spk_ctrl.feed_enrollment_audio.assert_called_once_with(b"some-audio-chunk")
        spk_ctrl.check_enrollment_timeout.assert_called_once()

    def test_trigger_assistant_with_context(self):
        owner = DummyOwner()
        controller = AssistantRuntimeController(owner)

        controller.trigger_assistant(origin="manual", context_id="chat-test-123")
        self.assertEqual(getattr(owner, "_active_listen_context_id", None), "chat-test-123")

    def test_audio_loop_enqueues_with_active_listen_context(self):
        owner = DummyOwner()
        owner._state = "listening"
        owner._active_listen_context_id = "chat-custom-456"
        owner.audio_filter = MagicMock()
        owner._report_error = MagicMock()
        owner.provider = MagicMock()
        owner.provider.process_chunk.return_value = ("ciao assistente", "")
        owner.q = MagicMock()
        owner.q.get.return_value = b"pcm-audio-data"

        controller = AssistantRuntimeController(owner)
        with patch.object(controller, 'enqueue_request') as mock_enqueue:
            # Fermiamo il loop dopo la prima iterazione con un'eccezione
            owner.provider.process_chunk.side_effect = [("ciao assistente", ""), Exception("stop-loop")]
            try:
                controller._audio_loop()
            except Exception:
                pass

            mock_enqueue.assert_called_once()
            _, kwargs = mock_enqueue.call_args
            self.assertEqual(kwargs.get("context_id"), "chat-custom-456")
            self.assertEqual(getattr(owner, "_active_listen_context_id", None), "voice")


if __name__ == '__main__':
    unittest.main()


