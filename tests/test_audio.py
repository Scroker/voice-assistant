import unittest
import sys
import numpy as np
from pathlib import Path

daemon_dir = Path(__file__).resolve().parent.parent / 'src' / 'daemon'
sys.path.insert(0, str(daemon_dir))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

try:
    from daemon.audio.vad import SilenceDetector
    from daemon.audio.player import AudioPlayer
except ImportError:
    from audio.vad import SilenceDetector
    from audio.player import AudioPlayer


class TestAudioModule(unittest.TestCase):
    def test_silence_detector_volume(self):
        detector = SilenceDetector(silence_timeout_sec=1.0, max_duration_sec=5.0, volume_threshold=100.0)
        
        # Test silent audio chunk
        silent_pcm = np.zeros(1600, dtype=np.int16).tobytes()
        res = detector.process_chunk(silent_pcm)
        self.assertFalse(res['is_speaking'])
        self.assertEqual(res['volume'], 0.0)

        # Test loud audio chunk
        loud_pcm = (np.ones(1600, dtype=np.int16) * 1000).tobytes()
        res = detector.process_chunk(loud_pcm)
        self.assertTrue(res['is_speaking'])
        self.assertGreater(res['volume'], 100.0)

    def test_audio_player_queue(self):
        player = AudioPlayer()
        player.start()
        self.assertTrue(player._running)
        
        # Enqueue empty audio bytes
        player.enqueue_audio(b"")
        player.stop()
        self.assertFalse(player._running)


if __name__ == '__main__':
    unittest.main()
