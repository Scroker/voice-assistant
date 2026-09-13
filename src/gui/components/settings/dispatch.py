"""
Componente per la configurazione del dispatch dei comandi (Fast-Path).
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gio', '2.0')
from gi.repository import Gtk, Gio, GObject

from .base import bind_setting

_SWITCH_BINDINGS = {
    "dispatch_fast_path_row": "fast-path-enabled",
}


class DispatchSettings:
    """Configura le opzioni di Dispatch dei Comandi (Fast-Path)."""

    def __init__(self, builder: Gtk.Builder, settings: Gio.Settings | None, parent_window: Gtk.Window | None = None):
        self.builder = builder
        self.settings = settings
        self.parent_window = parent_window
        self._setup()

    def _setup(self) -> None:
        for widget_id, key in _SWITCH_BINDINGS.items():
            bind_setting(self.settings, key, self.builder, widget_id, "active")
        bind_setting(
            self.settings, "semantic-router-confidence-threshold",
            self.builder, "dispatch_semantic_threshold_row", "value",
        )
        fast_row = self.builder.get_object("dispatch_fast_path_row")
        thresh_row = self.builder.get_object("dispatch_semantic_threshold_row")
        self.options_row = self.builder.get_object("dispatch_options_row")
        if fast_row and thresh_row:
            fast_row.bind_property("active", thresh_row, "sensitive", GObject.BindingFlags.SYNC_CREATE)

