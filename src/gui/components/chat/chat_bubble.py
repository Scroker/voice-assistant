"""
Widget ChatBubble stile Libadwaita per la visualizzazione dei messaggi utente e assistente.
"""

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk


class ChatBubble(Gtk.Box):
    """Bolla di chat stile Libadwaita."""

    def __init__(self, text: str, is_user: bool = False):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.is_user = is_user
        self.set_margin_top(6)
        self.set_margin_bottom(6)
        self.set_margin_start(16)
        self.set_margin_end(16)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.label = Gtk.Label(label=text)
        self.label.set_wrap(True)
        self.label.set_selectable(True)
        self.label.set_xalign(0.0)
        self.label.set_margin_top(12)
        self.label.set_margin_bottom(12)
        self.label.set_margin_start(16)
        self.label.set_margin_end(16)
        card.append(self.label)

        if is_user:
            self.set_halign(Gtk.Align.END)
            card.add_css_class("card")
            card.add_css_class("accent")
            self.set_margin_start(48)
        else:
            self.set_halign(Gtk.Align.START)
            card.add_css_class("card")
            self.set_margin_end(48)

        self.append(card)

    def append_text(self, text: str) -> None:
        """Accoda testo alla bolla (utilizzato durante lo streaming dei token)."""
        current = self.label.get_text()
        self.label.set_text(current + text)
