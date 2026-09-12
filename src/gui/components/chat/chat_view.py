"""
Componente ChatView per la gestione della lista dei messaggi e dello streaming.
"""

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib

from .chat_bubble import ChatBubble


class ChatView:
    """Gestisce la visualizzazione della conversazione, l'autoscroll e lo streaming dei token."""

    def __init__(self, chat_box: Gtk.Box, scrolled: Gtk.ScrolledWindow | None = None):
        self.chat_box = chat_box
        self.scrolled = scrolled
        self.current_assistant_bubble: ChatBubble | None = None
        self._streaming_active: bool = False

    @property
    def streaming_active(self) -> bool:
        return self._streaming_active

    @streaming_active.setter
    def streaming_active(self, value: bool) -> None:
        self._streaming_active = value

    def scroll_to_bottom(self) -> None:
        """Esegue lo scroll in basso all'interno della ScrolledWindow."""
        if not self.scrolled:
            return

        def _scroll() -> bool:
            adj = self.scrolled.get_vadjustment()
            if adj:
                adj.set_value(adj.get_upper() - adj.get_page_size())
            return False

        GLib.idle_add(_scroll)

    def add_user_message(self, text: str) -> ChatBubble:
        """Aggiunge una nuova bolla messaggio dell'utente."""
        bubble = ChatBubble(text, is_user=True)
        self.chat_box.append(bubble)
        self.current_assistant_bubble = None
        self._streaming_active = False
        self.scroll_to_bottom()
        return bubble

    def add_assistant_message(self, text: str) -> ChatBubble:
        """Aggiunge una nuova bolla messaggio dell'assistente."""
        bubble = ChatBubble(text, is_user=False)
        self.chat_box.append(bubble)
        self.current_assistant_bubble = bubble
        self.scroll_to_bottom()
        return bubble

    def append_assistant_token(self, token: str) -> None:
        """Accoda un token alla bolla dell'assistente corrente (o ne crea una se assente)."""
        if self.current_assistant_bubble is None:
            self.add_assistant_message("")
        self.current_assistant_bubble.append_text(token)
        self.scroll_to_bottom()

    def close_current_bubble(self) -> None:
        """Chiude la bolla dell'assistente corrente al termine dello streaming."""
        self.current_assistant_bubble = None
        self._streaming_active = False

    def clear(self) -> None:
        """Rimuove tutti i messaggi dalla chat."""
        child = self.chat_box.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self.chat_box.remove(child)
            child = next_child
        self.current_assistant_bubble = None
        self._streaming_active = False
