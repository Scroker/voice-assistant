"""Runtime audio device and PipeWire helper for the Voice Assistant daemon."""

from __future__ import annotations

import logging
import subprocess

try:
    import sounddevice as sd
except ImportError:  # pragma: no cover - optional runtime dependency
    sd = None

from core.daemon_protocol import DaemonOwner

logger = logging.getLogger("VoiceAssistant.Audio")

# Chiavi GSettings che controllano la catena di filtri applicata ai chunk PCM.
# `audio-aec-enabled` non è inclusa: agisce sul modulo PipeWire, non su AudioFilter.
AUDIO_FILTER_KEYS = (
    "audio-highpass-enabled",
    "audio-highpass-cutoff",
    "audio-agc-enabled",
    "audio-agc-target-rms",
    "audio-agc-max-gain",
    "audio-noise-gate-enabled",
    "audio-noise-gate-threshold",
    "audio-noise-gate-attenuation",
)

_FILTER_KEY_TO_CONFIG = {
    "audio-highpass-enabled": ("highpass_enabled", "boolean"),
    "audio-highpass-cutoff": ("highpass_cutoff", "double"),
    "audio-agc-enabled": ("agc_enabled", "boolean"),
    "audio-agc-target-rms": ("agc_target_rms", "double"),
    "audio-agc-max-gain": ("agc_max_gain", "double"),
    "audio-noise-gate-enabled": ("noise_gate_enabled", "boolean"),
    "audio-noise-gate-threshold": ("noise_gate_threshold", "double"),
    "audio-noise-gate-attenuation": ("noise_gate_attenuation", "double"),
}


def _schema_of(settings):
    try:
        return getattr(settings.props, "settings_schema", None)
    except Exception:
        return getattr(settings, "settings_schema", None)


def _has_key(schema, key: str) -> bool:
    """Evita get_* su chiavi assenti: con uno schema compilato obsoleto GLib abortirebbe il processo."""
    if schema is None or not hasattr(schema, "has_key"):
        return True
    try:
        return schema.has_key(key)
    except Exception:
        return False


def build_filter_config(settings) -> dict:
    """Costruisce il dizionario di configurazione di AudioFilter a partire da GSettings."""
    config: dict = {}
    if not settings:
        return config

    schema = _schema_of(settings)
    for key, (config_name, kind) in _FILTER_KEY_TO_CONFIG.items():
        if not _has_key(schema, key):
            continue
        try:
            config[config_name] = settings.get_boolean(key) if kind == "boolean" else settings.get_double(key)
        except Exception as e:
            logger.warning(f"[VoiceAssistant.Audio] Impossibile leggere l'impostazione {key}: {e}")
    return config


def apply_filter_settings(audio_filter, settings) -> None:
    """Applica a caldo le impostazioni dei filtri a un'istanza AudioFilter già attiva."""
    if audio_filter is None:
        return
    config = build_filter_config(settings)
    if config:
        audio_filter.update_config(config)
        logger.info(f"[VoiceAssistant.Audio] Configurazione filtri audio aggiornata: {config}")


def is_aec_enabled(settings) -> bool:
    """Indica se l'utente ha abilitato la cancellazione dell'eco PipeWire (default: abilitata)."""
    if not settings:
        return True
    if not _has_key(_schema_of(settings), "audio-aec-enabled"):
        return True
    try:
        return settings.get_boolean("audio-aec-enabled")
    except Exception:
        return True


class AudioRuntimeController:
    """Centralizes microphone stream creation and PipeWire AEC setup."""

    def __init__(self, owner: DaemonOwner, queue_ref, audio_callback):
        self.owner = owner
        self.queue_ref = queue_ref
        self.audio_callback = audio_callback
        self._stream = None
        self._aec_initialized = False

    def ensure_pipewire_aec(self):
        if self._aec_initialized:
            return
        if not is_aec_enabled(getattr(self.owner, "settings", None)):
            logger.info("[VoiceAssistant.Audio] Cancellazione eco disabilitata dalle impostazioni: modulo PipeWire non caricato.")
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
        if not is_aec_enabled(getattr(self.owner, "settings", None)):
            # Con l'AEC disattivato si usa il dispositivo predefinito, non echo-cancel-source.
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
