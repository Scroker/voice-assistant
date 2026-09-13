import os
import sys
import shutil
import tempfile
import time
import unittest
from typing import Optional
import numpy as np

# Add src/daemon to import path
daemon_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "daemon"))
if daemon_dir not in sys.path:
    sys.path.insert(0, daemon_dir)

from services.speaker_id.backend import SpeakerEmbeddingBackend
from services.speaker_id.identifier import SpeakerIdentifier, SpeakerMatch
from services.speaker_id.profiles import SpeakerProfileStore


class MockSpeakerBackend:
    """Deterministic mock embedding backend for testing."""

    name: str = "mock"
    sample_rate: int = 16000
    min_samples: int = 1600

    def __init__(self, available: bool = True) -> None:
        self._available = available
        self.preset_vector: Optional[np.ndarray] = None

    def is_available(self) -> bool:
        return self._available

    def load(self) -> None:
        pass

    def unload(self) -> None:
        pass

    def preprocess(self, pcm: np.ndarray) -> np.ndarray:
        return pcm

    def embed(self, pcm: np.ndarray) -> Optional[np.ndarray]:
        if pcm is None or len(pcm) < self.min_samples:
            return None
        if self.preset_vector is not None:
            return self.preset_vector
        # Deterministic vector based on pcm mean
        v = np.zeros(4, dtype=np.float32)
        idx = int(abs(np.mean(pcm) * 100)) % 4
        v[idx] = 1.0
        return v


class TestSpeakerIdentifier(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="va_test_id_")
        self.store = SpeakerProfileStore(self.test_dir, active_backend="mock")
        self.backend = MockSpeakerBackend(available=True)
        self.identifier = SpeakerIdentifier(
            backend=self.backend,
            store=self.store,
            threshold=0.75,
            margin=0.10,
        )

    def tearDown(self):
        self.identifier.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_status_no_profiles(self):
        pcm = np.ones(3200, dtype=np.float32)
        match, emb = self.identifier.match_pcm(pcm)
        self.assertEqual(match.status, "no_profiles")
        self.assertIsNotNone(emb)

    def test_status_insufficient_audio(self):
        pcm = np.ones(500, dtype=np.float32)  # Less than min_samples (1600)
        match, emb = self.identifier.match_pcm(pcm)
        self.assertEqual(match.status, "insufficient_audio")
        self.assertIsNone(emb)

    def test_status_unavailable(self):
        self.backend._available = False
        pcm = np.ones(3200, dtype=np.float32)
        match, emb = self.identifier.match_pcm(pcm)
        self.assertEqual(match.status, "unavailable")

    def test_match_recognition_and_unknown(self):
        # Enroll user Alice with vector [1, 0, 0, 0]
        alice_vec = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
        self.store.save_enrollment("Alice", alice_vec, backend_name="mock")

        # Audio matching Alice (score 1.0 >= 0.75)
        self.backend.preset_vector = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
        pcm = np.ones(3200, dtype=np.float32)
        match, emb = self.identifier.match_pcm(pcm)
        self.assertEqual(match.status, "recognized")
        self.assertEqual(match.display_name, "Alice")
        self.assertAlmostEqual(match.score, 1.0, places=4)

        # Audio of Bob [0, 1, 0, 0], orthogonal to Alice (score 0.0 < 0.75)
        self.backend.preset_vector = np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)
        match, emb = self.identifier.match_pcm(pcm)
        self.assertEqual(match.status, "unknown")
        self.assertAlmostEqual(match.score, 0.0, places=4)

    def test_submit_async_and_adaptation(self):
        alice_vec = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
        self.store.save_enrollment("Alice", alice_vec, backend_name="mock")

        # Vector with high similarity: score = 0.90 >= 0.75 + 0.05 -> adapts
        adapt_vec = np.array([0.95, 0.05, 0.0, 0.0], dtype=np.float32)
        adapt_vec /= np.linalg.norm(adapt_vec)
        self.backend.preset_vector = adapt_vec

        result = {}

        def on_result(match: SpeakerMatch):
            result["match"] = match

        pcm = np.ones(3200, dtype=np.float32)
        self.identifier.submit(pcm, on_result, is_final=True, allow_adaptation=True)

        for _ in range(50):
            if "match" in result:
                break
            time.sleep(0.02)

        self.assertIn("match", result)
        self.assertEqual(result["match"].status, "recognized")

        # Profile history should have 1 item now
        prof = self.store.get_profile("alice")
        self.assertEqual(len(prof["history"]), 1)


if __name__ == "__main__":
    unittest.main()
