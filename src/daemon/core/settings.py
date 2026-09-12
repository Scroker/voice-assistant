"""
Reactive GSettings Observer for Voice Assistant Daemon
"""
import logging
from gi.repository import Gio, GLib

logger = logging.getLogger("VoiceAssistant.Settings")


def get_boolean_setting(settings, key: str, default: bool) -> bool:
    """Legge un booleano da GSettings tollerando uno schema compilato obsoleto.

    GSettings considera fatale l'accesso a una chiave inesistente e aborta il processo
    invece di sollevare un'eccezione: qui la chiave viene verificata prima di leggerla,
    così un daemon aggiornato con schema vecchio ricade sul default anziché crashare.
    """
    if not settings:
        return default
    try:
        schema = getattr(settings.props, "settings_schema", None)
    except Exception:
        schema = getattr(settings, "settings_schema", None)
    if schema is not None and hasattr(schema, "has_key"):
        try:
            if not schema.has_key(key):
                logger.warning(f"Chiave GSettings '{key}' assente dallo schema installato: uso il default {default}.")
                return default
        except Exception:
            return default
    try:
        return settings.get_boolean(key)
    except Exception as e:
        logger.warning(f"Impossibile leggere l'impostazione booleana '{key}': {e}")
        return default


class SettingsObserver:
    """
    Encapsulates reading/writing and change observation for GSettings
    org.gnome.shell.extensions.voice-assistant.
    """
    SCHEMA_ID = 'org.gnome.shell.extensions.voice-assistant'

    def __init__(self, callback_on_change=None):
        self._settings = None
        self._callback = callback_on_change
        try:
            self._settings = Gio.Settings.new(self.SCHEMA_ID)
            self._settings.connect('changed', self._on_settings_changed)
        except Exception as e:
            logger.warning(f"[SettingsObserver] Failed to connect GSettings ({e})")

    def _on_settings_changed(self, settings, key):
        logger.debug(f"[SettingsObserver] Key changed: {key}")
        if self._callback:
            try:
                self._callback(key)
            except Exception as e:
                logger.error(f"[SettingsObserver] Error in settings change callback: {e}")

    def get_string(self, key: str, default: str = "") -> str:
        if self._settings and key in self._settings.keys():
            return self._settings.get_string(key)
        return default

    def get_boolean(self, key: str, default: bool = False) -> bool:
        if self._settings and key in self._settings.keys():
            return self._settings.get_boolean(key)
        return default

    def set_string(self, key: str, value: str) -> bool:
        if self._settings and key in self._settings.keys():
            return self._settings.set_string(key, value)
        return False

    def set_boolean(self, key: str, value: bool) -> bool:
        if self._settings and key in self._settings.keys():
            return self._settings.set_boolean(key, value)
        return False
