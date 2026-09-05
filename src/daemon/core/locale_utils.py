"""
Locale & System Language Utilities for Voice Assistant
"""

import os
import locale
from typing import Optional


def get_system_language(default: str = "en") -> str:
    """
    Rileva la lingua di sistema dell'utente e ritorna il codice ISO 639-1 a due lettere
    (es. 'it', 'en', 'fr', 'de', 'es').
    
    Verifica in ordine di priorità:
    1. Variabili d'ambiente POSIX (LC_ALL, LC_MESSAGES, LANG).
    2. GNOME GLib.get_language_names() se disponibile.
    3. Modulo Python standard locale.getlocale().
    4. Valore di fallback 'default' (predefinito 'en').
    """
    # 1. Variabili d'ambiente standard POSIX
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        val = os.environ.get(var)
        if val and val.strip() not in ("", "C", "POSIX"):
            code = _clean_lang_code(val)
            if code:
                return code

    # 2. GNOME GLib language names
    try:
        from gi.repository import GLib
        for name in GLib.get_language_names():
            if name and name not in ("C", "POSIX"):
                code = _clean_lang_code(name)
                if code:
                    return code
    except Exception:
        pass

    # 3. Modulo Python standard locale
    try:
        loc = locale.getlocale()[0]
        if loc and loc not in ("C", "POSIX"):
            code = _clean_lang_code(loc)
            if code:
                return code
    except Exception:
        pass

    return default


def get_system_locale(default: str = "en_US") -> str:
    """
    Rileva il locale completo di sistema (es. 'it_IT', 'en_US', 'fr_FR').
    """
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        val = os.environ.get(var)
        if val and val.strip() not in ("", "C", "POSIX"):
            clean = val.split(".")[0].split("@")[0].strip()
            if "_" in clean:
                return clean

    try:
        from gi.repository import GLib
        for name in GLib.get_language_names():
            if name and name not in ("C", "POSIX") and "_" in name:
                return name.split(".")[0].split("@")[0].strip()
    except Exception:
        pass

    try:
        loc = locale.getlocale()[0]
        if loc and "_" in loc:
            return loc.split(".")[0].split("@")[0].strip()
    except Exception:
        pass

    return default


def _clean_lang_code(val: str) -> Optional[str]:
    """Estrae un codice lingua ISO valido (es. 'it', 'en', 'de') da un locale string."""
    clean = val.split(".")[0].split("@")[0].split("_")[0].split("-")[0].strip().lower()
    if len(clean) in (2, 3) and clean.isalpha():
        return clean
    return None
