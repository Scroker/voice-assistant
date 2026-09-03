"""Lifecycle helpers that keep the daemon state and UI notifications out of main.py."""

from __future__ import annotations

import logging
import time

from gi.repository import GLib

from core.daemon_protocol import DaemonOwner

logger = logging.getLogger("VoiceAssistant.Lifecycle")


class DaemonLifecycle:
    """Encapsulates daemon state transitions, notifications and progress callbacks."""

    def __init__(self, owner: DaemonOwner):
        self.owner = owner

    def emit_download_progress(self, provider: str, model_name: str, percent: int):
        def _emit():
            try:
                self.owner.DownloadProgress(provider, model_name, percent)
            except Exception as e:
                logger.error(f"Error emitting DownloadProgress signal: {e}")
            return False

        GLib.idle_add(_emit)

    def show_notification(self, notif):
        if notif:
            notif.show()
        return False

    def set_state(self, state):
        if hasattr(state, 'value'):
            state_str = str(state.value).lower()
        else:
            state_str = str(state).lower().replace("assistantstate.", "")

        if getattr(self.owner, '_state', None) == state_str:
            return

        self.owner._state = state_str

        model_manager = getattr(self.owner, "model_manager", None)
        if model_manager and state_str in ("listening", "speaking", "processing"):
            model_manager.update_active_timestamp()

        def _do_set_state():
            self.owner.StateChanged(state_str)
            logger.info(f"Stato UI cambiato in: {state_str}")

            if state_str == "downloading":
                self.owner._inhibitor.inhibit("Scaricamento modello Voice Assistant in corso")
            else:
                self.owner._inhibitor.uninhibit()

            if state_str == "disabled":
                logger.info("Microfono disattivato (hardware chiuso).")
                self.owner._close_stream()
            elif state_str in ("idle", "listening", "speaking", "processing"):
                if state_str == "listening" and not getattr(self.owner, "provider", None):
                    runtime_manager = getattr(self.owner, "runtime_manager", None)
                    if runtime_manager:
                        self.owner._state = "loading"
                        runtime_manager.ensure_stt_provider(state_str)
                        return False
                self.owner._create_stream()
                if self.owner._stream and not getattr(self.owner._stream, 'active', False):
                    logger.info("Microfono attivato (in ascolto).")
                    if hasattr(self.owner._stream, 'start'):
                        self.owner._stream.start()
                if state_str == "listening":
                    self.owner._listening_start_time = time.time()
                    self.owner._last_speech_time = None
                    self.owner._ignore_audio_until = time.time() + 0.35
                    if hasattr(self.owner, 'provider') and self.owner.provider:
                        self.owner.provider.reset()
                if state_str == "speaking":
                    self.owner._ignore_audio_until = time.time() + 0.5
                    self.owner._start_speaking_watchdog()
                if state_str in ("idle", "speaking"):
                    while not self.owner.q.empty():
                        try:
                            self.owner.q.get_nowait()
                        except Exception:
                            break
                    if getattr(self.owner, 'ww_model', None):
                        self.owner.reset_wakeword_recognizer()
            return False

        GLib.idle_add(_do_set_state)
