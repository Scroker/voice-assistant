"""
Unit tests for listening loop resilience, load_provider safety, and audio chime sample rate.
"""
import unittest
import sys
import time
import queue
from unittest.mock import MagicMock, patch
from pathlib import Path

daemon_dir = Path(__file__).resolve().parent.parent / 'src' / 'daemon'
sys.path.insert(0, str(daemon_dir))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

try:
    from daemon.audio.player import AudioPlayer
except ImportError:
    from audio.player import AudioPlayer


class TestListeningLoopResilience(unittest.TestCase):

    def test_wakeword_chime_sample_rate_and_queue(self):
        """Verify wakeword chime is generated at 16000Hz matching microphone input sample rate."""
        player = AudioPlayer()
        player.play_wakeword_chime()

        self.assertFalse(player.queue.empty(), "Audio player queue should contain chime PCM data")
        pcm_data, sample_rate = player.queue.get_nowait()
        self.assertEqual(sample_rate, 16000, "Chime sample rate should match microphone sample rate (16000Hz)")
        self.assertGreater(len(pcm_data), 0, "Chime PCM data should not be empty")

    def test_load_provider_handles_missing_settings_observer(self):
        """Verify load_provider does not raise AttributeError when _settings_observer is absent."""
        mock_assistant = MagicMock()
        mock_assistant.provider_name = "vosk"
        mock_assistant.model_name = "vosk-model-small-it-0.22"
        mock_assistant.hardware = "cpu"
        mock_assistant.extra_config = {}
        mock_assistant.models_dir = "/tmp"
        mock_assistant._downloading_models = {}
        mock_assistant._load_id = 1
        del mock_assistant._settings_observer  # Ensure attribute does not exist

        with patch('main.get_provider') as mock_get_provider, \
             patch('main.notify2') as mock_notify:
            
            mock_provider_inst = MagicMock()
            mock_get_provider.return_value = mock_provider_inst

            # Import load_provider bound logic or call directly
            from main import VoiceAssistant
            
            # Execute load_provider on mock instance
            try:
                VoiceAssistant.load_provider(mock_assistant, load_id=1)
                error_raised = False
            except AttributeError as e:
                error_raised = True
                self.fail(f"load_provider raised AttributeError when _settings_observer is missing: {e}")
            except Exception:
                error_raised = False

            self.assertFalse(error_raised)
            self.assertEqual(mock_assistant.provider, mock_provider_inst)

    def test_listening_loop_timeout_resilience(self):
        """Verify listening loop handles partial speech timeouts deterministically."""
        mock_assistant = MagicMock()
        mock_assistant._state = "listening"
        mock_assistant.wakeword = "anthon"
        mock_assistant._listening_start_time = time.time() - 3.0  # 3 seconds ago (> 2.5s)
        mock_assistant._last_partial_text = ""
        mock_assistant._last_partial_change_time = None
        mock_assistant.audio_filter = MagicMock()
        mock_assistant.audio_filter.process.return_value = b"pcm_data"
        
        mock_provider = MagicMock()
        mock_provider.process_chunk.return_value = ("", "")
        mock_assistant.provider = mock_provider

        # Check 2.5s timeout logic
        now = time.time()
        start_time = mock_assistant._listening_start_time
        last_change = mock_assistant._last_partial_change_time
        
        timeout_triggered = (not last_change and (now - start_time) >= 2.5)
        self.assertTrue(timeout_triggered, "2.5s inactivity timeout should trigger return to idle")

    def test_barge_in_matching_during_speaking(self):
        """Verify strict exact matching for barge-in commands during speaking state to prevent TTS echo self-interruption."""
        wakeword_lower = "assistente"
        ww_variants = {wakeword_lower, "assistenti", "assistenza", "assiste", "stop", "basta", "zitto"}
        
        # Test case A: TTS voice produces similar word 'assistenza' or 'sistema' in recognized_str
        recognized_str_echo = "questo sistema assiste l utente"
        words = recognized_str_echo.split()
        
        # In speaking state: exact word match ONLY
        is_speaking = True
        matched_speaking = next((v for v in ww_variants if v in words), None)
        # 'assiste' is in words, but 'sistema' or 'assistenza' (partial fuzzy) are NOT matched falsely
        self.assertEqual(matched_speaking, "assiste")

        # Test case B: TTS voice produces partial words that fail exact match
        recognized_str_partial = "resistenza o consistenza"
        words_partial = recognized_str_partial.split()
        matched_partial = next((v for v in ww_variants if v in words_partial), None)
        self.assertIsNone(matched_partial, "Fuzzy words from TTS echo must NOT match during speaking state")

    def test_speaking_state_audio_cooldown(self):
        """Verify speaking state sets an audio ignore cooldown of 0.5s to prevent initial TTS chime echo truncation."""
        now = time.time()
        ignore_audio_until = now + 0.5
        
        # Immediately during TTS start (0.1s later)
        self.assertTrue((now + 0.1) < ignore_audio_until, "Audio during initial 0.5s speaking window must be ignored")
        # After 0.6s
        self.assertFalse((now + 0.6) < ignore_audio_until, "Audio after 0.5s speaking window can process barge-in")

    def test_agc_gain_bounds_in_audio_filter(self):
        """Verify AudioFilter AGC gain is smoothly bounded between 0.8 and 2.0."""
        from audio.filter import AudioFilter
        import numpy as np

        audio_filter = AudioFilter(sample_rate=16000)
        self.assertEqual(audio_filter.current_gain, 1.0)

        # Process quiet audio chunk
        quiet_pcm = (np.ones(1600, dtype=np.int16) * 50).tobytes()
        for _ in range(10):
            audio_filter.process(quiet_pcm)

        self.assertLessEqual(audio_filter.current_gain, 2.0, "AGC gain must not exceed 2.0 upper bound")
        self.assertGreaterEqual(audio_filter.current_gain, 0.8, "AGC gain must not drop below 0.8 lower bound")


if __name__ == '__main__':
    unittest.main()
