# Voice Assistant GNOME Extension
# Copyright (C) 2026 Giorgio Dramis
# GPLv3 License

import numpy as np

class AudioFilter:
    """
    Real-time high-performance audio filter using NumPy.
    Implements:
    1. Biquad High-Pass IIR filter (80Hz cutoff) to remove low-frequency hum.
    2. Adaptive Noise Floor & Dynamic Volume Threshold tracking based on active microphone gain.
    3. AGC (Automatic Gain Control) to compensate for low/high system microphone volume.
    """
    def __init__(self, sample_rate: int = 16000, highpass_cutoff: float = 80.0):
        self.sample_rate = sample_rate
        self.highpass_cutoff = highpass_cutoff
        
        # Biquad Highpass Filter Coefficients (Direct Form I)
        w0 = 2 * np.pi * highpass_cutoff / sample_rate
        cos_w0 = np.cos(w0)
        sin_w0 = np.sin(w0)
        alpha = sin_w0 / (2 * np.sqrt(2))  # Q = 0.707 (Butterworth)

        b0 = (1 + cos_w0) / 2
        b1 = -(1 + cos_w0)
        b2 = (1 + cos_w0) / 2
        a0 = 1 + alpha
        a1 = -2 * cos_w0
        a2 = 1 - alpha

        self.b = np.array([b0 / a0, b1 / a0, b2 / a0], dtype=np.float32)
        self.a = np.array([a1 / a0, a2 / a0], dtype=np.float32)

        # Filter state memory
        self._x1 = 0.0
        self._x2 = 0.0
        self._y1 = 0.0
        self._y2 = 0.0

        # Adaptive Noise Floor & Gain state
        self._noise_floor = 150.0
        self.target_speech_rms = 1200.0  # RMS target per parlato chiaro
        self.current_gain = 1.0

    def get_dynamic_threshold(self) -> float:
        """Ritorna la soglia di volume calcolata dinamicamente in base al rumore di fondo del microfono."""
        return max(150.0, self._noise_floor * 2.0)

    def process(self, pcm_bytes: bytes) -> bytes:
        """Process raw 16-bit PCM bytes through high-pass filter, AGC, and adaptive noise gate."""
        if not pcm_bytes:
            return pcm_bytes

        audio_in = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
        if len(audio_in) == 0:
            return pcm_bytes

        # 1. Apply High-Pass Filter (IIR)
        audio_out = np.zeros_like(audio_in)
        b0, b1, b2 = self.b
        a1, a2 = self.a

        x1, x2 = self._x1, self._x2
        y1, y2 = self._y1, self._y2

        for i in range(len(audio_in)):
            x0 = audio_in[i]
            y0 = b0 * x0 + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            audio_out[i] = y0
            x2, x1 = x1, x0
            y2, y1 = y1, y0

        self._x1, self._x2 = x1, x2
        self._y1, self._y2 = y1, y2

        # 2. Adaptive Noise Floor Tracking
        rms = float(np.sqrt(np.mean(audio_out**2))) if len(audio_out) > 0 else 0.0
        if rms < self._noise_floor:
            self._noise_floor = 0.95 * self._noise_floor + 0.05 * rms
        elif rms > self._noise_floor * 3.0:
            # Speech detected, keep noise floor steady
            pass

        # 3. AGC: Normalizzazione automatica del guadagno in base al volume del microfono
        if rms > self._noise_floor * 2.0:
            desired_gain = min(2.0, max(0.8, self.target_speech_rms / max(rms, 100.0)))
            self.current_gain = 0.98 * self.current_gain + 0.02 * desired_gain

        audio_out *= self.current_gain

        # 4. Soft Noise Gate basato sulla soglia dinamica
        gate_threshold = self.get_dynamic_threshold()
        if rms < gate_threshold:
            audio_out *= 0.3  # Attenua rumore di fondo

        # Clip and convert back to int16 PCM bytes
        audio_int16 = np.clip(audio_out, -32768, 32767).astype(np.int16)
        return audio_int16.tobytes()
