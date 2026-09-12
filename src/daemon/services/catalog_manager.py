"""Model Catalog Service for dynamic online discovery and local tracking.

Provides:
- Online-first model and voice catalogs (Vosk, Whisper, Piper TTS)
- Local cache with TTL (Time-To-Live)
- Real-time detection of installed models on disk
- Seamless offline fallback for already installed models
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("VoiceAssistant.CatalogManager")

# Catalogo curato dei modelli GGUF locali. Vive qui, e non nel codice che costruisce la
# lista per l'interfaccia, perché è anche l'unica fonte affidabile del repository
# Hugging Face di origine: il nome file salvato in `llm-model` non lo contiene, e senza
# questa mappa un modello pubblicato fuori dall'org "bartowski" (es. Qwen) genererebbe
# un URL di download errato.
CURATED_GGUF_MODELS: List[Dict[str, Any]] = [
    {
        "id": "bartowski/Llama-3.2-1B-Instruct-GGUF:Llama-3.2-1B-Instruct-Q4_K_M.gguf",
        "provider": "llm",
        "name": "Llama 3.2 1B Instruct (GGUF)",
        "subtitle": "Consigliato • ~800 MB • Leggero e veloce",
        "repo": "bartowski/Llama-3.2-1B-Instruct-GGUF",
        "file": "Llama-3.2-1B-Instruct-Q4_K_M.gguf",
        "size_text": "808 MB",
        "url": "https://huggingface.co/bartowski/Llama-3.2-1B-Instruct-GGUF/resolve/main/Llama-3.2-1B-Instruct-Q4_K_M.gguf",
    },
    {
        "id": "bartowski/Llama-3.2-3B-Instruct-GGUF:Llama-3.2-3B-Instruct-Q4_K_M.gguf",
        "provider": "llm",
        "name": "Llama 3.2 3B Instruct (GGUF)",
        "subtitle": "Bilanciato • ~2.0 GB",
        "repo": "bartowski/Llama-3.2-3B-Instruct-GGUF",
        "file": "Llama-3.2-3B-Instruct-Q4_K_M.gguf",
        "size_text": "2.0 GB",
        "url": "https://huggingface.co/bartowski/Llama-3.2-3B-Instruct-GGUF/resolve/main/Llama-3.2-3B-Instruct-Q4_K_M.gguf",
    },
    {
        "id": "google/gemma-2-2b-it-GGUF:gemma-2-2b-it-Q4_K_M.gguf",
        "provider": "llm",
        "name": "Gemma 2 2B IT (GGUF)",
        "subtitle": "Google • ~1.6 GB",
        "repo": "google/gemma-2-2b-it-GGUF",
        "file": "gemma-2-2b-it-Q4_K_M.gguf",
        "size_text": "1.6 GB",
        "url": "https://huggingface.co/google/gemma-2-2b-it-GGUF/resolve/main/gemma-2-2b-it-Q4_K_M.gguf",
    },
    {
        "id": "Qwen/Qwen2.5-1.5B-Instruct-GGUF:qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "provider": "llm",
        "name": "Qwen 2.5 1.5B Instruct (GGUF)",
        "subtitle": "Alibaba • ~1.0 GB",
        "repo": "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
        "file": "qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "size_text": "1.0 GB",
        "url": "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf",
    },
    {
        "id": "Qwen/Qwen2.5-3B-Instruct-GGUF:qwen2.5-3b-instruct-q4_k_m.gguf",
        "provider": "llm",
        "name": "Qwen 2.5 3B Instruct (GGUF)",
        "subtitle": "Alibaba • ~2.0 GB",
        "repo": "Qwen/Qwen2.5-3B-Instruct-GGUF",
        "file": "qwen2.5-3b-instruct-q4_k_m.gguf",
        "size_text": "2.0 GB",
        "url": "https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf",
    },
    {
        "id": "bartowski/Mistral-7B-Instruct-v0.3-GGUF:Mistral-7B-Instruct-v0.3-Q4_K_M.gguf",
        "provider": "llm",
        "name": "Mistral 7B Instruct v0.3 (GGUF)",
        "subtitle": "Mistral AI • ~4.4 GB",
        "repo": "bartowski/Mistral-7B-Instruct-v0.3-GGUF",
        "file": "Mistral-7B-Instruct-v0.3-Q4_K_M.gguf",
        "size_text": "4.4 GB",
        "url": "https://huggingface.co/bartowski/Mistral-7B-Instruct-v0.3-GGUF/resolve/main/Mistral-7B-Instruct-v0.3-Q4_K_M.gguf",
    },
    {
        "id": "bartowski/DeepSeek-R1-Distill-Qwen-1.5B-GGUF:DeepSeek-R1-Distill-Qwen-1.5B-Q4_K_M.gguf",
        "provider": "llm",
        "name": "DeepSeek R1 Distill 1.5B (GGUF)",
        "subtitle": "Ragionamento • ~1.1 GB",
        "repo": "bartowski/DeepSeek-R1-Distill-Qwen-1.5B-GGUF",
        "file": "DeepSeek-R1-Distill-Qwen-1.5B-Q4_K_M.gguf",
        "size_text": "1.1 GB",
        "url": "https://huggingface.co/bartowski/DeepSeek-R1-Distill-Qwen-1.5B-GGUF/resolve/main/DeepSeek-R1-Distill-Qwen-1.5B-Q4_K_M.gguf",
    },
]


def resolve_gguf_catalog_id(filename: str) -> Optional[str]:
    """Ritorna l'id `repo:file` del catalogo per un nome file GGUF "nudo", se conosciuto."""
    if not filename:
        return None
    target = os.path.basename(filename).lower()
    for entry in CURATED_GGUF_MODELS:
        if entry["file"].lower() == target:
            return entry["id"]
    return None


def get_cache_dir() -> Path:
    try:
        from core.path_utils import get_cache_dir as _pu_get_cache
        return _pu_get_cache("catalog")
    except Exception:
        cache_base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
        path = Path(cache_base) / "voice-assistant" / "catalog"
        try:
            path.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return path


def get_default_models_dir() -> Path:
    try:
        from core.path_utils import get_models_dir as _pu_get_models
        return _pu_get_models()
    except Exception:
        data_base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        return Path(data_base) / "voice-assistant" / "models"


class InstalledModelDetector:
    """Tracks installed STT models, TTS voices, and LLMs using ModelRegistry."""

    @staticmethod
    def get_installed_stt_models(models_dir: Optional[str | Path] = None) -> Set[str]:
        try:
            from core.model_registry import model_registry, ModelRegistry
            if models_dir:
                reg = ModelRegistry(models_dir=models_dir)
                reg.reconcile_with_disk()
                return reg.get_installed_model_ids("vosk") | reg.get_installed_model_ids("whisper")
            return model_registry.get_installed_model_ids("vosk") | model_registry.get_installed_model_ids("whisper")
        except Exception as e:
            logger.debug(f"Error using ModelRegistry in get_installed_stt_models: {e}")
            return set()

    @staticmethod
    def get_installed_tts_voices(models_dir: Optional[str | Path] = None) -> Set[str]:
        try:
            from core.model_registry import model_registry, ModelRegistry
            if models_dir:
                reg = ModelRegistry(models_dir=models_dir)
                reg.reconcile_with_disk()
                return reg.get_installed_model_ids("piper")
            return model_registry.get_installed_model_ids("piper")
        except Exception as e:
            logger.debug(f"Error using ModelRegistry in get_installed_tts_voices: {e}")
            return set()

    @staticmethod
    def get_installed_sherpa_models(models_dir: Optional[str | Path] = None) -> Set[str]:
        try:
            from core.model_registry import model_registry, ModelRegistry
            if models_dir:
                reg = ModelRegistry(models_dir=models_dir)
                reg.reconcile_with_disk()
                return reg.get_installed_model_ids("sherpa-onnx")
            return model_registry.get_installed_model_ids("sherpa-onnx")
        except Exception as e:
            logger.debug(f"Error using ModelRegistry in get_installed_sherpa_models: {e}")
            return set()

    @staticmethod
    def get_installed_llm_models(models_dir: Optional[str | Path] = None) -> Set[str]:
        try:
            from core.model_registry import model_registry, ModelRegistry
            if models_dir:
                reg = ModelRegistry(models_dir=models_dir)
                reg.reconcile_with_disk()
                return reg.get_installed_model_ids("gguf")
            return model_registry.get_installed_model_ids("gguf")
        except Exception as e:
            logger.debug(f"Error using ModelRegistry in get_installed_llm_models: {e}")
            return set()


class ModelCatalogService:
    """Manages dynamic online fetching, caching and local model tracking."""

    CACHE_TTL_SECONDS = 86400  # 24 hours

    def __init__(self, cache_dir: Optional[Path] = None, models_dir: Optional[Path] = None):
        self.cache_dir = cache_dir or get_cache_dir()
        self.models_dir = models_dir or get_default_models_dir()
        self._stt_config_cache: Optional[Dict[str, Any]] = None
        self._tts_config_cache: Optional[Dict[str, Any]] = None

    def _get_stt_config(self) -> Dict[str, Any]:
        if self._stt_config_cache is None:
            from core.data_loader import load_json_data
            self._stt_config_cache = load_json_data("catalog/stt_models.json", fallback_default={}) or {}
        return self._stt_config_cache

    def _get_tts_config(self) -> Dict[str, Any]:
        if self._tts_config_cache is None:
            from core.data_loader import load_json_data
            self._tts_config_cache = load_json_data("catalog/tts_voices.json", fallback_default={}) or {}
        return self._tts_config_cache

    def _read_cache_file(self, filename: str) -> Optional[List[Dict[str, Any]]]:
        cache_path = self.cache_dir / filename
        if not cache_path.exists():
            return None
        try:
            mtime = cache_path.stat().st_mtime
            if (time.time() - mtime) > self.CACHE_TTL_SECONDS:
                return None  # expired
            with cache_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception as e:
            logger.debug(f"Cache read error for {filename}: {e}")
        return None

    def _write_cache_file(self, filename: str, data: List[Dict[str, Any]]) -> None:
        try:
            cache_path = self.cache_dir / filename
            with cache_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.debug(f"Cache write error for {filename}: {e}")

    # ==================== VOSK ====================

    def get_vosk_models(
        self,
        user_lang: Optional[str] = None,
        force_refresh: bool = False,
        models_dir: Optional[str | Path] = None,
    ) -> List[Dict[str, Any]]:
        """Fetches Vosk models list from online index with caching and installed flags."""
        installed = InstalledModelDetector.get_installed_stt_models(models_dir or self.models_dir)
        cfg = self._get_stt_config()
        endpoint = cfg.get("endpoints", {}).get(
            "vosk", "https://alphacephei.com/vosk/models/model-list.json"
        )
        cached_models = None if force_refresh else self._read_cache_file("vosk_models.json")
        models: List[Dict[str, Any]] = []

        if cached_models:
            models = cached_models
        else:
            try:
                req = urllib.request.Request(endpoint, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=6) as response:
                    raw_data = json.loads(response.read().decode("utf-8"))
                    parsed: List[Dict[str, Any]] = []
                    for item in raw_data:
                        if item.get("obsolete") == "true":
                            continue
                        m_id = item.get("name") or ""
                        m_type = item.get("type", "")

                        # Filter out TTS and SPK models from STT catalog
                        if (m_type in ("tts", "spk") or
                            "-tts-" in m_id or m_id.startswith("vosk-model-tts-") or
                            "-spk-" in m_id or m_id.startswith("vosk-model-spk-")):
                            continue

                        m_lang = item.get("lang", "en")
                        m_lang_text = item.get("lang_text", m_lang.upper())
                        m_size = item.get("size_text", "")
                        m_url = item.get("url", f"https://alphacephei.com/vosk/models/{m_id}.zip")

                        parsed.append({
                            "id": m_id,
                            "name": f"{m_lang_text} - {m_id} ({m_size})" if m_size else f"{m_lang_text} - {m_id}",
                            "lang": m_lang,
                            "lang_text": m_lang_text,
                            "size_text": m_size,
                            "url": m_url,
                            "type": m_type,
                        })
                    if parsed:
                        models = parsed
                        self._write_cache_file("vosk_models.json", parsed)
            except Exception as e:
                logger.warning(f"[VoskCatalog] Online fetch failed ({e}). Falling back to local cache/seed.")
                cached_any = self._read_cache_file("vosk_models.json")
                if cached_any:
                    models = cached_any
                else:
                    models = cfg.get("seed_fallback", {}).get("vosk", [])

        # Enrich with installed flag
        u_lang = (user_lang or "").split("_")[0].split("-")[0].lower()
        enriched = []
        for m in models:
            item = dict(m)
            item["installed"] = (item.get("id") in installed)
            enriched.append(item)

        # Sort: installed first, then user_lang, then others
        def _sort_key(m):
            is_inst = 0 if m.get("installed") else 1
            is_user = 0 if (u_lang and m.get("lang", "").lower() == u_lang) else 1
            return (is_inst, is_user, m.get("lang_text", ""), m.get("name", ""))

        enriched.sort(key=_sort_key)
        return enriched

    # ==================== WHISPER ====================

    def get_whisper_models(
        self,
        force_refresh: bool = False,
        models_dir: Optional[str | Path] = None,
    ) -> List[Dict[str, Any]]:
        """Fetches Whisper models list from HuggingFace API with caching and installed flags."""
        installed = InstalledModelDetector.get_installed_stt_models(models_dir or self.models_dir)
        cfg = self._get_stt_config()
        endpoint = cfg.get("endpoints", {}).get(
            "whisper", "https://huggingface.co/api/models?author=Systran&search=faster-whisper"
        )
        cached_models = None if force_refresh else self._read_cache_file("whisper_models.json")
        models: List[Dict[str, Any]] = []

        if cached_models:
            models = cached_models
        else:
            try:
                req = urllib.request.Request(endpoint, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=6) as response:
                    raw_data = json.loads(response.read().decode("utf-8"))
                    parsed: List[Dict[str, Any]] = []
                    
                    # Size mapping heuristics for known faster-whisper models
                    size_map = {
                        "tiny": "~75MB", "tiny.en": "~75MB",
                        "base": "~140MB", "base.en": "~140MB",
                        "small": "~466MB", "small.en": "~466MB",
                        "medium": "~1.5GB", "medium.en": "~1.5GB",
                        "large-v1": "~3.1GB", "large-v2": "~3.1GB", "large-v3": "~3.1GB",
                        "large-v3-turbo": "~1.6GB",
                        "distil-large-v2": "~1.5GB", "distil-large-v3": "~1.5GB",
                        "distil-medium.en": "~780MB", "distil-small.en": "~330MB",
                    }

                    for item in raw_data:
                        full_id = item.get("id", "")
                        if "faster-whisper" not in full_id and "faster-distil-whisper" not in full_id:
                            continue
                        short_id = full_id.replace("Systran/faster-whisper-", "").replace("Systran/faster-", "")
                        is_en = short_id.endswith(".en")
                        size_text = size_map.get(short_id, "Cloud/HF")
                        is_rec = (short_id == "base")

                        rec_label = " - Consigliato" if is_rec else ""
                        parsed.append({
                            "id": short_id,
                            "full_id": full_id,
                            "name": f"{short_id.capitalize()} ({size_text}{rec_label})",
                            "lang": "en" if is_en else "multilingual",
                            "lang_text": "English" if is_en else "Multilingual",
                            "size_text": size_text,
                            "recommended": is_rec,
                        })

                    if parsed:
                        models = parsed
                        self._write_cache_file("whisper_models.json", parsed)
            except Exception as e:
                logger.warning(f"[WhisperCatalog] Online fetch failed ({e}). Falling back to local cache/seed.")
                cached_any = self._read_cache_file("whisper_models.json")
                if cached_any:
                    models = cached_any
                else:
                    models = cfg.get("seed_fallback", {}).get("whisper", [])

        # Enrich with installed flag
        enriched = []
        for m in models:
            item = dict(m)
            m_id = item.get("id", "")
            item["installed"] = (m_id in installed)
            enriched.append(item)

        # Sort: installed first, recommended, then name
        def _whisper_sort_key(m):
            is_inst = 0 if m.get("installed") else 1
            is_rec = 0 if m.get("recommended") else 1
            return (is_inst, is_rec, m.get("name", ""))

        enriched.sort(key=_whisper_sort_key)
        return enriched

    # ==================== CLOUD STT ====================

    def get_cloud_models(self) -> List[Dict[str, Any]]:
        """Returns available cloud STT models defined in catalog."""
        cfg = self._get_stt_config()
        return cfg.get("cloud_models", [
            {
                "id": "whisper-1",
                "provider": "openai_cloud",
                "name": "OpenAI Whisper Cloud (whisper-1)",
                "subtitle": "OpenAI Cloud • High Accuracy • Fast",
                "lang": "multilingual",
                "lang_text": "Multilingual",
                "size_text": "Cloud API",
            },
            {
                "id": "whisper-large-v3",
                "provider": "groq_cloud",
                "name": "Groq Whisper Cloud (whisper-large-v3)",
                "subtitle": "Groq Cloud • Ultra Fast Whisper",
                "lang": "multilingual",
                "lang_text": "Multilingual",
                "size_text": "Cloud API",
            },
        ])

    # ==================== PIPER TTS ====================

    def get_piper_voices(
        self,
        user_lang: Optional[str] = None,
        force_refresh: bool = False,
        models_dir: Optional[str | Path] = None,
    ) -> List[Dict[str, Any]]:
        """Fetches Piper voices list from HuggingFace index with caching and installed flags."""
        installed = InstalledModelDetector.get_installed_tts_voices(models_dir or self.models_dir)
        cfg = self._get_tts_config()
        endpoint = cfg.get("endpoints", {}).get(
            "piper", "https://huggingface.co/rhasspy/piper-voices/raw/main/voices.json"
        )
        cached_voices = None if force_refresh else self._read_cache_file("piper_voices.json")
        voices: List[Dict[str, Any]] = []

        if cached_voices:
            voices = cached_voices
        else:
            try:
                req = urllib.request.Request(endpoint, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=8) as response:
                    raw_data = json.loads(response.read().decode("utf-8"))
                    parsed: List[Dict[str, Any]] = []
                    
                    for v_key, v_info in raw_data.items():
                        lang_info = v_info.get("language", {})
                        lang_code = lang_info.get("code", "")
                        lang_name = lang_info.get("name_english", lang_code)
                        quality = v_info.get("quality", "medium")
                        speaker = v_info.get("name", "")

                        parsed.append({
                            "id": v_key,
                            "name": f"{speaker.capitalize()} ({lang_code} - {quality.capitalize()})",
                            "lang": lang_code.split("_")[0].lower(),
                            "lang_code": lang_code,
                            "lang_text": lang_name,
                            "speaker": speaker,
                            "quality": quality,
                        })

                    if parsed:
                        voices = parsed
                        self._write_cache_file("piper_voices.json", parsed)
            except Exception as e:
                logger.warning(f"[PiperCatalog] Online fetch failed ({e}). Falling back to local cache/seed.")
                cached_any = self._read_cache_file("piper_voices.json")
                if cached_any:
                    voices = cached_any
                else:
                    voices = cfg.get("seed_fallback", {}).get("piper", [])

        # Enrich with installed flag
        u_lang = (user_lang or "").split("_")[0].split("-")[0].lower()
        enriched = []
        for v in voices:
            item = dict(v)
            v_id = item.get("id", "")
            item["installed"] = (v_id in installed)
            enriched.append(item)

        # Sort: installed first, then user_lang, then name
        def _tts_sort_key(v):
            is_inst = 0 if v.get("installed") else 1
            is_user = 0 if (u_lang and v.get("lang", "").lower() == u_lang) else 1
            return (is_inst, is_user, v.get("lang_text", ""), v.get("name", ""))

        enriched.sort(key=_tts_sort_key)
        return enriched

    # ==================== SHERPA-ONNX KWS ====================

    def get_sherpa_models(
        self,
        user_lang: Optional[str] = None,
        force_refresh: bool = False,
        models_dir: Optional[str | Path] = None,
    ) -> List[Dict[str, Any]]:
        installed = InstalledModelDetector.get_installed_sherpa_models(models_dir or self.models_dir)
        cached_models = None if force_refresh else self._read_cache_file("sherpa_models.json")
        models: List[Dict[str, Any]] = []

        if cached_models:
            models = cached_models
        else:
            try:
                req = urllib.request.Request(
                    "https://api.github.com/repos/k2-fsa/sherpa-onnx/releases/tags/kws-models",
                    headers={"User-Agent": "VoiceAssistant/1.0"},
                )
                with urllib.request.urlopen(req, timeout=5) as response:
                    raw_data = json.loads(response.read().decode("utf-8"))
                    assets = raw_data.get("assets", [])
                    parsed: List[Dict[str, Any]] = []
                    for a in assets:
                        name = a.get("name", "")
                        if not name.endswith(".tar.bz2") or name == "checksum.txt":
                            continue
                        m_id = name[:-8]  # remove .tar.bz2
                        size_bytes = a.get("size", 0)
                        size_mb = size_bytes / (1024 * 1024)
                        size_text = f"{size_mb:.1f} MB" if size_mb >= 1 else f"{size_bytes / 1024:.0f} KB"
                        url = a.get("browser_download_url") or f"https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/{name}"

                        if "zh-en" in m_id:
                            lang = "multilingual"
                            lang_text = "Cinese / Inglese (Bilingue)"
                            friendly_name = "Sherpa Zipformer Bilingual ZH/EN (Consigliato)"
                        elif "wenetspeech" in m_id or "-zh-" in m_id:
                            lang = "zh"
                            lang_text = "Cinese"
                            is_mobile = "mobile" in m_id
                            friendly_name = f"Sherpa Zipformer Wenetspeech{' Mobile' if is_mobile else ''}"
                        elif "gigaspeech" in m_id or "-en-" in m_id:
                            lang = "en"
                            lang_text = "Inglese / Multi-lingua"
                            is_mobile = "mobile" in m_id
                            friendly_name = f"Sherpa Zipformer Gigaspeech{' Mobile' if is_mobile else ''}"
                        else:
                            lang = "multilingual"
                            lang_text = "Multi-lingua"
                            friendly_name = f"Sherpa KWS {m_id}"

                        parsed.append({
                            "id": m_id,
                            "name": friendly_name,
                            "lang": lang,
                            "lang_text": lang_text,
                            "size_text": size_text,
                            "size_bytes": size_bytes,
                            "url": url,
                        })
                    if parsed:
                        models = parsed
                        self._write_cache_file("sherpa_models.json", parsed)
            except Exception as e:
                logger.warning(f"[SherpaCatalog] Online fetch failed ({e}). Falling back to local cache/seed.")
                cached_any = self._read_cache_file("sherpa_models.json")
                if cached_any:
                    models = cached_any
                else:
                    models = [
                        {
                            "id": "sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20",
                            "name": "Sherpa Zipformer Bilingual ZH/EN (Consigliato)",
                            "lang": "multilingual",
                            "lang_text": "Cinese / Inglese (Bilingue)",
                            "size_text": "31.4 MB",
                            "url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20.tar.bz2",
                        },
                        {
                            "id": "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01",
                            "name": "Sherpa Zipformer Gigaspeech",
                            "lang": "en",
                            "lang_text": "Inglese / Multi-lingua",
                            "size_text": "16.8 MB",
                            "url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01.tar.bz2",
                        },
                        {
                            "id": "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01-mobile",
                            "name": "Sherpa Zipformer Gigaspeech Mobile",
                            "lang": "en",
                            "lang_text": "Inglese / Multi-lingua (Mobile)",
                            "size_text": "14.9 MB",
                            "url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01-mobile.tar.bz2",
                        },
                        {
                            "id": "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01",
                            "name": "Sherpa Zipformer Wenetspeech",
                            "lang": "zh",
                            "lang_text": "Cinese",
                            "size_text": "31.1 MB",
                            "url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01.tar.bz2",
                        },
                        {
                            "id": "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01-mobile",
                            "name": "Sherpa Zipformer Wenetspeech Mobile",
                            "lang": "zh",
                            "lang_text": "Cinese (Mobile)",
                            "size_text": "14.6 MB",
                            "url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01-mobile.tar.bz2",
                        },
                    ]

        u_lang = (user_lang or "").split("_")[0].split("-")[0].lower()
        enriched = []
        for m in models:
            item = dict(m)
            item["installed"] = (item.get("id") in installed)
            enriched.append(item)

        def _sherpa_sort_key(m):
            is_inst = 0 if m.get("installed") else 1
            is_user = 0 if (u_lang and (m.get("lang", "").lower() == u_lang or m.get("lang") == "multilingual")) else 1
            return (is_inst, is_user, m.get("lang_text", ""), m.get("name", ""))

        enriched.sort(key=_sherpa_sort_key)
        return enriched

    # ==================== DEFAULTS ====================

    def get_default_model(self, provider: str, lang: Optional[str] = None) -> str:
        """Resolves the default recommended model for a provider and language."""
        p = provider.lower()
        cfg = self._get_stt_config()
        defaults = cfg.get("defaults", {})

        if p == "vosk":
            l_code = (lang or "it").split("_")[0].split("-")[0].lower()
            vosk_def = defaults.get("vosk", {})
            return vosk_def.get(l_code, vosk_def.get("default", "vosk-model-small-it-0.22"))
        elif p == "whisper":
            return defaults.get("whisper", "base")
        elif p in ("openai_cloud", "groq_cloud", "cloud_stt"):
            cloud_defs = defaults.get("cloud", {})
            return cloud_defs.get(p, "whisper-1")
        return ""

    def get_default_voice(self, provider: str = "piper", lang: Optional[str] = None) -> str:
        """Resolves the default recommended voice for a TTS provider and language."""
        p = provider.lower()
        cfg = self._get_tts_config()
        defaults = cfg.get("defaults", {})

        if p == "piper":
            l_code = (lang or "it").split("_")[0].split("-")[0].lower()
            piper_defs = defaults.get("piper", {})
            return piper_defs.get(l_code, piper_defs.get("default", "it_IT-paola-medium"))
        elif p == "openai":
            return defaults.get("openai", "alloy")
        return ""


# Global singleton instance for easy import across daemon and GUI
catalog_service = ModelCatalogService()
