"""Voice enrollment recorder for Speaker Identification."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, List, Optional
import numpy as np

from .backend import SpeakerEmbeddingBackend
from .profiles import SpeakerProfileStore

logger = logging.getLogger("VoiceAssistant.SpeakerID.Enrollment")


class EnrollmentRecorder:
    """Records microphone audio for voice enrollment, monitors levels, and creates profiles."""

    def __init__(
        self,
        store: SpeakerProfileStore,
        backend: SpeakerEmbeddingBackend,
        on_progress: Optional[Callable[[float, float], None]] = None,
        on_finished: Optional[Callable[[bool, str, str], None]] = None,
        sample_rate: int = 16000,
        min_useful_speech_s: float = 4.0,
    ) -> None:
        self.store = store
        self.backend = backend
        self.on_progress = on_progress
        self.on_finished = on_finished
        self.sample_rate = sample_rate
        self.min_useful_speech_s = min_useful_speech_s

        self._lock = threading.Lock()
        self._is_active = False
        self._cancel_requested = False
        self._display_name = ""
        self._duration_s = 8.0
        self._start_time = 0.0
        self._last_chunk_time = 0.0
        self._chunks: List[np.ndarray] = []
        self._total_samples = 0
        self._completion_thread: Optional[threading.Thread] = None

    @property
    def is_active(self) -> bool:
        """Return True if an enrollment session is actively recording or processing."""
        with self._lock:
            return self._is_active

    def start(self, display_name: str, duration_s: float = 8.0, username: Optional[str] = None) -> bool:
        """Start recording enrollment audio."""
        with self._lock:
            if self._is_active:
                logger.warning("Enrollment already active, ignoring start request.")
                return False

            if hasattr(self.backend, "is_downloading") and self.backend.is_downloading():
                logger.warning("Cannot start enrollment: embedding backend model is downloading.")
                self._notify_finished(False, "", "downloading")
                return False

            if not self.backend.is_available():
                logger.error("Cannot start enrollment: embedding backend unavailable.")
                self._notify_finished(False, "", "unavailable")
                return False

            self._is_active = True
            self._cancel_requested = False
            self._display_name = display_name.strip()
            self._username = (username or "").strip()
            self._duration_s = max(4.0, float(duration_s))
            self._start_time = time.monotonic()
            self._last_chunk_time = self._start_time
            self._chunks = []
            self._total_samples = 0
            self._completion_thread = None

            logger.info(
                "Started speaker enrollment for '%s' (user: '%s', target: %.1fs)",
                self._display_name,
                self._username,
                self._duration_s,
            )
            return True

    def feed(self, chunk: bytes) -> None:
        """Feed raw audio chunk from microphone loop (16kHz 16-bit mono PCM)."""
        notify_prog = None
        start_worker = False
        chunks_to_process = []
        display_name = ""
        username = ""

        with self._lock:
            if not self._is_active:
                return

            now = time.monotonic()
            self._last_chunk_time = now

            if not chunk:
                return

            pcm = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
            if len(pcm) == 0:
                return

            self._chunks.append(pcm)
            self._total_samples += len(pcm)

            # Compute RMS level (normalized 0..1, typical speech max RMS ~0.3)
            rms = float(np.sqrt(np.mean(pcm ** 2)))
            level = min(1.0, max(0.0, rms / 0.3))

            target_samples = int(self._duration_s * self.sample_rate)
            progress = min(1.0, float(self._total_samples) / float(target_samples))
            notify_prog = (progress, level)

            if self._total_samples >= target_samples:
                logger.info("Enrollment audio collection finished (%d samples). Processing...", self._total_samples)
                self._is_active = False
                chunks_to_process = list(self._chunks)
                display_name = self._display_name
                username = self._username
                start_worker = True

        if notify_prog:
            self._notify_progress(notify_prog[0], notify_prog[1])

        if start_worker:
            self._completion_thread = threading.Thread(
                target=self._process_completion,
                args=(chunks_to_process, display_name, username),
                daemon=True,
                name="SpeakerEnrollmentWorker",
            )
            self._completion_thread.start()

    def check_timeout(self) -> None:
        """Safety timeout check. Call periodically if audio stream might stall."""
        notify_timeout = False
        with self._lock:
            if not self._is_active:
                return

            now = time.monotonic()
            no_chunk = (now - self._last_chunk_time > 3.0)
            total_timeout = (now - self._start_time > self._duration_s + 3.0)
            if no_chunk or total_timeout:
                logger.warning("Enrollment timed out: no audio received (chunk=%.1fs, total=%.1fs).",
                               now - self._last_chunk_time, now - self._start_time)
                self._is_active = False
                self._chunks.clear()
                notify_timeout = True

        if notify_timeout:
            self._notify_finished(False, "", "no_audio")

    def cancel(self) -> bool:
        """Cancel the ongoing enrollment and discard recorded audio."""
        notify_cancelled = False
        with self._lock:
            was_active = self._is_active or (self._completion_thread is not None and self._completion_thread.is_alive())
            if not was_active or self._cancel_requested:
                return False

            self._cancel_requested = True
            self._is_active = False
            self._chunks.clear()
            logger.info("Speaker enrollment cancelled by user.")
            notify_cancelled = True

        if notify_cancelled:
            self._notify_finished(False, "", "cancelled")
        return True

    def _process_completion(self, chunks: List[np.ndarray], display_name: str, username: Optional[str] = None) -> None:
        """Background worker that trims silence, checks duration, and computes anchor embedding."""
        try:
            with self._lock:
                if self._cancel_requested:
                    logger.info("Enrollment cancelled prior to worker processing.")
                    return

            if not chunks:
                self._notify_finished(False, "", "insufficient_speech")
                return

            full_pcm = np.concatenate(chunks)

            # Preprocess audio (trim silence)
            if hasattr(self.backend, "preprocess"):
                try:
                    useful_pcm = self.backend.preprocess(full_pcm)
                except Exception as exc:
                    logger.error("Backend preprocess failed during enrollment: %s", exc)
                    useful_pcm = full_pcm
            else:
                useful_pcm = full_pcm

            useful_duration_s = float(len(useful_pcm)) / float(self.sample_rate)
            logger.info(
                "Enrollment recorded %.1fs total audio, useful speech: %.2fs (min required: %.1fs)",
                float(len(full_pcm)) / float(self.sample_rate),
                useful_duration_s,
                self.min_useful_speech_s,
            )

            if useful_duration_s < self.min_useful_speech_s:
                with self._lock:
                    if self._cancel_requested:
                        return
                logger.warning(
                    "Useful speech too short (%.2fs < %.1fs). Enrollment rejected.",
                    useful_duration_s,
                    self.min_useful_speech_s,
                )
                self._notify_finished(False, "", "insufficient_speech")
                return

            # Compute speaker embedding anchor
            emb = self.backend.embed(useful_pcm)
            with self._lock:
                if self._cancel_requested:
                    logger.info("Enrollment cancelled after embedding computation.")
                    return

            if emb is None:
                logger.warning("Failed to extract embedding from enrollment audio.")
                self._notify_finished(False, "", "insufficient_speech")
                return

            # Save enrollment profile
            profile_id = self.store.save_enrollment(
                display_name=display_name,
                anchor=emb,
                backend_name=self.backend.name,
                username=username,
            )
            with self._lock:
                if self._cancel_requested:
                    logger.info("Enrollment cancelled during save: delete created profile.")
                    self.store.delete_profile(profile_id)
                    return

            logger.info("Successfully enrolled speaker '%s' with profile_id '%s'", display_name, profile_id)
            self._notify_finished(True, profile_id, "")

        except Exception as exc:
            logger.exception("Unexpected error in enrollment processing: %s", exc)
            self._notify_finished(False, "", "unavailable")

    def _notify_progress(self, progress: float, level: float) -> None:
        if self.on_progress:
            try:
                self.on_progress(float(progress), float(level))
            except Exception as exc:
                logger.debug("Error in on_progress callback: %s", exc)

    def _notify_finished(self, success: bool, profile_id: str, message: str) -> None:
        if self.on_finished:
            try:
                self.on_finished(bool(success), str(profile_id), str(message))
            except Exception as exc:
                logger.debug("Error in on_finished callback: %s", exc)
