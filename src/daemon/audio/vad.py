"""
Voice Activity Detection & Silence Detector for Voice Assistant Daemon
"""
import time
import numpy as np


class SilenceDetector:
    """
    Tracks speech activity and detects silence timeouts or max listening duration.
    """
    def __init__(self, silence_timeout_sec: float = 2.0, max_duration_sec: float = 12.0, volume_threshold: float = 300.0):
        self.silence_timeout_sec = silence_timeout_sec
        self.max_duration_sec = max_duration_sec
        self.volume_threshold = volume_threshold
        self.noise_floor = 150.0
        self.listening_start_time = None
        self.last_speech_time = None

    def reset(self):
        self.listening_start_time = None
        self.last_speech_time = None

    def process_chunk(self, pcm_data: bytes, partial_text: str = "") -> dict:
        """
        Processes a raw PCM audio chunk (int16 16kHz mono).
        Returns a dict indicating status with dynamic volume thresholding based on mic noise floor.
        """
        now = time.time()
        if self.listening_start_time is None:
            self.listening_start_time = now

        audio_np = np.frombuffer(pcm_data, dtype=np.int16)
        volume = float(np.abs(audio_np.astype(float)).mean()) if len(audio_np) > 0 else 0.0

        # Adattamento continuo della soglia in base al rumore di fondo del microfono
        if volume < self.noise_floor:
            self.noise_floor = 0.95 * self.noise_floor + 0.05 * volume

        dynamic_threshold = max(self.volume_threshold, self.noise_floor * 2.0)
        is_speaking = (volume > dynamic_threshold) or (len(partial_text.strip()) > 0)
        if is_speaking:
            self.last_speech_time = now

        silence_timeout_reached = False
        if self.last_speech_time and (now - self.last_speech_time) > self.silence_timeout_sec:
            silence_timeout_reached = True

        max_duration_reached = False
        if self.listening_start_time and (now - self.listening_start_time) > self.max_duration_sec:
            max_duration_reached = True

        return {
            'is_speaking': is_speaking,
            'silence_timeout_reached': silence_timeout_reached,
            'max_duration_reached': max_duration_reached,
            'volume': volume,
            'dynamic_threshold': dynamic_threshold
        }
