"""Speaker Identification runtime controller.

Coordinates the profile store, embedding backend, asynchronous identifier,
pre-roll buffer, enrollment recorder, and decision policy.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from services.speaker_id import (
    EnrollmentRecorder,
    PreRollBuffer,
    SpeakerDecision,
    SpeakerEmbeddingBackend,
    SpeakerIdentifier,
    SpeakerPolicy,
    SpeakerProfileStore,
    VoiceSpeakerSession,
    create_backend,
    get_gnome_session_display_name,
    get_gnome_session_user,
)

logger = logging.getLogger("VoiceAssistant.SpeakerRuntime")


class SpeakerIdController:
    """Central controller coordinating Speaker Identification operations."""

    def __init__(
        self,
        backend_name: str = "resemblyzer",
        threshold: float = 0.75,
        mode: str = "disabled",
        on_enrollment_progress: Optional[Callable[[float, float], None]] = None,
        on_enrollment_finished: Optional[Callable[[bool, str, str], None]] = None,
        backend: Optional[SpeakerEmbeddingBackend] = None,
        store: Optional[SpeakerProfileStore] = None,
        is_downloading_fn: Optional[Callable[[], bool]] = None,
        download_progress_fn: Optional[Callable[[], int]] = None,
        on_notify_fn: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self.mode = mode
        self.backend = backend or create_backend(backend_name)
        self.store = store or SpeakerProfileStore(active_backend=self.backend.name)
        self.threshold = threshold
        self.is_downloading_fn = is_downloading_fn
        self.download_progress_fn = download_progress_fn
        self.on_notify_fn = on_notify_fn

        self.identifier = SpeakerIdentifier(
            backend=self.backend,
            store=self.store,
            threshold=self.threshold,
        )
        self.preroll_buffer = PreRollBuffer(max_chunks=4)

        self._on_enrollment_progress = on_enrollment_progress
        self._on_enrollment_finished = on_enrollment_finished

        self.enrollment_recorder = EnrollmentRecorder(
            store=self.store,
            backend=self.backend,
            on_progress=self._handle_enrollment_progress,
            on_finished=self._handle_enrollment_finished,
            sample_rate=16000,
            min_useful_speech_s=4.0,
        )

        import threading
        self.ready_event = threading.Event()
        self.ready = False
        if hasattr(self.backend, "is_loaded") and self.backend.is_loaded():
            self.ready = True
            self.ready_event.set()
        elif self.mode != "disabled":
            self.warm_up()
        else:
            self.ready_event.set()

    def warm_up(self) -> None:
        """Asynchronously load model and run dummy embedding to compile graph."""
        if self.mode == "disabled":
            self.ready_event.set()
            return

        def _do_warm_up():
            try:
                logger.info("Avvio warm_up del modello speaker id...")
                self.backend.load()
                import numpy as np
                dummy_pcm = (np.random.randn(16000) * 0.001).astype(np.float32)
                self.backend.embed(dummy_pcm)
                self.ready = True
                self.ready_event.set()
                logger.info("Warm_up speaker id completato.")
            except Exception as exc:
                logger.error("Errore durante warm_up dello speaker id: %s", exc, exc_info=True)
                self.ready = False
                self.ready_event.set()

        self.ready = False
        self.ready_event.clear()
        import threading
        threading.Thread(target=_do_warm_up, daemon=True, name="SpeakerWarmUpThread").start()

    def set_mode(self, mode: str) -> None:
        """Update active speaker identification mode ('disabled', 'informative', 'gate')."""
        old_mode = self.mode
        self.mode = (mode or "disabled").strip().lower()
        if self.mode not in SpeakerPolicy.MODES:
            self.mode = "disabled"

        logger.info("Speaker ID mode changed from '%s' to '%s'", old_mode, self.mode)
        if self.mode == "disabled":
            self.unload_backend()
        else:
            self.warm_up()

    def set_threshold(self, threshold: float) -> None:
        """Update match threshold dynamically."""
        self.threshold = float(threshold)
        self.identifier.set_threshold(self.threshold)

    def unload_backend(self) -> None:
        """Unload embedding backend model from memory."""
        if self.mode == "gate":
            logger.debug("Scaricamento backend speaker ignorato in modalità gate.")
            return
        try:
            self.backend.unload()
            self.ready = False
            self.ready_event.clear()
        except Exception as exc:
            logger.warning("Error unloading speaker embedding backend: %s", exc)

    def create_session(self, origin: str = "manual", noise_floor_getter: Optional[Callable[[], float]] = None) -> VoiceSpeakerSession:
        """Instantiate and start a new speaker verification session for an utterance."""
        if self.mode != "disabled" and not self.ready:
            self.warm_up()

        session = VoiceSpeakerSession(
            identifier=self.identifier,
            pre_roll_buffer=self.preroll_buffer,
            noise_floor_getter=noise_floor_getter,
            ready_event=self.ready_event,
        )
        session.start(origin=origin)
        return session

    def evaluate_policy(
        self,
        verdict: Optional[Any],
        *,
        is_voice: bool,
        is_stop_command: bool,
        tool_name: Optional[str] = None,
    ) -> SpeakerDecision:
        """Evaluate speaker verification policy given the utterance verdict."""
        return SpeakerPolicy.evaluate(
            mode=self.mode,
            verdict=verdict,
            is_voice=is_voice,
            is_stop_command=is_stop_command,
            tool_name=tool_name,
        )

    def get_profiles(self) -> List[Dict[str, Any]]:
        """List all enrolled speaker profiles."""
        return self.store.list_profiles()

    def get_current_user_profile(self) -> Optional[Dict[str, Any]]:
        """Retrieve the profile bound to the active GNOME session user."""
        return self.store.get_current_user_profile()

    def delete_profile(self, profile_id: str) -> bool:
        """Delete an enrolled speaker profile by ID."""
        return self.store.delete_profile(profile_id)

    @property
    def is_downloading(self) -> bool:
        """Check if the speaker embedding model is currently downloading."""
        if self.is_downloading_fn:
            try:
                if self.is_downloading_fn():
                    return True
            except Exception:
                pass
        if hasattr(self.backend, "is_downloading"):
            try:
                return bool(self.backend.is_downloading())
            except Exception:
                pass
        return False

    @property
    def is_available(self) -> bool:
        """Check if speaker embedding backend is available and ready."""
        try:
            return bool(self.backend.is_available())
        except Exception:
            return False

    def get_download_percent(self) -> int:
        if not self.is_downloading:
            return 0
        if self.download_progress_fn:
            try:
                return int(self.download_progress_fn())
            except Exception:
                pass
        return 0

    def get_status(self) -> Dict[str, Any]:
        """Return comprehensive status dictionary."""
        is_dl = self.is_downloading
        is_avail = self.is_available
        dl_percent = self.get_download_percent()

        if is_dl:
            status = "downloading"
            msg = f"Download del modello vocale in corso ({dl_percent}%)."
        elif is_avail:
            status = "ready"
            msg = "Pronto."
        else:
            status = "unavailable"
            msg = "Componenti di riconoscimento vocale non disponibili o non installati."

        return {
            "available": is_avail,
            "downloading": is_dl,
            "download_percent": dl_percent,
            "status": status,
            "message": msg,
        }

    def start_enrollment(
        self,
        display_name: str = "",
        duration_s: float = 8.0,
        username: Optional[str] = None,
    ) -> bool:
        """Start a new voice enrollment recording session bound to GNOME session user."""
        if self.is_downloading:
            logger.warning("Enrollment inhibited: speaker model is downloading.")
            if self.on_notify_fn:
                self.on_notify_fn(
                    "Assistente Vocale",
                    "Download del modello vocale in corso. La registrazione sarà disponibile al termine.",
                )
            if self._on_enrollment_finished:
                self._on_enrollment_finished(False, "", "downloading")
            return False

        if not self.is_available:
            logger.warning("Enrollment inhibited: speaker model / backend unavailable.")
            if self.on_notify_fn:
                self.on_notify_fn(
                    "Assistente Vocale",
                    "Riconoscimento vocale non disponibile: è necessario installare i componenti richiesti.",
                )
            if self._on_enrollment_finished:
                self._on_enrollment_finished(False, "", "unavailable")
            return False

        disp = (display_name or "").strip() or get_gnome_session_display_name()
        user = (username or "").strip() or get_gnome_session_user()
        return self.enrollment_recorder.start(
            display_name=disp,
            duration_s=duration_s,
            username=user,
        )

    def cancel_enrollment(self) -> bool:
        """Cancel any ongoing enrollment recording."""
        return self.enrollment_recorder.cancel()

    def feed_enrollment_audio(self, chunk: bytes) -> None:
        """Feed raw audio chunk to active enrollment recorder."""
        self.enrollment_recorder.feed(chunk)

    def check_enrollment_timeout(self) -> None:
        """Safety-check the active enrollment recording for a stalled/overlong session."""
        self.enrollment_recorder.check_timeout()

    @property
    def is_enrollment_active(self) -> bool:
        """Return True if enrollment recording or processing is currently active."""
        return self.enrollment_recorder.is_active

    def _handle_enrollment_progress(self, progress: float, level: float) -> None:
        if self._on_enrollment_progress:
            try:
                self._on_enrollment_progress(progress, level)
            except Exception as exc:
                logger.debug("Error in enrollment progress handler: %s", exc)

    def _handle_enrollment_finished(self, success: bool, profile_id: str, message: str) -> None:
        if self._on_enrollment_finished:
            try:
                self._on_enrollment_finished(success, profile_id, message)
            except Exception as exc:
                logger.debug("Error in enrollment finished handler: %s", exc)

    def stop(self) -> None:
        """Shut down background threads."""
        self.identifier.stop()
        self.enrollment_recorder.cancel()
