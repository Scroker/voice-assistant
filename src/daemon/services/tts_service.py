"""
TTS Service Manager supporting Piper TTS, eSpeak-ng, OpenAI Cloud TTS, and System Speech Dispatcher.
"""
import os
import shutil
import subprocess
import tempfile
import logging
import json
import urllib.request
import urllib.error
from typing import Optional, Dict, Any, List

import threading

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
    Piper TTS Provider using local ONNX neural models for fast natural human speech.
    Supports native python piper-tts library and automatic HF model download.
    """
    DEFAULT_VOICE = "it_IT-paola-medium"
    HF_REPO = "rhasspy/piper-voices"
    VOICE_HF_PATHS = {
        "it_IT-paola-medium": ("it/it_IT/paola/medium/it_IT-paola-medium.onnx", "it/it_IT/paola/medium/it_IT-paola-medium.onnx.json"),
        "it_IT-riccardo-x_low": ("it/it_IT/riccardo/x_low/it_IT-riccardo-x_low.onnx", "it/it_IT/riccardo/x_low/it_IT-riccardo-x_low.onnx.json"),
        "it_IT-paola-high": ("it/it_IT/paola/high/it_IT-paola-high.onnx", "it/it_IT/paola/high/it_IT-paola-high.onnx.json"),
        "en_US-lessac-medium": ("en/en_US/lessac/medium/en_US-lessac-medium.onnx", "en/en_US/lessac/medium/en_US-lessac-medium.onnx.json"),
        "en_US-amy-medium": ("en/en_US/amy/medium/en_US-amy-medium.onnx", "en/en_US/amy/medium/en_US-amy-medium.onnx.json"),
        "en_GB-alan-low": ("en/en_GB/alan/low/en_GB-alan-low.onnx", "en/en_GB/alan/low/en_GB-alan-low.onnx.json"),
    }

    def __init__(self, models_dir: Optional[str] = None):
        self.models_dir = models_dir or os.path.expanduser("~/.local/share/voice-assistant/models/tts")
        os.makedirs(self.models_dir, exist_ok=True)
        self._loaded_voice = None
        self._loaded_voice_name = None
        self._lock = threading.Lock()

    def ensure_voice_downloaded(self, voice_name: str = DEFAULT_VOICE) -> tuple:
        """Scarica i file .onnx e .onnx.json del modello vocale neurale se non presenti."""
        onnx_local = os.path.join(self.models_dir, f"{voice_name}.onnx")
        json_local = os.path.join(self.models_dir, f"{voice_name}.onnx.json")

        if os.path.exists(onnx_local) and os.path.exists(json_local) and os.path.getsize(onnx_local) > 0:
            return onnx_local, json_local

        if voice_name not in self.VOICE_HF_PATHS:
            voice_name = self.DEFAULT_VOICE
            onnx_local = os.path.join(self.models_dir, f"{voice_name}.onnx")
            json_local = os.path.join(self.models_dir, f"{voice_name}.onnx.json")
            if os.path.exists(onnx_local) and os.path.exists(json_local) and os.path.getsize(onnx_local) > 0:
                return onnx_local, json_local

        onnx_rel, json_rel = self.VOICE_HF_PATHS[voice_name]

        logger.info(f"[PiperTTS] Scaricamento del modello vocale neurale '{voice_name}' da HuggingFace...")
        try:
            from huggingface_hub import hf_hub_download
            dl_onnx = hf_hub_download(repo_id=self.HF_REPO, filename=onnx_rel, local_dir=self.models_dir)
            dl_json = hf_hub_download(repo_id=self.HF_REPO, filename=json_rel, local_dir=self.models_dir)
            
            shutil.copy2(dl_onnx, onnx_local)
            shutil.copy2(dl_json, json_local)
            return onnx_local, json_local
        except Exception as e:
            logger.error(f"[PiperTTS] Errore scaricamento modello vocale Piper: {e}")
            raise e

    def load_voice(self, voice_name: str = DEFAULT_VOICE):
        if self._loaded_voice and self._loaded_voice_name == voice_name:
            return self._loaded_voice

        onnx_path, json_path = self.ensure_voice_downloaded(voice_name)
        try:
            from piper import PiperVoice
            voice = PiperVoice.load(onnx_path, config_path=json_path)
            self._loaded_voice = voice
            self._loaded_voice_name = voice_name
            return voice
        except ImportError:
            logger.warning("[PiperTTS] Modulo 'piper-tts' non installato in Python.")
            return None

    def synthesize(self, text: str, voice: Optional[str] = None, speed: float = 1.0) -> Optional[bytes]:
        if not text or not text.strip():
            return None

        voice_name = voice or self.DEFAULT_VOICE

        with self._lock:
            # 1. Tentativo con libreria Python nativa 'piper-tts'
            try:
                piper_voice = self.load_voice(voice_name)
                if piper_voice:
                    import wave
                    import io
                    buffer = io.BytesIO()
                    with wave.open(buffer, 'wb') as wav_file:
                        wav_file.setnchannels(1)
                        wav_file.setsampwidth(2)
                        wav_file.setframerate(piper_voice.config.sample_rate)
                        for chunk in piper_voice.synthesize(text):
                            if hasattr(chunk, 'audio_int16_bytes') and chunk.audio_int16_bytes:
                                wav_file.writeframes(chunk.audio_int16_bytes)
                    audio_bytes = buffer.getvalue()
                    if len(audio_bytes) > 44:
                        return audio_bytes
            except Exception as e:
                logger.error(f"[PiperTTS] Errore sintesi nativa Python: {e}")

        # 2. Tentativo con eseguibile binario 'piper' se presente nel PATH
        piper_bin = shutil.which("piper")
        if piper_bin:
            onnx_path = os.path.join(self.models_dir, f"{voice_name}.onnx")
            if os.path.exists(onnx_path):
                tmp_wav_path = None
                try:
                    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
                        tmp_wav_path = tmp_wav.name

                    cmd = [
                        piper_bin,
                        "--model", onnx_path,
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
                    logger.error(f"[PiperTTS] Errore sintesi CLI piper: {e}")
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


class OpenAITTSProvider(BaseTTSProvider):
    """
    OpenAI Cloud Neural TTS Provider using /v1/audio/speech API.
    Supports voices: alloy, echo, fable, onyx, nova, shimmer.
    """
    def __init__(self, api_key: str = ""):
        self.api_key = api_key

    def synthesize(self, text: str, voice: Optional[str] = None, speed: float = 1.0) -> Optional[bytes]:
        if not text or not text.strip():
            return None
        
        api_key = self.api_key or os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            logger.warning("[OpenAITTS] Nessuna chiave API fornita per OpenAI TTS.")
            return None

        voice_name = voice if voice in ("alloy", "echo", "fable", "onyx", "nova", "shimmer") else "alloy"
        endpoint = "https://api.openai.com/v1/audio/speech"

        payload = {
            "model": "tts-1",
            "input": text,
            "voice": voice_name,
            "response_format": "wav",
            "speed": max(0.25, min(4.0, speed))
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }

        try:
            req = urllib.request.Request(endpoint, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                audio_bytes = resp.read()
                if len(audio_bytes) > 44:
                    return audio_bytes
        except Exception as e:
            logger.error(f"[OpenAITTS] Errore sintesi OpenAI TTS: {e}")

        return None


class SystemTTSProvider(BaseTTSProvider):
    """
    System Speech Dispatcher (spd-say) provider fallback.
    """
    def synthesize(self, text: str, voice: Optional[str] = None, speed: float = 1.0) -> Optional[bytes]:
        if not text or not text.strip():
            return None
        
        spd_bin = shutil.which("spd-say")
        if spd_bin:
            tmp_wav_path = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
                    tmp_wav_path = tmp_wav.name
                
                cmd = [spd_bin, "-l", voice or "it", "-r", str(int((speed - 1.0) * 100)), "-w", tmp_wav_path, text]
                res = subprocess.run(cmd, capture_output=True, text=True)
                if res.returncode == 0 and os.path.exists(tmp_wav_path) and os.path.getsize(tmp_wav_path) > 0:
                    with open(tmp_wav_path, "rb") as f:
                        audio_bytes = f.read()
                    os.remove(tmp_wav_path)
                    return audio_bytes
            except Exception as e:
                logger.error(f"[SystemTTS] Errore spd-say: {e}")
                if tmp_wav_path and os.path.exists(tmp_wav_path):
                    os.remove(tmp_wav_path)

        return None


class TTSServiceManager:
    """
    Manager for TTS synthesis, routing to Piper, eSpeak, OpenAI, System, or custom providers
    and piping audio to the AudioPlayer.
    """
    def __init__(self, audio_player: Optional[Any] = None, settings_observer: Optional[Any] = None):
        self.audio_player = audio_player
        self.settings_observer = settings_observer

        api_key = ""
        if self.settings_observer:
            api_key = self.settings_observer.get("llm-api-key", "")

        self.providers: Dict[str, BaseTTSProvider] = {
            "piper": PiperTTSProvider(),
            "espeak": EspeakTTSProvider(),
            "openai": OpenAITTSProvider(api_key=api_key),
            "system": SystemTTSProvider(),
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
            api_key = self.settings_observer.get("llm-api-key", "")
            if "openai" in self.providers and isinstance(self.providers["openai"], OpenAITTSProvider):
                self.providers["openai"].api_key = api_key

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
