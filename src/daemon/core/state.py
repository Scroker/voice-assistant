"""
Centralized State Machine for Voice Assistant Daemon
"""
from enum import Enum
import threading


import logging
from core.logger import ErrorCollector

logger = logging.getLogger("VoiceAssistant.State")


class AssistantState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    DOWNLOADING = "downloading"
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"


class StateMachine:
    """
    Thread-safe State Machine managing the assistant lifecycle states
    and invoking state change callbacks.
    """
    def __init__(self, initial_state: str = AssistantState.IDLE.value):
        self._lock = threading.Lock()
        self._state = initial_state
        self._callbacks = []

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def add_callback(self, callback):
        """Add a callback function(new_state) invoked when state changes."""
        with self._lock:
            if callback not in self._callbacks:
                self._callbacks.append(callback)

    def remove_callback(self, callback):
        """Remove a previously registered callback."""
        with self._lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

    def set_state(self, new_state: str) -> bool:
        """
        Transition to a new state if different from current state.
        Returns True if transition occurred, False otherwise.
        """
        callbacks_to_invoke = []
        with self._lock:
            if self._state == new_state:
                return False
            logger.info(f"State transition: {self._state} -> {new_state}")
            self._state = new_state
            ErrorCollector.update_context("state", new_state)
            callbacks_to_invoke = list(self._callbacks)

        # Invoke callbacks outside the lock to prevent deadlocks
        for cb in callbacks_to_invoke:
            try:
                cb(new_state)
            except Exception as e:
                logger.error(f"Error in state callback: {e}")

        return True
