"""Runtime audio device and PipeWire helper for the Voice Assistant daemon."""

import logging
import subprocess

try:
    import sounddevice as sd
except ImportError:  # pragma: no cover - optional runtime dependency
    sd = None

logger = logging.getLogger("VoiceAssistant.Audio")


class AudioRuntimeController:
    """Centralizes microphone stream creation and PipeWire AEC setup."""

    def __init__(self, owner, queue_ref, audio_callback):
        self.owner = owner
        self.queue_ref = queue_ref
        self.audio_callback = audio_callback
        self._stream = None
        self._aec_initialized = False

    def ensure_pipewire_aec(self):
        if self._aec_initialized:
            return
        self._aec_initialized = True
        try:
            res = subprocess.run(["pactl", "list", "modules", "short"], capture_output=True, text=True)
            if "module-echo-cancel" not in res.stdout:
                logger.info("[VoiceAssistant.Audio] Attivazione automatica PipeWire WebRTC AEC / Noise Suppression...")
                subprocess.run(["pactl", "load-module", "module-echo-cancel", "aec_method=webrtc"], capture_output=True)
                subprocess.run(["pactl", "set-default-source", "echo-cancel-source"], capture_output=True)
        except Exception as e:
            logger.warning(f"Impossibile caricare modulo PipeWire echo-cancel: {e}")

    def get_input_device(self):
        if sd is None:
            return None
        try:
            devices = sd.query_devices()
            for idx, dev in enumerate(devices):
                if "echo-cancel" in dev['name'].lower() and dev['max_input_channels'] > 0:
                    logger.info(f"[VoiceAssistant.Audio] Utilizzo del dispositivo microfono AEC: {dev['name']}")
                    return idx
        except Exception:
            pass
        return None

    def create_stream(self):
        if self._stream is not None:
            return self._stream

        if sd is None:
            logger.warning("[VoiceAssistant.Audio] sounddevice non disponibile: stream audio non inizializzato.")
            return None

        self.ensure_pipewire_aec()
        device_idx = self.get_input_device()
        try:
            self._stream = sd.RawInputStream(
                samplerate=16000,
                blocksize=8000,
                device=device_idx,
                dtype='int16',
                channels=1,
                callback=self.audio_callback,
            )
            self.owner._stream = self._stream
            return self._stream
        except Exception as e:
            logger.warning(f"[VoiceAssistant.Audio] Impossibile aprire dispositivo AEC (sample rate): {e}. Fallback su dispositivo predefinito.")
            self._stream = sd.RawInputStream(
                samplerate=16000,
                blocksize=8000,
                device=None,
                dtype='int16',
                channels=1,
                callback=self.audio_callback,
            )
            self.owner._stream = self._stream
            return self._stream

    def close_stream(self):
        if self._stream is not None:
            if self._stream.active:
                self._stream.stop()
            self._stream.close()
            self._stream = None
            self.owner._stream = None

            while not self.queue_ref.empty():
                try:
                    self.queue_ref.get_nowait()
                except Exception:
                    break
