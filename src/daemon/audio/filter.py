# Voice Assistant GNOME Extension
# Copyright (C) 2026 Giorgio Dramis
# GPLv3 License

import numpy as np


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


class AudioFilter:
    """
    Real-time high-performance audio filter using NumPy.
    Implements three independently switchable stages:
    1. Biquad High-Pass IIR filter to remove low-frequency hum.
    2. AGC (Automatic Gain Control) to compensate for low/high system microphone volume.
    3. Adaptive Noise Floor tracking with a soft noise gate.

    Every stage can be enabled/disabled and tuned at runtime via `update_config()`,
    which preserves the filter state so settings changes don't click or pop.
    """

    HIGHPASS_CUTOFF_RANGE = (20.0, 500.0)
    AGC_TARGET_RMS_RANGE = (200.0, 8000.0)
    AGC_MAX_GAIN_RANGE = (1.0, 8.0)
    GATE_THRESHOLD_RANGE = (1.0, 10.0)
    GATE_ATTENUATION_RANGE = (0.0, 1.0)

    def __init__(
        self,
        sample_rate: int = 16000,
        highpass_cutoff: float = 80.0,
        highpass_enabled: bool = True,
        agc_enabled: bool = True,
        agc_target_rms: float = 1200.0,
        agc_max_gain: float = 2.0,
        noise_gate_enabled: bool = True,
        noise_gate_threshold: float = 2.0,
        noise_gate_attenuation: float = 0.3,
    ):
        self.sample_rate = sample_rate

        self.highpass_enabled = bool(highpass_enabled)
        self.agc_enabled = bool(agc_enabled)
        self.agc_max_gain = _clamp(agc_max_gain, *self.AGC_MAX_GAIN_RANGE)
        self.noise_gate_enabled = bool(noise_gate_enabled)
        self.noise_gate_threshold = _clamp(noise_gate_threshold, *self.GATE_THRESHOLD_RANGE)
        self.noise_gate_attenuation = _clamp(noise_gate_attenuation, *self.GATE_ATTENUATION_RANGE)

        self.highpass_cutoff = _clamp(highpass_cutoff, *self.HIGHPASS_CUTOFF_RANGE)
        self._compute_biquad_coefficients()

        # Filter state memory
        self._x1 = 0.0
        self._x2 = 0.0
        self._y1 = 0.0
        self._y2 = 0.0

        # Adaptive Noise Floor & Gain state
        self._noise_floor = 150.0
        self.target_speech_rms = _clamp(agc_target_rms, *self.AGC_TARGET_RMS_RANGE)
        self.current_gain = 1.0

    def get_noise_floor(self) -> float:
        """Restituisce il livello stimato attuale del rumore di fondo (RMS int16)."""
        return float(self._noise_floor)

    def _compute_biquad_coefficients(self) -> None:
        """Biquad Highpass Filter Coefficients (Direct Form I, Q = 0.707 Butterworth)."""
        w0 = 2 * np.pi * self.highpass_cutoff / self.sample_rate
        cos_w0 = np.cos(w0)
        sin_w0 = np.sin(w0)
        alpha = sin_w0 / (2 * np.sqrt(2))

        b0 = (1 + cos_w0) / 2
        b1 = -(1 + cos_w0)
        b2 = (1 + cos_w0) / 2
        a0 = 1 + alpha
        a1 = -2 * cos_w0
        a2 = 1 - alpha

        self.b = np.array([b0 / a0, b1 / a0, b2 / a0], dtype=np.float32)
        self.a = np.array([a1 / a0, a2 / a0], dtype=np.float32)

    def update_config(self, config: dict) -> None:
        """Applica a caldo una nuova configurazione dei filtri mantenendo lo stato interno."""
        if "highpass_enabled" in config:
            self.highpass_enabled = bool(config["highpass_enabled"])
        if "agc_enabled" in config:
            self.agc_enabled = bool(config["agc_enabled"])
        if "noise_gate_enabled" in config:
            self.noise_gate_enabled = bool(config["noise_gate_enabled"])
        if "agc_target_rms" in config:
            self.target_speech_rms = _clamp(config["agc_target_rms"], *self.AGC_TARGET_RMS_RANGE)
        if "agc_max_gain" in config:
            self.agc_max_gain = _clamp(config["agc_max_gain"], *self.AGC_MAX_GAIN_RANGE)
            self.current_gain = min(self.current_gain, self.agc_max_gain)
        if "noise_gate_threshold" in config:
            self.noise_gate_threshold = _clamp(config["noise_gate_threshold"], *self.GATE_THRESHOLD_RANGE)
        if "noise_gate_attenuation" in config:
            self.noise_gate_attenuation = _clamp(config["noise_gate_attenuation"], *self.GATE_ATTENUATION_RANGE)

        if "highpass_cutoff" in config:
            new_cutoff = _clamp(config["highpass_cutoff"], *self.HIGHPASS_CUTOFF_RANGE)
            if new_cutoff != self.highpass_cutoff:
                self.highpass_cutoff = new_cutoff
                self._compute_biquad_coefficients()

        if not self.agc_enabled:
            self.current_gain = 1.0

    def get_dynamic_threshold(self) -> float:
        """Ritorna la soglia di volume calcolata dinamicamente in base al rumore di fondo del microfono."""
        return max(150.0, self._noise_floor * self.noise_gate_threshold)

    def process(self, pcm_bytes: bytes) -> bytes:
        """Process raw 16-bit PCM bytes through high-pass filter, AGC, and adaptive noise gate."""
        if not pcm_bytes:
            return pcm_bytes

        if not (self.highpass_enabled or self.agc_enabled or self.noise_gate_enabled):
            return pcm_bytes

        audio_in = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
        if len(audio_in) == 0:
            return pcm_bytes

        # 1. Apply High-Pass Filter (IIR)
        if self.highpass_enabled:
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
        else:
            audio_out = audio_in.copy()

        # 2. Adaptive Noise Floor Tracking
        rms = float(np.sqrt(np.mean(audio_out**2))) if len(audio_out) > 0 else 0.0
        if rms < self._noise_floor:
            self._noise_floor = 0.95 * self._noise_floor + 0.05 * rms
        elif rms > self._noise_floor * 3.0:
            # Speech detected, keep noise floor steady
            pass

        # 3. AGC: Normalizzazione automatica del guadagno in base al volume del microfono
        if self.agc_enabled:
            if rms > self._noise_floor * 2.0:
                desired_gain = min(self.agc_max_gain, max(0.8, self.target_speech_rms / max(rms, 100.0)))
                self.current_gain = 0.98 * self.current_gain + 0.02 * desired_gain

            audio_out *= self.current_gain

        # 4. Soft Noise Gate basato sulla soglia dinamica
        if self.noise_gate_enabled:
            gate_threshold = self.get_dynamic_threshold()
            if rms < gate_threshold:
                audio_out *= self.noise_gate_attenuation

        # Clip and convert back to int16 PCM bytes
        audio_int16 = np.clip(audio_out, -32768, 32767).astype(np.int16)
        return audio_int16.tobytes()
