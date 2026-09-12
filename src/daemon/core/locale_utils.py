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


_FALLBACK_SUPPORTED_LANGUAGES = [
    {"code": "it", "name": "Italiano", "english_name": "Italian"},
    {"code": "en", "name": "English", "english_name": "English"},
    {"code": "de", "name": "Deutsch", "english_name": "German"},
    {"code": "fr", "name": "Français", "english_name": "French"},
    {"code": "es", "name": "Español", "english_name": "Spanish"},
    {"code": "pt", "name": "Português", "english_name": "Portuguese"},
    {"code": "nl", "name": "Nederlands", "english_name": "Dutch"},
    {"code": "ru", "name": "Русский", "english_name": "Russian"},
    {"code": "zh", "name": "中文", "english_name": "Chinese"},
    {"code": "ja", "name": "日本語", "english_name": "Japanese"},
    {"code": "ko", "name": "한국어", "english_name": "Korean"},
    {"code": "pl", "name": "Polski", "english_name": "Polish"},
    {"code": "uk", "name": "Українська", "english_name": "Ukrainian"},
    {"code": "tr", "name": "Türkçe", "english_name": "Turkish"},
    {"code": "sv", "name": "Svenska", "english_name": "Swedish"},
]

_SUPPORTED_LANGUAGES_CACHE = None


def get_supported_languages() -> list[dict[str, str]]:
    """Carica l'elenco delle lingue supportate da data/locales/supported_languages.json con fallback."""
    global _SUPPORTED_LANGUAGES_CACHE
    if _SUPPORTED_LANGUAGES_CACHE is not None:
        return _SUPPORTED_LANGUAGES_CACHE

    try:
        from core.data_loader import load_json_data
        data = load_json_data("locales/supported_languages.json", fallback_default=_FALLBACK_SUPPORTED_LANGUAGES)
        if isinstance(data, list) and len(data) > 0:
            _SUPPORTED_LANGUAGES_CACHE = data
            return _SUPPORTED_LANGUAGES_CACHE
    except Exception:
        pass

    _SUPPORTED_LANGUAGES_CACHE = _FALLBACK_SUPPORTED_LANGUAGES
    return _SUPPORTED_LANGUAGES_CACHE


def get_language_label(code: str) -> str:
    """Restituisce l'etichetta formattata per il codice lingua (es. 'Italiano (it)')."""
    langs = get_supported_languages()
    for item in langs:
        if item.get("code") == code:
            return f"{item.get('name', code)} ({code})"
    return f"{code.upper()} ({code})"

