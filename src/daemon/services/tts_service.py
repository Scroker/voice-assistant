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


def load_default_piper_voices() -> dict:
    try:
        from core.data_loader import load_json_data
        cfg = load_json_data("catalog/tts_voices.json", fallback_default={}) or {}
        defs = cfg.get("defaults", {}).get("piper")
        if defs:
            return dict(defs)
    except Exception:
        pass
    return {
        "it": "it_IT-paola-medium",
        "en": "en_US-lessac-medium",
        "de": "de_DE-thorsten-medium",
        "fr": "fr_FR-siwis-medium",
        "es": "es_ES-sharvard-medium",
        "pt": "pt_BR-edresson-low",
        "nl": "nl_NL-mls-medium",
        "ru": "ru_RU-dmitri-medium",
        "zh": "zh_CN-huayan-medium",
        "pl": "pl_PL-darkman-medium",
        "uk": "uk_UA-ukrainian_tts-medium",
    }


class PiperTTSProvider(BaseTTSProvider):
    """
    Piper TTS Provider using local ONNX neural models for fast natural human speech.
    Supports native python piper-tts library and automatic HF model download.
    """
    DEFAULT_VOICES = load_default_piper_voices()
    DEFAULT_VOICE = DEFAULT_VOICES.get("it", "it_IT-paola-medium")
    HF_REPO = "rhasspy/piper-voices"

    @classmethod
    def get_available_voices(cls, user_lang: Optional[str] = None, force_refresh: bool = False) -> list[dict]:
        try:
            from services.catalog_manager import catalog_service
            return catalog_service.get_piper_voices(user_lang=user_lang, force_refresh=force_refresh)
        except Exception as e:
            logger.warning(f"Errore caricamento voci Piper: {e}")
            return []

    @classmethod
    def get_default_voice(cls, lang: Optional[str] = None) -> str:
        """Ritorna la voce neurale predefinita per la lingua specificata o di sistema."""
        if not lang or not lang.strip():
            try:
                from core.locale_utils import get_system_language
                lang = get_system_language()
            except ImportError:
                lang = "it"
        try:
            from services.catalog_manager import catalog_service
            def_v = catalog_service.get_default_voice("piper", lang=lang)
            if def_v:
                return def_v
        except Exception:
            pass
        code = lang.split("_")[0].split("-")[0].lower()
        return cls.DEFAULT_VOICES.get(code, "it_IT-paola-medium" if code == "it" else "en_US-lessac-medium")

    @classmethod
    def get_hf_voice_path(cls, voice_name: str) -> tuple[str, str]:
        """
        Ritorna la coppia di percorsi relativi (onnx, onnx.json) su Hugging Face
        per qualsiasi modello vocale Piper, secondo la struttura standard del repo:
        {lang}/{locale}/{speaker}/{quality}/{voice_name}.onnx[.json]
        """
        parts = voice_name.split("-")
        if len(parts) >= 3:
            locale = parts[0]
            speaker = parts[1]
            quality = "-".join(parts[2:])
            lang = locale.split("_")[0].lower()
            rel_dir = f"{lang}/{locale}/{speaker}/{quality}"
            return f"{rel_dir}/{voice_name}.onnx", f"{rel_dir}/{voice_name}.onnx.json"
        lang = voice_name.split("_")[0].lower() if "_" in voice_name else "en"
        return f"{lang}/{voice_name}/{voice_name}.onnx", f"{lang}/{voice_name}/{voice_name}.onnx.json"

    def __init__(self, models_dir: Optional[str] = None, model_manager: Optional[Any] = None):
        self.models_dir = models_dir or os.path.expanduser("~/.local/share/voice-assistant/models/tts")
        os.makedirs(self.models_dir, exist_ok=True)
        self.model_manager = model_manager
        self._loaded_voice = None
        self._loaded_voice_name = None
        self._lock = threading.Lock()

    def ensure_voice_downloaded(self, voice_name: Optional[str] = None, progress_callback: Optional[Callable[[int], None]] = None) -> tuple:
        """Scarica i file .onnx e .onnx.json del modello vocale neurale se non presenti."""
        if not voice_name:
            voice_name = self.get_default_voice()

        onnx_local = os.path.join(self.models_dir, f"{voice_name}.onnx")
        json_local = os.path.join(self.models_dir, f"{voice_name}.onnx.json")

        if os.path.exists(onnx_local) and os.path.exists(json_local) and os.path.getsize(onnx_local) > 0:
            if progress_callback:
                progress_callback(100)
            return onnx_local, json_local

        def _report(pct: int):
            if not progress_callback:
                return
            try:
                import inspect
                sig = inspect.signature(progress_callback)
                params = [p for p in sig.parameters.values() if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)]
                if len(params) >= 2:
                    progress_callback(voice_name, pct)
                else:
                    progress_callback(pct)
            except Exception:
                try:
                    progress_callback(pct)
                except TypeError:
                    progress_callback(voice_name, pct)

        onnx_rel, json_rel = self.get_hf_voice_path(voice_name)

        logger.info(f"[PiperTTS] Scaricamento del modello vocale neurale '{voice_name}' da HuggingFace...")
        _report(5)

        # Rimuovi file temporanei residui da precedenti download interrotti
        for p in [f"{onnx_local}.tmp", f"{json_local}.tmp"]:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

        try:
            downloaded = False
            # 1. Tentativo primario: Streaming diretto HTTP con report continuo della percentuale
            try:
                import urllib.request
                base_url = f"https://huggingface.co/{self.HF_REPO}/resolve/main"
                onnx_url = f"{base_url}/{onnx_rel}"
                json_url = f"{base_url}/{json_rel}"
                headers = {"User-Agent": "Mozilla/5.0 (Linux; VoiceAssistant)"}

                # Scarica prima il file .json di configurazione (leggero, ~5-10 KB)
                tmp_json = f"{json_local}.tmp"
                req_json = urllib.request.Request(json_url, headers=headers)
                with urllib.request.urlopen(req_json, timeout=30) as resp, open(tmp_json, "wb") as f_out:
                    shutil.copyfileobj(resp, f_out)
                os.replace(tmp_json, json_local)

                _report(10)

                # Scarica il file pesato .onnx a blocchi con monitoraggio percentuale continuo
                tmp_onnx = f"{onnx_local}.tmp"
                req_onnx = urllib.request.Request(onnx_url, headers=headers)
                with urllib.request.urlopen(req_onnx, timeout=60) as resp, open(tmp_onnx, "wb") as f_out:
                    content_len = int(resp.headers.get("Content-Length", 0))
                    downloaded_bytes = 0
                    chunk_size = 128 * 1024
                    last_pct = 10

                    while True:
                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        f_out.write(chunk)
                        downloaded_bytes += len(chunk)
                        if content_len > 0:
                            pct = 10 + int((downloaded_bytes / content_len) * 89)
                        else:
                            pct = min(98, 10 + int(downloaded_bytes / (1024 * 1024)))
                        pct = min(99, max(10, pct))
                        if pct > last_pct:
                            last_pct = pct
                            _report(pct)

                os.replace(tmp_onnx, onnx_local)
                downloaded = True
                _report(100)
            except InterruptedError:
                raise
            except Exception as e_stream:
                logger.warning(f"[PiperTTS] Download HTTP streaming fallito ({e_stream}), tentativo con fallback HuggingFace Hub...")
                for p in [f"{onnx_local}.tmp", f"{json_local}.tmp"]:
                    if os.path.exists(p):
                        try:
                            os.remove(p)
                        except Exception:
                            pass

            # 2. Fallback: Libreria huggingface_hub con adapter di progresso tqdm
            if not downloaded:
                class _HfProgressTqdm:
                    def __init__(self, *args, **kwargs):
                        self.total = kwargs.get('total') or 0
                        self.n = kwargs.get('initial') or 0
                        self.last_pct = 10

                    def update(self, n=1):
                        self.n += n
                        if self.total > 0:
                            pct = min(99, max(10, 10 + int((self.n / self.total) * 89)))
                            if pct > self.last_pct:
                                self.last_pct = pct
                                _report(pct)

                    def close(self):
                        pass

                    def __enter__(self):
                        return self

                    def __exit__(self, *args):
                        pass

                from huggingface_hub import hf_hub_download
                dl_json = hf_hub_download(repo_id=self.HF_REPO, filename=json_rel, local_dir=self.models_dir)
                _report(10)
                dl_onnx = hf_hub_download(repo_id=self.HF_REPO, filename=onnx_rel, local_dir=self.models_dir, tqdm_class=_HfProgressTqdm)
                shutil.copy2(dl_onnx, onnx_local)
                shutil.copy2(dl_json, json_local)
                downloaded = True
                _report(100)

            return onnx_local, json_local
        except InterruptedError:
            logger.info(f"[PiperTTS] Download di '{voice_name}' interrotto dall'utente.")
            for p in [f"{onnx_local}.tmp", f"{json_local}.tmp", onnx_local, json_local]:
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass
            raise
        except Exception as e:
            for p in [f"{onnx_local}.tmp", f"{json_local}.tmp"]:
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass
            def_voice = self.get_default_voice()
            if not progress_callback and voice_name != def_voice:
                logger.warning(f"[PiperTTS] Impossibile scaricare '{voice_name}' ({e}), fallback sulla voce predefinita '{def_voice}'.")
                return self.ensure_voice_downloaded(def_voice, progress_callback=None)
            logger.error(f"[PiperTTS] Errore scaricamento modello vocale Piper '{voice_name}': {e}")
            raise e

    def load_voice(self, voice_name: Optional[str] = None):
        if not voice_name:
            voice_name = self.get_default_voice()
        if self._loaded_voice and self._loaded_voice_name == voice_name:
            return self._loaded_voice

        onnx_path, json_path = self.ensure_voice_downloaded(voice_name)
        try:
            from piper import PiperVoice
            voice = PiperVoice.load(onnx_path, config_path=json_path)
            self._loaded_voice = voice
            self._loaded_voice_name = voice_name
            if self.model_manager:
                self.model_manager.register_instance("tts", self, self.unload_voice)
            return voice
        except ImportError:
            logger.warning("[PiperTTS] Modulo 'piper-tts' non installato in Python.")
            return None

    def unload_voice(self):
        """Release the cached Piper ONNX voice while keeping downloaded assets intact."""
        with self._lock:
            self._loaded_voice = None
            self._loaded_voice_name = None

    def synthesize(self, text: str, voice: Optional[str] = None, speed: float = 1.0) -> Optional[bytes]:
        if not text or not text.strip():
            return None

        voice_name = voice or self.get_default_voice()

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

        if not voice:
            try:
                from core.locale_utils import get_system_language
                voice = get_system_language()
            except ImportError:
                voice = "en"
        voice_name = voice
        if "_" in voice_name or "-" in voice_name:
            voice_name = voice_name.split("_")[0].split("-")[0].lower()

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
            if res.returncode != 0:
                # Fallback con lingua base
                cmd = [espeak_bin, "-s", str(words_per_minute), "-w", tmp_wav_path, text]
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
    OpenAI Cloud TTS Provider (tts-1 / tts-1-hd).
    """
    def __init__(self, api_key: str = "", model: str = "tts-1", endpoint: str = ""):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.model = model or "tts-1"
        self.endpoint = endpoint or "https://api.openai.com/v1/audio/speech"

    @classmethod
    def get_available_voices(cls) -> list[str]:
        try:
            from core.data_loader import load_json_data
            cfg = load_json_data("catalog/tts_voices.json", fallback_default={}) or {}
            return cfg.get("openai_voices", ["alloy", "echo", "fable", "onyx", "nova", "shimmer"])
        except Exception:
            return ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]

    def synthesize(self, text: str, voice: Optional[str] = None, speed: float = 1.0) -> Optional[bytes]:
        if not text or not text.strip():
            return None

        if not self.api_key:
            logger.warning("[OpenAITTS] Nessuna chiave API fornita per OpenAI TTS.")
            return None

        valid_voices = self.get_available_voices()
        if voice and voice.lower() in [v.lower() for v in valid_voices]:
            voice_name = voice.lower()
        else:
            voice_name = "alloy"

        payload = json.dumps({
            "model": self.model or "tts-1",
            "input": text,
            "voice": voice_name,
            "response_format": "wav",
            "speed": max(0.25, min(4.0, speed)),
        }).encode("utf-8")

        target_url = self.endpoint or "https://api.openai.com/v1/audio/speech"
        req = urllib.request.Request(
            target_url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                if resp.status == 200:
                    return resp.read()
        except Exception as e:
            logger.error(f"[OpenAITTS] Errore richiesta OpenAI TTS: {e}")

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
                
                if not voice:
                    try:
                        from core.locale_utils import get_system_language
                        voice = get_system_language()
                    except ImportError:
                        voice = "en"

                lang_code = voice.split("_")[0].split("-")[0].lower() if ("_" in voice or "-" in voice) else voice

                cmd = [spd_bin, "-l", lang_code, "-r", str(int((speed - 1.0) * 100)), "-w", tmp_wav_path, text]
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
    def __init__(self, audio_player: Optional[Any] = None, settings_observer: Optional[Any] = None,
                 model_manager: Optional[Any] = None, models_dir: Optional[str] = None):
        self.audio_player = audio_player
        self.settings_observer = settings_observer
        self.models_dir = models_dir

        api_key = ""
        model = "tts-1"
        endpoint = "https://api.openai.com/v1/audio/speech"

        try:
            from core.cloud_config import get_cloud_config
            cloud_cfg = get_cloud_config().get_provider_config("tts", "openai")
            api_key = cloud_cfg.get("api_key") or get_cloud_config().get_api_key("tts", "openai")
            model = cloud_cfg.get("model") or "tts-1"
            endpoint = cloud_cfg.get("endpoint") or endpoint
        except Exception:
            pass

        if not api_key and self.settings_observer:
            api_key = self.settings_observer.get("llm-api-key", "")

        self.providers: Dict[str, BaseTTSProvider] = {
            "piper": PiperTTSProvider(models_dir=models_dir, model_manager=model_manager),
            "espeak": EspeakTTSProvider(),
            "openai": OpenAITTSProvider(api_key=api_key, model=model, endpoint=endpoint),
            "system": SystemTTSProvider(),
        }

    def synthesize(self, text: str, voice: Optional[str] = None, speed: Optional[float] = None) -> Optional[bytes]:
        """Sintetizza il testo e restituisce i byte WAV generati."""
        if not text or not text.strip():
            return None

        if self.settings_observer and not self.settings_observer.get("tts-enabled", True):
            logger.info("[TTS] Sintesi vocale disabilitata da impostazioni.")
            return None

        sys_lang = None
        try:
            from core.locale_utils import get_system_language
            sys_lang = get_system_language()
        except ImportError:
            sys_lang = "en"

        current_provider_name = "piper"
        if self.settings_observer:
            current_provider_name = (
                self.settings_observer.get("tts-provider", "")
                or self.settings_observer.get("tts-engine", "")
                or "piper"
            )
            cfg_lang = self.settings_observer.get("language", "")
            active_lang = cfg_lang.strip() if cfg_lang and cfg_lang.strip() else sys_lang
            def_voice = PiperTTSProvider.get_default_voice(active_lang)

            if current_provider_name.lower() == "openai":
                try:
                    from core.cloud_config import get_cloud_config
                    c_cfg = get_cloud_config().get_provider_config("tts", "openai")
                    c_key = c_cfg.get("api_key") or get_cloud_config().get_api_key("tts", "openai")
                    c_model = c_cfg.get("model") or "tts-1"
                    c_voice = c_cfg.get("voice") or "alloy"
                    c_endpoint = c_cfg.get("endpoint") or get_cloud_config().get_endpoint("tts", "openai")
                except Exception:
                    c_key, c_model, c_voice, c_endpoint = "", "tts-1", "alloy", ""

                obs_llm_key = (self.settings_observer.get("llm-api-key", "") or "").strip()
                voice = voice or c_voice or "alloy"
                api_key = c_key or obs_llm_key
                model = c_model or "tts-1"
                if "openai" in self.providers and isinstance(self.providers["openai"], OpenAITTSProvider):
                    self.providers["openai"].api_key = api_key
                    self.providers["openai"].model = model
                    if c_endpoint:
                        self.providers["openai"].endpoint = c_endpoint
            else:
                voice = voice or self.settings_observer.get("tts-voice", def_voice)

            if speed is None:
                speed = self.settings_observer.get("tts-speed", 1.0)
        else:
            voice = voice or PiperTTSProvider.get_default_voice(sys_lang)
            if speed is None:
                speed = 1.0

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
                fallback_lang = sys_lang
                if self.settings_observer:
                    cfg_l = self.settings_observer.get("language", "")
                    if cfg_l and cfg_l.strip():
                        fallback_lang = cfg_l.strip()
                audio_bytes = espeak_provider.synthesize(text, voice=fallback_lang, speed=speed)

        return audio_bytes

    def speak(self, text: str, provider_name: str = "piper", voice: Optional[str] = None, speed: Optional[float] = None) -> bool:
        """
        Sintetizza il testo e lo invia all'AudioPlayer per la riproduzione.
        """
        audio_bytes = self.synthesize(text, voice=voice, speed=speed)
        if audio_bytes and self.audio_player:
            logger.info(f"[TTS] Riproduzione audio ({len(audio_bytes)} byte) per: '{text[:30]}...'")
            self.audio_player.play_wav_bytes(audio_bytes)
            return True
        
        return False
