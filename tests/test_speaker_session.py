import os
import sys
import shutil
import tempfile
import unittest
from typing import Optional
import numpy as np

# Add src/daemon to import path
daemon_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "daemon"))
if daemon_dir not in sys.path:
    sys.path.insert(0, daemon_dir)

from services.speaker_id.identifier import SpeakerIdentifier, SpeakerMatch
from services.speaker_id.profiles import SpeakerProfileStore
from services.speaker_id.session import PreRollBuffer, VoiceSpeakerSession


class MockBackendForSession:
    name: str = "mock"
    sample_rate: int = 16000
    min_samples: int = 1600

    def is_available(self) -> bool:
        return True

    def load(self) -> None:
        pass

    def unload(self) -> None:
        pass

    def preprocess(self, pcm: np.ndarray) -> np.ndarray:
        return pcm

    def embed(self, pcm: np.ndarray) -> Optional[np.ndarray]:
        if pcm is None or len(pcm) < self.min_samples:
            return None
        # Return vector based on amplitude to distinguish foreign vs target
        mean_val = float(np.mean(np.abs(pcm)))
        if mean_val > 0.5:
            return np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)  # Foreign
        return np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)  # Target


class TestSpeakerSession(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="va_test_session_")
        self.store = SpeakerProfileStore(self.test_dir, active_backend="mock")
        # Save profile for User (vector [1, 0, 0, 0])
        self.store.save_enrollment("TargetUser", np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32), backend_name="mock")

        self.backend = MockBackendForSession()
        self.identifier = SpeakerIdentifier(backend=self.backend, store=self.store, threshold=0.75)
        self.preroll = PreRollBuffer(max_seconds=2.0, sample_rate=16000)

    def tearDown(self):
        self.identifier.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_preroll_buffer_capacity(self):
        # 16000 samples/sec * 2 bytes = 32000 bytes/sec
        # Feed 3 seconds of data in 0.5s chunks (8000 samples = 16000 bytes)
        chunk = b"\x00\x01" * 8000
        for _ in range(6):  # 3.0s total
            self.preroll.feed(chunk)

        snapshot = self.preroll.get_snapshot()
        # Max capacity is 2.0s -> exactly 4 chunks of 0.5s
        self.assertEqual(len(snapshot), 4)

    def test_wakeword_origin_uses_preroll(self):
        # Fill preroll
        chunk = b"\x00\x02" * 8000
        self.preroll.feed(chunk)

        session = VoiceSpeakerSession(
            identifier=self.identifier,
            preroll_buffer=self.preroll,
            sample_rate=16000,
        )
        session.start(origin="wakeword")

        # Verify session has initial samples from preroll
        self.assertGreater(len(session._audio_chunks), 0)

    def test_manual_origin_discards_preroll(self):
        chunk = b"\x00\x02" * 8000
        self.preroll.feed(chunk)

        session = VoiceSpeakerSession(
            identifier=self.identifier,
            preroll_buffer=self.preroll,
            sample_rate=16000,
        )
        session.start(origin="manual")

        # Preroll should be discarded
        self.assertEqual(len(session._audio_chunks), 0)

    def test_finalize_recognized(self):
        session = VoiceSpeakerSession(
            identifier=self.identifier,
            preroll_buffer=self.preroll,
            sample_rate=16000,
        )
        session.start(origin="manual")

        # Feed 1.5s of low amplitude audio (TargetUser)
        # int16 values ~5000 -> float32 ~0.15
        val = int(5000).to_bytes(2, "little", signed=True)
        chunk = val * 8000  # 0.5s
        for _ in range(3):
            session.feed(chunk)

        verdict = session.finalize(timeout_s=2.0)
        self.assertEqual(verdict.status, "recognized")
        self.assertEqual(verdict.display_name, "TargetUser")
        self.assertFalse(verdict.overlap_detected)

    def test_session_cancel(self):
        session = VoiceSpeakerSession(
            identifier=self.identifier,
            preroll_buffer=self.preroll,
            sample_rate=16000,
        )
        session.start(origin="manual")
        chunk = b"\x00\x01" * 8000
        session.feed(chunk)
        session.cancel()

        self.assertTrue(session._cancelled)
        self.assertEqual(len(session._audio_chunks), 0)


if __name__ == "__main__":
    unittest.main()
