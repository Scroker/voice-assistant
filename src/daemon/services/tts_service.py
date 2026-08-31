"""
TTS Service Manager supporting Piper TTS, eSpeak-ng, and Audio Player integration.
"""
import os
import shutil
import subprocess
import tempfile
import logging
from typing import Optional, Dict, Any, List

logger = logging.getLogger("VoiceAssistant.TTS")

class BaseTTSProvider:
    """Base class for TTS Providers."""

    def synthesize(self, text: str, voice: Optional[str] = None, speed: float = 1.0) -> Optional[bytes]:
        """
        Sintetizza il testo fornito e restituisce i byte dell'audio WAV.
        """
        raise NotImplementedError()


class PiperTTSProvider(BaseTTSProvider):
    """
    Piper TTS Provider using local ONNX neural models for fast natural speech.
    """
    def __init__(self, models_dir: Optional[str] = None):
        self.models_dir = models_dir or os.path.expanduser("~/.local/share/voice-assistant/models/tts")
        os.makedirs(self.models_dir, exist_ok=True)

    def synthesize(self, text: str, voice: Optional[str] = None, speed: float = 1.0) -> Optional[bytes]:
        if not text or not text.strip():
            return None

        voice_name = voice or "it_IT-paola-medium"
        model_path = os.path.join(self.models_dir, f"{voice_name}.onnx")
        
        piper_bin = shutil.which("piper")
        if not piper_bin:
            logger.warning("[PiperTTS] Eseguibile 'piper' non trovato nel PATH.")
            return None

        if not os.path.exists(model_path):
            logger.warning(f"[PiperTTS] Modello '{model_path}' non trovato.")
            return None

        tmp_wav_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
                tmp_wav_path = tmp_wav.name

            cmd = [
                piper_bin,
                "--model", model_path,
                "--output_file", tmp_wav_path,
                "--length_scale", str(1.0 / max(0.5, speed))
            ]

            process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            process.communicate(input=text)

            if os.path.exists(tmp_wav_path) and os.path.getsize(tmp_wav_path) > 0:
                with open(tmp_wav_path, "rb") as f:
                    audio_bytes = f.read()
                os.remove(tmp_wav_path)
                return audio_bytes

        except Exception as e:
            logger.error(f"[PiperTTS] Errore sintesi vocale: {e}")
            if tmp_wav_path and os.path.exists(tmp_wav_path):
                os.remove(tmp_wav_path)

        return None


class EspeakTTSProvider(BaseTTSProvider):
    """
    eSpeak-ng Provider as lightweight offline fallback.
    """
    def synthesize(self, text: str, voice: Optional[str] = None, speed: float = 1.0) -> Optional[bytes]:
        if not text or not text.strip():
            return None

        espeak_bin = shutil.which("espeak-ng") or shutil.which("espeak")
        if not espeak_bin:
            logger.warning("[EspeakTTS] Eseguibile 'espeak-ng'/'espeak' non trovato nel PATH.")
            return None

        voice_name = voice or "it"
        words_per_minute = int(175 * speed)

        tmp_wav_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
                tmp_wav_path = tmp_wav.name

            cmd = [
                espeak_bin,
                "-v", voice_name,
                "-s", str(words_per_minute),
                "-w", tmp_wav_path,
                text
            ]

            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0 and os.path.exists(tmp_wav_path) and os.path.getsize(tmp_wav_path) > 0:
                with open(tmp_wav_path, "rb") as f:
                    audio_bytes = f.read()
                os.remove(tmp_wav_path)
                return audio_bytes

        except Exception as e:
            logger.error(f"[EspeakTTS] Errore sintesi vocale espeak: {e}")
            if tmp_wav_path and os.path.exists(tmp_wav_path):
                os.remove(tmp_wav_path)

        return None


class TTSServiceManager:
    """
    Manager for TTS synthesis, routing to Piper, eSpeak, or custom providers
    and piping audio to the AudioPlayer.
    """
    def __init__(self, audio_player: Optional[Any] = None, settings_observer: Optional[Any] = None):
        self.audio_player = audio_player
        self.settings_observer = settings_observer
        self.providers: Dict[str, BaseTTSProvider] = {
            "piper": PiperTTSProvider(),
            "espeak": EspeakTTSProvider(),
        }

    def speak(self, text: str, provider_name: str = "piper", voice: Optional[str] = None, speed: float = 1.0) -> bool:
        """
        Sintetizza il testo e lo invia all'AudioPlayer per la riproduzione.
        """
        if not text or not text.strip():
            return False

        if self.settings_observer and not self.settings_observer.get("tts-enabled", True):
            logger.info("[TTS] Sintesi vocale disabilitata da impostazioni.")
            return False

        current_provider_name = provider_name
        if self.settings_observer:
            current_provider_name = self.settings_observer.get("tts-provider", provider_name)
            voice = voice or self.settings_observer.get("tts-voice", "it_IT-paola-medium")
            speed = speed or self.settings_observer.get("tts-speed", 1.0)

        provider = self.providers.get(current_provider_name.lower())
        if not provider:
            logger.warning(f"[TTS] Provider '{current_provider_name}' non trovato. Fallback su 'espeak'.")
            provider = self.providers.get("espeak")

        audio_bytes = None
        if provider:
            audio_bytes = provider.synthesize(text, voice=voice, speed=speed)

        # Fallback su espeak se il provider principale non produce audio
        if not audio_bytes and current_provider_name != "espeak":
            logger.info("[TTS] Tentativo di fallback su espeak-ng...")
            espeak_provider = self.providers.get("espeak")
            if espeak_provider:
                audio_bytes = espeak_provider.synthesize(text, voice="it", speed=speed)

        if audio_bytes and self.audio_player:
            logger.info(f"[TTS] Riproduzione audio ({len(audio_bytes)} byte) per: '{text[:30]}...'")
            self.audio_player.play_wav_bytes(audio_bytes)
            return True
        
        return False
