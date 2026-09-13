"""Asynchronous speaker identification worker and similarity matching."""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple
import numpy as np

from .backend import SpeakerEmbeddingBackend
from .profiles import SpeakerProfileStore

logger = logging.getLogger("VoiceAssistant.SpeakerID.Identifier")


@dataclass
class SpeakerMatch:
    """Result of matching an audio segment against known speaker profiles."""

    profile_id: Optional[str] = None
    display_name: Optional[str] = None
    score: float = 0.0
    status: str = "unknown"  # "recognized" | "unknown" | "insufficient_audio" | "no_profiles" | "unavailable"


class SpeakerIdentifier:
    """Performs background embedding calculation and profile similarity matching."""

    def __init__(
        self,
        backend: SpeakerEmbeddingBackend,
        store: SpeakerProfileStore,
        threshold: float = 0.75,
        margin: float = 0.10,
    ) -> None:
        self.backend = backend
        self.store = store
        self.threshold = threshold
        self.margin = margin

        self._queue: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True, name="SpeakerIdentifierWorker")
        self._worker_thread.start()

    def set_threshold(self, threshold: float) -> None:
        """Update match threshold dynamically."""
        with self._lock:
            self.threshold = max(0.50, min(0.95, float(threshold)))
            logger.info("Speaker ID threshold updated to: %.2f", self.threshold)

    def match_pcm(self, pcm: np.ndarray) -> Tuple[SpeakerMatch, Optional[np.ndarray]]:
        """Compute embedding and match synchronously against store profiles.

        Returns:
            Tuple of (SpeakerMatch, embedding_vector_or_None)
        """
        if not self.backend.is_available():
            return SpeakerMatch(status="unavailable"), None

        try:
            emb = self.backend.embed(pcm)
        except Exception as exc:
            logger.error("Error computing embedding during match: %s", exc)
            return SpeakerMatch(status="unavailable"), None

        if emb is None:
            return SpeakerMatch(status="insufficient_audio"), None

        if hasattr(self.store, "get_all_cached_references"):
            cached_refs = self.store.get_all_cached_references()
            if not cached_refs:
                return SpeakerMatch(score=0.0, status="no_profiles"), emb
            best_profile_id = None
            best_name = None
            best_score = -1.0
            for pid, dname, ref_emb in cached_refs:
                sim = float(np.dot(emb, ref_emb))
                if sim > best_score:
                    best_score = sim
                    best_profile_id = pid
                    best_name = dname
        else:
            profiles = self.store.list_profiles()
            valid_profiles = [p for p in profiles if not p.get("needs_reenroll")]
            if not valid_profiles:
                return SpeakerMatch(score=0.0, status="no_profiles"), emb

            best_profile_id = None
            best_name = None
            best_score = -1.0

            for p in valid_profiles:
                pid = p["id"]
                ref_emb = self.store.reference_embedding(pid)
                if ref_emb is None:
                    continue

                sim = float(np.dot(emb, ref_emb))
                if sim > best_score:
                    best_score = sim
                    best_profile_id = pid
                    best_name = p.get("display_name") or pid

        with self._lock:
            th = self.threshold

        if best_score >= th and best_profile_id:
            match = SpeakerMatch(
                profile_id=best_profile_id,
                display_name=best_name,
                score=best_score,
                status="recognized",
            )
        else:
            match = SpeakerMatch(
                profile_id=best_profile_id,
                display_name=best_name,
                score=max(0.0, best_score),
                status="unknown",
            )

        return match, emb

    def submit(
        self,
        pcm: np.ndarray,
        callback: Callable[..., None],
        is_final: bool = False,
        allow_adaptation: bool = False,
    ) -> bool:
        """Submit an audio chunk for background embedding and matching.

        If is_final is False, drops the task if the queue already has >= 2 pending items.
        """
        if not is_final and self._queue.qsize() >= 2:
            logger.debug("Dropping intermediate window matching task (queue busy: %d)", self._queue.qsize())
            return False

        self._queue.put((pcm, callback, is_final, allow_adaptation))
        return True

    def update_history_if_eligible(
        self,
        match: SpeakerMatch,
        embedding: Optional[np.ndarray],
        overlap_detected: bool = False,
    ) -> bool:
        """Adaptively update profile history on high-confidence match without overlap."""
        if not match or match.status != "recognized" or not match.profile_id:
            return False
        if embedding is None:
            return False
        if overlap_detected:
            return False

        with self._lock:
            eligible_score = self.threshold + 0.05

        if match.score >= eligible_score:
            logger.info(
                "Adapting speaker profile '%s' history (score %.2f >= %.2f)",
                match.profile_id,
                match.score,
                eligible_score,
            )
            return self.store.add_history(match.profile_id, embedding)

        return False

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                item = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue

            if item is None:
                self._queue.task_done()
                break

            pcm, callback, is_final, allow_adaptation = item
            try:
                match, emb = self.match_pcm(pcm)
                if allow_adaptation and match.status == "recognized":
                    self.update_history_if_eligible(match, emb, overlap_detected=False)
                try:
                    callback(match, emb)
                except TypeError:
                    callback(match)
            except Exception as exc:
                logger.error("Error in SpeakerIdentifier worker loop: %s", exc)
                try:
                    try:
                        callback(SpeakerMatch(status="unavailable"), None)
                    except TypeError:
                        callback(SpeakerMatch(status="unavailable"))
                except Exception:
                    pass
            finally:
                self._queue.task_done()

    def stop(self) -> None:
        """Stop worker thread and empty queue."""
        self._stop_event.set()
        self._queue.put(None)
        if self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2.0)
