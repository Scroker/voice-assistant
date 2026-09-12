"""
Helper di base per il binding di GSettings con i widget GTK/Libadwaita.
"""

import logging
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gio', '2.0')
from gi.repository import Gtk, Gio

_log = logging.getLogger("VoiceAssistant.GUI.Settings.Base")


def has_setting_key(settings: Gio.Settings | None, key: str) -> bool:
    """Verifica che la chiave esista nello schema caricato.

    Necessario perché GSettings considera l'accesso a una chiave inesistente un errore
    fatale: aborta l'intero processo (SIGABRT) invece di sollevare un'eccezione. Succede
    ogni volta che lo schema compilato installato è più vecchio del codice.
    """
    if not settings:
        return False
    try:
        schema = getattr(settings.props, "settings_schema", None)
    except Exception:
        schema = getattr(settings, "settings_schema", None)
    if schema is None or not hasattr(schema, "has_key"):
        return True
    try:
        return schema.has_key(key)
    except Exception:
        return False


def bind_setting(
    settings: Gio.Settings | None,
    key: str,
    builder: Gtk.Builder,
    widget_id: str,
    prop: str,
    flags: Gio.SettingsBindFlags = Gio.SettingsBindFlags.DEFAULT,
) -> None:
    """Collega una chiave GSettings a una proprietà di un widget da Gtk.Builder."""
    if not settings:
        return
    widget = builder.get_object(widget_id)
    if not widget:
        return
    if not has_setting_key(settings, key):
        _log.warning(
            "Chiave GSettings '%s' assente dallo schema installato: binding di %s.%s ignorato. "
            "Ricompila gli schemi (meson install) per allineare lo schema al codice.",
            key, widget_id, prop,
        )
        return
    try:
        settings.bind(key, widget, prop, flags)
    except Exception as e:
        _log.warning("Errore bind %s -> %s.%s: %s", key, widget_id, prop, e)


def bind_radio_group(
    settings: Gio.Settings | None,
    key: str,
    builder: Gtk.Builder,
    radio_value_map: dict[str, str],
) -> None:
    """Collega un gruppo di Gtk.CheckButton (radio) a una chiave stringa di GSettings in modo bidirezionale."""
    if not settings:
        return

    updating = False

    def _sync_from_settings(*_args):
        nonlocal updating
        val = settings.get_string(key)
        for wid, value in radio_value_map.items():
            radio = builder.get_object(wid)
            if not radio:
                continue
            should_be_active = (value == val)
            if radio.get_active() != should_be_active:
                updating = True
                try:
                    radio.set_active(should_be_active)
                finally:
                    updating = False

    _sync_from_settings()

    for widget_id, value in radio_value_map.items():
        radio = builder.get_object(widget_id)
        if not radio:
            continue

        def _on_active(r, _pspec, v=value, k=key):
            if updating:
                return
            if r.get_active():
                settings.set_string(k, v)

        radio.connect("notify::active", _on_active)

    try:
        settings.connect(f"changed::{key}", _sync_from_settings)
    except Exception as e:
        _log.warning("Errore connect changed::%s: %s", key, e)
