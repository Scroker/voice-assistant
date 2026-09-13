import os
import sys
import shutil
import tempfile
import time
import unittest
from typing import Optional
from unittest import mock
import numpy as np

# Add src/daemon to import path
daemon_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "daemon"))
if daemon_dir not in sys.path:
    sys.path.insert(0, daemon_dir)

from services.speaker_id.enrollment import EnrollmentRecorder
from services.speaker_id.profiles import SpeakerProfileStore


class MockEnrollmentBackend:
    name: str = "mock"
    sample_rate: int = 16000
    min_samples: int = 1600

    def __init__(self, available: bool = True, trim_all_silence: bool = False) -> None:
        self._available = available
        self.trim_all_silence = trim_all_silence

    def is_available(self) -> bool:
        return self._available

    def load(self) -> None:
        pass

    def unload(self) -> None:
        pass

    def preprocess(self, pcm: np.ndarray) -> np.ndarray:
        if self.trim_all_silence:
            return np.empty(0, dtype=np.float32)
        return pcm

    def embed(self, pcm: np.ndarray) -> Optional[np.ndarray]:
        if pcm is None or len(pcm) < self.min_samples:
            return None
        return np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)


class TestSpeakerEnrollment(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="va_test_enroll_")
        self.store = SpeakerProfileStore(self.test_dir, active_backend="mock")
        self.backend = MockEnrollmentBackend(available=True)
        self.progress_events = []
        self.finished_event = None

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _on_progress(self, progress: float, level: float):
        self.progress_events.append((progress, level))

    def _on_finished(self, success: bool, profile_id: str, message: str):
        self.finished_event = (success, profile_id, message)

    def test_start_unavailable_backend(self):
        self.backend._available = False
        recorder = EnrollmentRecorder(
            store=self.store,
            backend=self.backend,
            on_progress=self._on_progress,
            on_finished=self._on_finished,
        )
        started = recorder.start("Mario Rossi", duration_s=4.0)
        self.assertFalse(started)
        self.assertEqual(self.finished_event, (False, "", "unavailable"))

    def test_successful_enrollment(self):
        recorder = EnrollmentRecorder(
            store=self.store,
            backend=self.backend,
            on_progress=self._on_progress,
            on_finished=self._on_finished,
            sample_rate=16000,
            min_useful_speech_s=2.0,  # lower for test speed
        )
        started = recorder.start("Alice", duration_s=4.0)
        self.assertTrue(started)
        self.assertTrue(recorder.is_active)

        # Feed 4.0s of audio in 0.5s chunks (8000 samples each)
        chunk = (int(5000).to_bytes(2, "little", signed=True)) * 8000
        for _ in range(8):
            recorder.feed(chunk)

        # Wait for background completion thread
        for _ in range(50):
            if self.finished_event is not None:
                break
            time.sleep(0.02)

        self.assertIsNotNone(self.finished_event)
        success, profile_id, msg = self.finished_event
        self.assertTrue(success)
        self.assertEqual(profile_id, "alice")
        self.assertEqual(msg, "")

        # Verify profile is in store
        prof = self.store.get_profile("alice")
        self.assertIsNotNone(prof)
        self.assertEqual(prof["meta"]["display_name"], "Alice")
        self.assertGreater(len(self.progress_events), 0)

    def test_insufficient_speech_rejected(self):
        self.backend.trim_all_silence = True  # Simulates complete silence trimmed away
        recorder = EnrollmentRecorder(
            store=self.store,
            backend=self.backend,
            on_progress=self._on_progress,
            on_finished=self._on_finished,
            sample_rate=16000,
            min_useful_speech_s=2.0,
        )
        recorder.start("Quiet User", duration_s=4.0)
        chunk = (int(0).to_bytes(2, "little", signed=True)) * 8000
        for _ in range(8):
            recorder.feed(chunk)

        for _ in range(50):
            if self.finished_event is not None:
                break
            time.sleep(0.02)

        self.assertIsNotNone(self.finished_event)
        success, profile_id, msg = self.finished_event
        self.assertFalse(success)
        self.assertEqual(msg, "insufficient_speech")

    def test_cancel_enrollment(self):
        recorder = EnrollmentRecorder(
            store=self.store,
            backend=self.backend,
            on_progress=self._on_progress,
            on_finished=self._on_finished,
        )
        recorder.start("Cancel User", duration_s=8.0)
        cancelled = recorder.cancel()
        self.assertTrue(cancelled)
        self.assertEqual(self.finished_event, (False, "", "cancelled"))

    def test_check_timeout_no_audio_after_3s(self):
        """Bug: check_timeout() non veniva mai chiamato dal loop audio, quindi
        un enrollment senza più chunk in arrivo restava attivo per sempre."""
        recorder = EnrollmentRecorder(
            store=self.store,
            backend=self.backend,
            on_progress=self._on_progress,
            on_finished=self._on_finished,
        )
        with mock.patch("services.speaker_id.enrollment.time.monotonic", return_value=1000.0):
            recorder.start("No Audio User", duration_s=8.0)
            self.assertTrue(recorder.is_active)

        # Nessun chunk arriva: simula il passare di >3s dall'ultimo chunk (=avvio)
        with mock.patch("services.speaker_id.enrollment.time.monotonic", return_value=1003.5):
            recorder.check_timeout()

        self.assertFalse(recorder.is_active)
        self.assertEqual(self.finished_event, (False, "", "no_audio"))

    def test_check_timeout_does_nothing_before_3s(self):
        recorder = EnrollmentRecorder(
            store=self.store,
            backend=self.backend,
            on_progress=self._on_progress,
            on_finished=self._on_finished,
        )
        with mock.patch("services.speaker_id.enrollment.time.monotonic", return_value=2000.0):
            recorder.start("Still Recording User", duration_s=8.0)

        with mock.patch("services.speaker_id.enrollment.time.monotonic", return_value=2001.0):
            recorder.check_timeout()

        self.assertTrue(recorder.is_active)
        self.assertIsNone(self.finished_event)

    def test_start_downloading_backend_inhibited(self):
        class DownloadingBackend(MockEnrollmentBackend):
            def is_downloading(self):
                return True

        backend = DownloadingBackend(available=True)
        recorder = EnrollmentRecorder(
            store=self.store,
            backend=backend,
            on_progress=self._on_progress,
            on_finished=self._on_finished,
        )
        started = recorder.start("Download User", duration_s=4.0)
        self.assertFalse(started)
        self.assertEqual(self.finished_event, (False, "", "downloading"))

    def test_speaker_controller_download_inhibition(self):
        from core.speaker_runtime import SpeakerIdController

        notif_called = []

        def _notify(title, msg):
            notif_called.append((title, msg))

        ctrl = SpeakerIdController(
            backend=self.backend,
            store=self.store,
            is_downloading_fn=lambda: True,
            download_progress_fn=lambda: 45,
            on_notify_fn=_notify,
        )
        self.assertTrue(ctrl.is_downloading)
        status = ctrl.get_status()
        self.assertEqual(status["status"], "downloading")
        self.assertEqual(status["download_percent"], 45)

        started = ctrl.start_enrollment("Test Inhibit")
        self.assertFalse(started)
        self.assertEqual(len(notif_called), 1)
        self.assertIn("Download", notif_called[0][1])


if __name__ == "__main__":
    unittest.main()
