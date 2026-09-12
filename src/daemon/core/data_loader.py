"""
Loader centralizzato e resiliente per risorse e file dati (JSON, testo, template).
Cerca in GResource, nell'albero di sviluppo e nelle directory XDG/sistema.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("VoiceAssistant.DataLoader")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DATA_DIR = _PROJECT_ROOT / "data"

_CANDIDATE_SEARCH_PATHS = [
    _DATA_DIR,
    Path.home() / ".local" / "share" / "gnome-shell" / "extensions" / "voice-assistant@scroker.github.io" / "data",
    Path.home() / ".local" / "share" / "gnome-shell" / "extensions" / "voice-assistant@scroker.github.io",
    Path.home() / ".local" / "share" / "gnome-shell" / "extensions" / "voice-assistant@mkswap.github.io" / "data",
    Path.home() / ".local" / "share" / "gnome-shell" / "extensions" / "voice-assistant@mkswap.github.io",
    Path("/usr/share/gnome-shell/extensions/voice-assistant@scroker.github.io/data"),
    Path("/usr/share/gnome-shell/extensions/voice-assistant@scroker.github.io"),
    Path("/usr/share/gnome-shell/extensions/voice-assistant@mkswap.github.io/data"),
    Path("/usr/share/gnome-shell/extensions/voice-assistant@mkswap.github.io"),
    Path("/usr/share/voice-assistant/data"),
    Path("/usr/share/voice-assistant"),
]


def resolve_data_path(rel_path: str) -> Optional[Path]:
    """Risolve il percorso assoluto di un file dati nel filesystem."""
    clean_rel = rel_path.lstrip("/")
    for base_path in _CANDIDATE_SEARCH_PATHS:
        candidate = base_path / clean_rel
        if candidate.is_file():
            return candidate
    return None


def load_text_data(rel_path: str, fallback_default: str = "") -> str:
    """
    Carica il contenuto testuale di una risorsa da GResource o filesystem.
    In caso di errore o assenza del file, restituisce fallback_default senza crash.
    """
    clean_rel = rel_path.lstrip("/")

    # 1. Tentativo tramite GResource
    try:
        from gi.repository import Gio
        resource_path = f"/org/gnome/shell/extensions/voice-assistant/{clean_rel}"
        res_bytes = Gio.resources_lookup_data(resource_path, Gio.ResourceLookupFlags.NONE)
        if res_bytes is not None:
            return res_bytes.get_data().decode("utf-8")
    except Exception:
        pass

    # 2. Tentativo tramite filesystem
    file_path = resolve_data_path(clean_rel)
    if file_path is not None:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            logger.warning(f"Errore lettura file dati '{file_path}': {e}")

    logger.debug(f"Risorsa '{rel_path}' non reperibile. Utilizzo fallback predefinito.")
    return fallback_default


def load_json_data(rel_path: str, fallback_default: Any = None) -> Any:
    """
    Carica e deserializza un file JSON da GResource o filesystem.
    In caso di errore di parsing o file mancante, restituisce fallback_default.
    """
    raw_text = load_text_data(rel_path, fallback_default="")
    if not raw_text or not raw_text.strip():
        return fallback_default

    try:
        return json.loads(raw_text)
    except Exception as e:
        logger.warning(f"Errore decodifica JSON per risorsa '{rel_path}': {e}")
        return fallback_default
