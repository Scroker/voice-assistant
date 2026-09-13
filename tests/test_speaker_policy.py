import os
import sys
import unittest

# Add src/daemon to import path
daemon_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "daemon"))
tests_dir = os.path.abspath(os.path.dirname(__file__))
if daemon_dir not in sys.path:
    sys.path.insert(0, daemon_dir)
if tests_dir not in sys.path:
    sys.path.insert(0, tests_dir)

from services.speaker_id.policy import SpeakerDecision, SpeakerPolicy
from services.speaker_id.session import SpeakerVerdict


class TestSpeakerPolicy(unittest.TestCase):
    def test_disabled_mode(self):
        v = SpeakerVerdict(profile_id=None, status="unknown")
        dec = SpeakerPolicy.evaluate("disabled", v, is_voice=True, is_stop_command=False)
        self.assertTrue(dec.allow)
        self.assertEqual(dec.reason, "disabled")
        self.assertEqual(dec.llm_context, "")

    def test_typed_input_bypass(self):
        v = SpeakerVerdict(profile_id=None, status="unknown")
        dec = SpeakerPolicy.evaluate("gate", v, is_voice=False, is_stop_command=False)
        self.assertTrue(dec.allow)
        self.assertEqual(dec.reason, "typed_input")

    def test_stop_command_bypass(self):
        v = SpeakerVerdict(profile_id=None, status="unknown")
        dec = SpeakerPolicy.evaluate("gate", v, is_voice=True, is_stop_command=True)
        self.assertTrue(dec.allow)
        self.assertEqual(dec.reason, "stop_command")

    def test_is_pure_stop_command_table(self):
        from services.speaker_id.policy import is_pure_stop_command
        wakewords = ["assistente", "computer"]

        # Valid pure stop commands
        self.assertTrue(is_pure_stop_command("stop"))
        self.assertTrue(is_pure_stop_command("basta!"))
        self.assertTrue(is_pure_stop_command("per favore stop"))
        self.assertTrue(is_pure_stop_command("ehi assistente basta", wakewords))
        self.assertTrue(is_pure_stop_command("ora zitto"))
        self.assertTrue(is_pure_stop_command("stop basta"))
        self.assertTrue(is_pure_stop_command("interrompi subito"))

        # Invalid commands that must NOT be treated as pure stop commands
        self.assertFalse(is_pure_stop_command("blocca lo schermo"))
        self.assertFalse(is_pure_stop_command("cancella la cartella documenti"))
        self.assertFalse(is_pure_stop_command("annulla la riunione"))
        self.assertFalse(is_pure_stop_command("stop musica per favore"))
        self.assertFalse(is_pure_stop_command("fermati qui e ascolta cosa dico"))
        self.assertFalse(is_pure_stop_command(""))
        self.assertFalse(is_pure_stop_command("assistente", wakewords))
        self.assertFalse(is_pure_stop_command("per favore"))


    def test_informative_mode(self):
        # Recognized speaker
        v_rec = SpeakerVerdict(profile_id="mario", display_name="Mario", score=0.88, status="recognized")
        dec_rec = SpeakerPolicy.evaluate("informative", v_rec, is_voice=True, is_stop_command=False)
        self.assertTrue(dec_rec.allow)
        self.assertEqual(dec_rec.reason, "recognized")
        self.assertIn("Mario", dec_rec.llm_context)

        # Unknown speaker still allowed in informative mode without context
        v_unk = SpeakerVerdict(profile_id=None, display_name=None, score=0.4, status="unknown")
        dec_unk = SpeakerPolicy.evaluate("informative", v_unk, is_voice=True, is_stop_command=False)
        self.assertTrue(dec_unk.allow)
        self.assertEqual(dec_unk.reason, "unknown")
        self.assertEqual(dec_unk.llm_context, "")

    def test_gate_mode_recognized(self):
        v = SpeakerVerdict(profile_id="luigi", display_name="Luigi", score=0.85, status="recognized", overlap_detected=False)
        dec = SpeakerPolicy.evaluate("gate", v, is_voice=True, is_stop_command=False)
        self.assertTrue(dec.allow)
        self.assertEqual(dec.reason, "recognized")
        self.assertIn("Luigi", dec.llm_context)

    def test_gate_mode_rejected_reasons(self):
        # Overlap detected
        v_ovl = SpeakerVerdict(profile_id="luigi", display_name="Luigi", status="recognized", overlap_detected=True)
        dec_ovl = SpeakerPolicy.evaluate("gate", v_ovl, is_voice=True, is_stop_command=False)
        self.assertFalse(dec_ovl.allow)
        self.assertEqual(dec_ovl.reason, "overlap")

        # Unknown speaker
        v_unk = SpeakerVerdict(profile_id=None, status="unknown")
        dec_unk = SpeakerPolicy.evaluate("gate", v_unk, is_voice=True, is_stop_command=False)
        self.assertFalse(dec_unk.allow)
        self.assertEqual(dec_unk.reason, "unknown")

        # No profiles enrolled
        v_np = SpeakerVerdict(profile_id=None, status="no_profiles")
        dec_np = SpeakerPolicy.evaluate("gate", v_np, is_voice=True, is_stop_command=False)
        self.assertFalse(dec_np.allow)
        self.assertEqual(dec_np.reason, "no_profiles")

        # Timeout / None verdict
        dec_none = SpeakerPolicy.evaluate("gate", None, is_voice=True, is_stop_command=False)
        self.assertFalse(dec_none.allow)
        self.assertEqual(dec_none.reason, "unavailable")

    def test_speaker_id_controller_creation_with_real_settings(self):
        from helpers_gsettings import get_real_settings
        from core.runtime_manager import DaemonRuntimeManager

        settings = get_real_settings()
        settings.set_string("speaker-id-mode", "gate")
        settings.set_double("speaker-id-threshold", 0.80)

        class DummyOwner:
            def __init__(self, s):
                self.settings = s
                self.speaker_id_controller = None
                self.pipeline_controller = None
                self.event_bus = None
                self.stt_engine = None
                self.llm_service = None
                self.provider_manager = None
                self.model_manager = None
                self.state_machine = None
                self.audio_player = None
                self.tts_manager = None
                self.mcp_manager = None
                self.models_dir = "/tmp"

            def _on_playback_finished(self):
                pass

            def set_state(self, s):
                pass

            def get_boolean_setting(self, key, default):
                return default

            def get(self, key, default=None):
                return default

            def notify_dependency_required(self, *args, **kwargs):
                pass

        owner = DummyOwner(settings)
        mgr = DaemonRuntimeManager(owner)
        mgr.initialize_services()

        self.assertIsNotNone(owner.speaker_id_controller)
        self.assertEqual(owner.speaker_id_controller.mode, "gate")
        self.assertAlmostEqual(owner.speaker_id_controller.threshold, 0.80)
        if owner.audio_player:
            owner.audio_player.stop()


if __name__ == "__main__":
    unittest.main()

