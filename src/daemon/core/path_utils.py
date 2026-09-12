"""
Utility centralizzata per la risoluzione dei percorsi standard XDG e delle impostazioni operative.
Carica le definizioni da data/config/defaults.json con fallback resiliente.
"""

from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("VoiceAssistant.PathUtils")

_FALLBACK_DEFAULTS: Dict[str, Any] = {
    "paths": {
        "data_dir": "~/.local/share/voice-assistant",
        "models_dir": "~/.local/share/voice-assistant/models",
        "models_stt_dir": "~/.local/share/voice-assistant/models/stt",
        "models_tts_dir": "~/.local/share/voice-assistant/models/tts",
        "models_llm_dir": "~/.local/share/voice-assistant/models/llm",
        "models_wakeword_dir": "~/.local/share/voice-assistant/models/wakeword",
        "logs_dir": "~/.local/share/voice-assistant/logs",
        "cache_dir": "~/.cache/voice-assistant"
    },
    "logging": {
        "max_bytes": 5242880,
        "backup_count": 5,
        "log_filename": "voice-assistant.log",
        "error_reports_dir": "error_reports",
        "bundles_dir": "bundles"
    },
    "audio": {
        "sample_rate": 16000,
        "channels": 1,
        "chunk_size": 1024
    },
    "network": {
        "http_timeout": 10.0,
        "catalog_cache_ttl_hours": 24
    }
}

_CONFIG_CACHE: Optional[Dict[str, Any]] = None


def load_defaults_config() -> Dict[str, Any]:
    """Carica la configurazione dei default da data/config/defaults.json o fallback."""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE

    try:
        from core.data_loader import load_json_data
        data = load_json_data("config/defaults.json", fallback_default=_FALLBACK_DEFAULTS)
        if isinstance(data, dict):
            _CONFIG_CACHE = data
            return _CONFIG_CACHE
    except Exception as e:
        logger.debug(f"Impossibile caricare config/defaults.json: {e}")

    _CONFIG_CACHE = _FALLBACK_DEFAULTS
    return _CONFIG_CACHE


def get_data_dir() -> Path:
    """Restituisce il percorso della cartella dati XDG (~/.local/share/voice-assistant)."""
    xdg_data = os.environ.get("XDG_DATA_HOME")
    if xdg_data and xdg_data.strip():
        base = Path(os.path.expanduser(xdg_data)) / "voice-assistant"
    else:
        cfg = load_defaults_config()
        path_str = cfg.get("paths", {}).get("data_dir", "~/.local/share/voice-assistant")
        base = Path(os.path.expanduser(path_str))
    try:
        base.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return base


def get_models_dir(custom_path: Optional[str | Path] = None) -> Path:
    """Restituisce la directory dei modelli rispettando eventuali override o GSettings."""
    if custom_path and str(custom_path).strip():
        p = Path(os.path.expanduser(str(custom_path).strip()))
        try:
            p.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return p

    base = get_data_dir() / "models"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return base


def get_stt_models_dir(custom_path: Optional[str | Path] = None) -> Path:
    """Restituisce la sottodirectory dei modelli STT."""
    p = get_models_dir(custom_path) / "stt"
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p


def get_tts_models_dir(custom_path: Optional[str | Path] = None) -> Path:
    """Restituisce la sottodirectory delle voci TTS."""
    p = get_models_dir(custom_path) / "tts"
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p


def get_llm_models_dir(custom_path: Optional[str | Path] = None) -> Path:
    """Restituisce la sottodirectory dei modelli LLM (GGUF)."""
    p = get_models_dir(custom_path) / "llm"
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p


def get_wakeword_models_dir(custom_path: Optional[str | Path] = None) -> Path:
    """Restituisce la sottodirectory dei modelli Wake Word (sherpa, openwakeword, ecc.)."""
    p = get_models_dir(custom_path) / "wakeword"
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p


def get_oww_models_dir(custom_path: Optional[str | Path] = None) -> Path:
    """Restituisce la sottodirectory specifica per i modelli OpenWakeWord."""
    p = get_wakeword_models_dir(custom_path) / "openwakeword"
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p



def get_logs_dir() -> Path:
    """Restituisce il percorso per i file di log."""
    p = get_data_dir() / "logs"
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p


def get_cache_dir(subdir: str = "") -> Path:
    """Restituisce la directory di cache XDG (~/.cache/voice-assistant[/subdir])."""
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    if xdg_cache and xdg_cache.strip():
        base = Path(os.path.expanduser(xdg_cache)) / "voice-assistant"
    else:
        cfg = load_defaults_config()
        path_str = cfg.get("paths", {}).get("cache_dir", "~/.cache/voice-assistant")
        base = Path(os.path.expanduser(path_str))

    if subdir and subdir.strip():
        base = base / subdir.strip()
    try:
        base.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return base


def get_logging_config() -> Dict[str, Any]:
    """Restituisce i parametri di rotazione dei log."""
    cfg = load_defaults_config()
    return dict(cfg.get("logging", _FALLBACK_DEFAULTS["logging"]))


def get_audio_config() -> Dict[str, Any]:
    """Restituisce i parametri audio di default."""
    cfg = load_defaults_config()
    return dict(cfg.get("audio", _FALLBACK_DEFAULTS["audio"]))


def get_network_config() -> Dict[str, Any]:
    """Restituisce i timeout e parametri di rete."""
    cfg = load_defaults_config()
    return dict(cfg.get("network", _FALLBACK_DEFAULTS["network"]))
