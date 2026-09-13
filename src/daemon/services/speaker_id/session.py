"""Voice speaker identification session and overlap detection."""

from __future__ import annotations

import collections
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Deque, List, Optional
import numpy as np

from .identifier import SpeakerIdentifier, SpeakerMatch

logger = logging.getLogger("VoiceAssistant.SpeakerID.Session")

CONSECUTIVE_FOREIGN_WINDOWS_FOR_OVERLAP = 2
WINDOW_DURATION_SEC = 1.5
WINDOW_STEP_SEC = 0.5


class SpeakerVerdict:
    """Consolidated identification outcome for an entire spoken utterance."""

    def __init__(
        self,
        match: Optional[SpeakerMatch] = None,
        wakeword_match: Optional[SpeakerMatch] = None,
        overlap_detected: bool = False,
        foreign_windows: int = 0,
        origin: str = "manual",
        timed_out: bool = False,
        profile_id: Optional[str] = None,
        display_name: Optional[str] = None,
        score: float = 0.0,
        status: Optional[str] = None,
    ) -> None:
        if match is None:
            self.match = SpeakerMatch(
                profile_id=profile_id,
                display_name=display_name,
                score=score,
                status=status or "unknown",
            )
        else:
            self.match = match
            if profile_id is not None:
                self.match.profile_id = profile_id
            if display_name is not None:
                self.match.display_name = display_name
            if score != 0.0:
                self.match.score = score
            if status is not None:
                self.match.status = status

        self.wakeword_match = wakeword_match
        self.overlap_detected = overlap_detected
        self.foreign_windows = foreign_windows
        self.origin = origin
        self.timed_out = timed_out

    @property
    def status(self) -> str:
        return self.match.status if self.match else "unknown"

    @property
    def profile_id(self) -> Optional[str]:
        return self.match.profile_id if self.match else None

    @property
    def display_name(self) -> Optional[str]:
        return self.match.display_name if self.match else None

    @property
    def score(self) -> float:
        return self.match.score if self.match else 0.0


class PreRollBuffer:
    """Thread-safe circular pre-roll buffer storing raw PCM int16 chunks."""

    def __init__(
        self,
        max_chunks: int = 4,
        max_seconds: Optional[float] = None,
        sample_rate: int = 16000,
    ) -> None:
        if max_seconds is not None:
            max_chunks = max(1, int(round(max_seconds / 0.5)))
        self._chunks: Deque[bytes] = collections.deque(maxlen=max_chunks)
        self._lock = threading.Lock()
        self.sample_rate = sample_rate

    def append(self, raw_chunk: bytes) -> None:
        """Store a raw PCM audio chunk."""
        if not raw_chunk:
            return
        with self._lock:
            self._chunks.append(raw_chunk)

    def feed(self, raw_chunk: bytes) -> None:
        """Alias for append."""
        self.append(raw_chunk)

    def snapshot(self) -> bytes:
        """Return a combined copy of all buffered pre-roll chunks."""
        with self._lock:
            return b"".join(self._chunks)

    def get_data(self) -> bytes:
        """Alias for snapshot."""
        return self.snapshot()

    def get_snapshot(self) -> List[bytes]:
        """Return a list copy of chunks in the buffer."""
        with self._lock:
            return list(self._chunks)

    def clear(self) -> None:
        """Empty the pre-roll buffer."""
        with self._lock:
            self._chunks.clear()


class VoiceSpeakerSession:
    """Tracks speaker verification across one listening session."""

    def __init__(
        self,
        identifier: SpeakerIdentifier,
        pre_roll_buffer: Optional[PreRollBuffer] = None,
        noise_floor_getter: Optional[callable] = None,
        preroll_buffer: Optional[PreRollBuffer] = None,
        sample_rate: int = 16000,
        ready_event: Optional[threading.Event] = None,
    ) -> None:
        self.identifier = identifier
        self.pre_roll_buffer = pre_roll_buffer if pre_roll_buffer is not None else preroll_buffer
        self.noise_floor_getter = noise_floor_getter
        self.sample_rate = sample_rate
        self._ready_event = ready_event

        self.origin: str = "manual"
        self._pre_roll_bytes: bytes = b""
        self._sentence_chunks: List[bytes] = []

        self._lock = threading.Lock()
        self._cancelled = False
        self._started = False

        self._ww_match_event = threading.Event()
        self._ww_match: Optional[SpeakerMatch] = None
        self._ww_embedding: Optional[np.ndarray] = None

        self._final_match_event = threading.Event()
        self._final_match: Optional[SpeakerMatch] = None
        self._final_embedding: Optional[np.ndarray] = None

        self._overlap_detected = False
        self._consecutive_foreign = 0
        self._total_foreign_windows = 0
        self._window_step_samples = int(16000 * WINDOW_STEP_SEC)
        self._window_size_samples = int(16000 * WINDOW_DURATION_SEC)
        self._overlap_buffer_pcm = np.array([], dtype=np.float32)

        self._next_window_seq = 0
        self._expected_window_seq = 0
        self._pending_windows: collections.OrderedDict[int, str] = collections.OrderedDict()

    @property
    def _audio_chunks(self) -> List[bytes]:
        with self._lock:
            res = []
            if self._pre_roll_bytes:
                res.append(self._pre_roll_bytes)
            res.extend(self._sentence_chunks)
            return res

    def start(self, origin: str = "manual") -> None:
        """Begin session, capture pre-roll buffer, and start background wakeword evaluation."""
        with self._lock:
            self.origin = origin
            self._started = True
            self._cancelled = False
            self._sentence_chunks = []
            self._overlap_buffer_pcm = np.array([], dtype=np.float32)
            self._consecutive_foreign = 0
            self._total_foreign_windows = 0
            self._overlap_detected = False
            self._next_window_seq = 0
            self._expected_window_seq = 0
            self._pending_windows.clear()
            self._ww_match_event.clear()
            self._final_match_event.clear()

            if origin == "wakeword" and self.pre_roll_buffer:
                self._pre_roll_bytes = self.pre_roll_buffer.snapshot()
            else:
                self._pre_roll_bytes = b""

        # Evaluate wakeword audio from pre-roll asynchronously
        if self._pre_roll_bytes:
            preroll_pcm = self._bytes_to_float32(self._pre_roll_bytes)
            def _on_ww_match(match: SpeakerMatch, emb: Optional[np.ndarray]):
                with self._lock:
                    self._ww_match = match
                    self._ww_embedding = emb
                    self._ww_match_event.set()
            self.identifier.submit(preroll_pcm, _on_ww_match, is_final=False)
        else:
            self._ww_match_event.set()

    def feed(self, raw_chunk: bytes) -> None:
        """Feed an incoming raw PCM int16 audio chunk from listening loop."""
        with self._lock:
            if self._cancelled or not self._started:
                return
            if not raw_chunk:
                return
            self._sentence_chunks.append(raw_chunk)

        # Check overlap incrementally if wakeword matched
        self._check_overlap_incremental(raw_chunk)

    def _check_overlap_incremental(self, chunk_bytes: bytes) -> None:
        """Perform overlap sliding window detection if wakeword speaker was recognized."""
        if not self._ww_match_event.is_set():
            return

        with self._lock:
            if self._overlap_detected:
                return
            ww_match = self._ww_match

        if not ww_match or ww_match.status != "recognized" or not ww_match.profile_id:
            return

        chunk_float = self._bytes_to_float32(chunk_bytes)
        with self._lock:
            self._overlap_buffer_pcm = np.concatenate([self._overlap_buffer_pcm, chunk_float])
            if len(self._overlap_buffer_pcm) < self._window_size_samples:
                return

            window = self._overlap_buffer_pcm[:self._window_size_samples]
            self._overlap_buffer_pcm = self._overlap_buffer_pcm[self._window_step_samples:]

        # Check if window is voiced (RMS > max(150.0 int16 level, 2.0 * noise_floor))
        rms = float(np.sqrt(np.mean(window ** 2))) * 32768.0
        noise_floor = 75.0
        if self.noise_floor_getter:
            try:
                noise_floor = float(self.noise_floor_getter())
            except Exception:
                pass

        min_rms = max(150.0, 2.0 * noise_floor)
        if rms < min_rms:
            return

        # Submit window for evaluation against reference embedding
        ref_emb = self.identifier.store.reference_embedding(ww_match.profile_id)
        if ref_emb is None:
            return

        with self._lock:
            seq = self._next_window_seq
            self._next_window_seq += 1

        def _process_pending_windows_locked():
            while self._expected_window_seq in self._pending_windows:
                status = self._pending_windows.pop(self._expected_window_seq)
                self._expected_window_seq += 1
                if status == "foreign":
                    self._consecutive_foreign += 1
                    self._total_foreign_windows += 1
                    if self._consecutive_foreign >= CONSECUTIVE_FOREIGN_WINDOWS_FOR_OVERLAP:
                        self._overlap_detected = True
                        logger.warning(
                            "Speaker overlap detected: %d consecutive foreign windows",
                            self._consecutive_foreign,
                        )
                elif status == "match":
                    self._consecutive_foreign = 0
                else:  # uncertain
                    self._consecutive_foreign = 0

        def _on_window_match(match: SpeakerMatch, emb: Optional[np.ndarray]):
            if emb is None:
                status = "uncertain"
            else:
                sim = float(np.dot(emb, ref_emb))
                th = self.identifier.threshold
                margin = self.identifier.margin
                if sim < (th - margin):
                    status = "foreign"
                elif sim >= th:
                    status = "match"
                else:
                    status = "uncertain"

            with self._lock:
                self._pending_windows[seq] = status
                _process_pending_windows_locked()

        submitted = self.identifier.submit(window, _on_window_match, is_final=False)
        if not submitted:
            # Dropped windows count as uncertain
            with self._lock:
                self._pending_windows[seq] = "uncertain"
                _process_pending_windows_locked()

    def finalize(self, timeout_s: float = 1.5) -> SpeakerVerdict:
        """Combine pre-roll and utterance, compute final verdict with timeout."""
        with self._lock:
            if self._cancelled:
                return SpeakerVerdict(
                    match=SpeakerMatch(status="unavailable"),
                    origin=self.origin,
                    timed_out=False,
                )

        if self._ready_event is not None and not self._ready_event.is_set():
            logger.info("Attesa warm_up modello speaker prima di finalize (max 8.0s)...")
            is_ready = self._ready_event.wait(timeout=8.0)
            if not is_ready or (hasattr(self.identifier.backend, "is_available") and not self.identifier.backend.is_available()):
                logger.warning("Speaker model warm_up non pronto o backend non disponibile.")
                return SpeakerVerdict(
                    match=SpeakerMatch(status="unavailable"),
                    origin=self.origin,
                    timed_out=False,
                )

        with self._lock:
            combined_bytes = self._pre_roll_bytes + b"".join(self._sentence_chunks)

        full_pcm = self._bytes_to_float32(combined_bytes)

        def _on_final(match: SpeakerMatch, emb: Optional[np.ndarray]):
            with self._lock:
                self._final_match = match
                self._final_embedding = emb
                self._final_match_event.set()

        # Submit final match
        submitted = self.identifier.submit(full_pcm, _on_final, is_final=True)
        if not submitted:
            return SpeakerVerdict(
                match=SpeakerMatch(status="unavailable"),
                origin=self.origin,
                timed_out=False,
            )

        # Wait for completion
        got_final = self._final_match_event.wait(timeout=max(0.1, timeout_s))

        with self._lock:
            timed_out = not got_final
            final_match = self._final_match if got_final and self._final_match else SpeakerMatch(status="unavailable")
            final_emb = self._final_embedding if got_final else None
            overlap = self._overlap_detected
            foreign_count = self._total_foreign_windows
            ww_match = self._ww_match

        # Update profile history adaptively if eligible
        if got_final and final_match.status == "recognized":
            self.identifier.update_history_if_eligible(final_match, final_emb, overlap_detected=overlap)

        return SpeakerVerdict(
            match=final_match,
            wakeword_match=ww_match,
            overlap_detected=overlap,
            foreign_windows=foreign_count,
            origin=self.origin,
            timed_out=timed_out,
        )

    def cancel(self) -> None:
        """Cancel the session and discard any ongoing analysis."""
        with self._lock:
            self._cancelled = True
            self._sentence_chunks.clear()
            self._pre_roll_bytes = b""
            self._overlap_buffer_pcm = np.array([], dtype=np.float32)
            self._ww_match_event.set()
            self._final_match_event.set()

    @staticmethod
    def _bytes_to_float32(pcm_bytes: bytes) -> np.ndarray:
        if not pcm_bytes:
            return np.array([], dtype=np.float32)
        int16_arr = np.frombuffer(pcm_bytes, dtype=np.int16)
        return (int16_arr.astype(np.float32) / 32768.0)
