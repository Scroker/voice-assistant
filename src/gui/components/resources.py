"""
Gestione centralizzata delle risorse GResource e dei temi di icone della GUI.
"""

import logging
import os

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Gdk, Gio

_log = logging.getLogger("VoiceAssistant.GUI.Resources")

_RESOURCE_LOADED = False

_EXT_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))

_CANDIDATE_GRESOURCE_PATHS = [
    os.path.join(_EXT_ROOT, "org.gnome.shell.extensions.voice-assistant.gresource"),
    os.path.normpath(
        os.path.join(
            _EXT_ROOT,
            "..",
            "build",
            "data",
            "org.gnome.shell.extensions.voice-assistant.gresource",
        )
    ),
    os.path.expanduser(
        "~/.local/share/gnome-shell/extensions/"
        "voice-assistant@mkswap.github.io/"
        "org.gnome.shell.extensions.voice-assistant.gresource"
    ),
    os.path.expanduser(
        "~/.local/share/gnome-shell/extensions/"
        "voice-assistant@scroker.github.io/"
        "org.gnome.shell.extensions.voice-assistant.gresource"
    ),
    "/usr/share/gnome-shell/extensions/voice-assistant@scroker.github.io/org.gnome.shell.extensions.voice-assistant.gresource",
    "/usr/share/gnome-shell/extensions/voice-assistant@mkswap.github.io/org.gnome.shell.extensions.voice-assistant.gresource",
]

_ICON_RESOURCE_BASE = "/org/gnome/shell/extensions/voice-assistant/icons"


def register_resources() -> bool:
    """Registra il file .gresource dell'estensione se presente su disco."""
    global _RESOURCE_LOADED
    if _RESOURCE_LOADED:
        return True

    for candidate in _CANDIDATE_GRESOURCE_PATHS:
        if os.path.exists(candidate):
            try:
                resource = Gio.Resource.load(candidate)
                Gio.resources_register(resource)
                _RESOURCE_LOADED = True
                _log.debug("GResource caricato con successo da %s", candidate)
                return True
            except Exception as e:
                _log.warning("Impossibile caricare gresource (%s): %s", candidate, e)

    return False


def register_icons(display: Gdk.Display | None = None) -> None:
    """Registra i percorsi delle icone nel tema attivo per il display specificato."""
    if display is None:
        display = Gdk.Display.get_default()
    if not display:
        return

    try:
        icon_theme = Gtk.IconTheme.get_for_display(display)
        # Registra percorsi di risorsa gresource
        icon_theme.add_resource_path(_ICON_RESOURCE_BASE)
        icon_theme.add_resource_path(f"{_ICON_RESOURCE_BASE}/hicolor")

        # Registra percorsi di fallback su disco
        candidate_icon_dirs = [
            os.path.join(_EXT_ROOT, "icons"),
            os.path.expanduser("~/.local/share/gnome-shell/extensions/voice-assistant@mkswap.github.io/icons"),
            os.path.expanduser("~/.local/share/gnome-shell/extensions/voice-assistant@scroker.github.io/icons"),
        ]
        for icons_dir in candidate_icon_dirs:
            if os.path.exists(icons_dir):
                icon_theme.add_search_path(icons_dir)
                icon_theme.add_search_path(os.path.join(icons_dir, "hicolor"))
    except Exception as e:
        _log.warning("Impossibile aggiungere percorsi icone: %s", e)
