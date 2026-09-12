"""
Componente per la configurazione degli stadi di dispatch dei comandi (Fast-Path e Medium-Path).
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gio', '2.0')
from gi.repository import Gtk, Gio

from .base import bind_setting

_SWITCH_BINDINGS = {
    "dispatch_fast_path_row": "fast-path-enabled",
    "dispatch_medium_path_row": "medium-path-enabled",
}


class DispatchSettings:
    """Configura la sottopagina Dispatch dei Comandi."""

    def __init__(self, builder: Gtk.Builder, settings: Gio.Settings | None, parent_window: Gtk.Window | None = None):
        self.builder = builder
        self.settings = settings
        self.parent_window = parent_window
        self._setup()

    def _setup(self) -> None:
        for widget_id, key in _SWITCH_BINDINGS.items():
            bind_setting(self.settings, key, self.builder, widget_id, "active")
