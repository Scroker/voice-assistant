"""
Componente per la pagina About delle impostazioni.
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gio', '2.0')
from gi.repository import Gtk, Gio

_PROJECT_URL = "https://github.com/Scroker/voice-assistant"


class AboutSettings:
    """Configura la pagina About e il pulsante della documentazione."""

    def __init__(self, builder: Gtk.Builder):
        self.builder = builder
        self._setup()

    def _setup(self) -> None:
        doc_btn = self.builder.get_object("doc_btn")
        if doc_btn:
            doc_btn.connect(
                "clicked",
                lambda _: Gio.AppInfo.launch_default_for_uri(_PROJECT_URL, None),
            )
