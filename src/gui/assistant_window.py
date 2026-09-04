"""
AssistantWindow — Standalone GTK4 / Libadwaita Chat Window for Voice Assistant.

Comunicazione col demone interamente via D-Bus (org.local.VoiceAssistant).
Segnali ricevuti: StateChanged, TranscriptReceived, ResponseTokenStreamed.
Metodi chiamati: ProcessTextInput, TriggerListening, OpenSettings.
"""

import logging
import os
import threading

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gdk, Gio

try:
    Adw.init()
except Exception:
    pass

_log = logging.getLogger("VoiceAssistant.GUI")

try:
    import sys as _sys
    _gui_dir = os.path.dirname(os.path.abspath(__file__))
    _daemon_core = os.path.join(os.path.dirname(_gui_dir), "daemon", "core")
    if _daemon_core not in _sys.path:
        _sys.path.insert(0, _daemon_core)
    from logger import glib_safe as _glib_safe
except Exception:
    def _glib_safe(fn, component=None):  # type: ignore[misc]
        return fn

# Registra il gresource dell'estensione per template UI e icone
_GRESOURCE_PATH = os.path.expanduser(
    "~/.local/share/gnome-shell/extensions/"
    "voice-assistant@scroker.github.io/"
    "org.gnome.shell.extensions.voice-assistant.gresource"
)
if os.path.exists(_GRESOURCE_PATH):
    try:
        _resource = Gio.Resource.load(_GRESOURCE_PATH)
        Gio.resources_register(_resource)
    except Exception as _e:
        _log.warning("Impossibile caricare gresource: %s", _e)

_DBUS_NAME = "org.local.VoiceAssistant"
_DBUS_PATH = "/org/local/VoiceAssistant"
_DBUS_IFACE = "org.local.VoiceAssistant"


class ChatBubble(Gtk.Box):
    """Bolla di chat stile Libadwaita."""

    def __init__(self, text: str, is_user: bool = False):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
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
        current = self.label.get_text()
        self.label.set_text(current + text)


@Gtk.Template(resource_path="/org/gnome/shell/extensions/voice-assistant/ui/assistant_window.ui")
class AssistantWindow(Adw.ApplicationWindow):
    """Finestra interattiva dell'Assistente Vocale (app standalone)."""

    __gtype_name__ = "AssistantWindow"

    chat_box = Gtk.Template.Child()
    scrolled = Gtk.Template.Child()
    entry = Gtk.Template.Child()
    send_btn = Gtk.Template.Child()
    mic_btn = Gtk.Template.Child()
    settings_btn = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.current_assistant_bubble: ChatBubble | None = None
        self._streaming_active = False
        self._proxy: Gio.DBusProxy | None = None
        self._pending_deps: list = []
        self._dep_debounce_id: int | None = None

        # Icone dall'estensione
        display = Gdk.Display.get_default()
        if display:
            try:
                icon_theme = Gtk.IconTheme.get_for_display(display)
                icon_theme.add_resource_path(
                    "/org/gnome/shell/extensions/voice-assistant/icons"
                )
                icon_theme.add_resource_path(
                    "/org/gnome/shell/extensions/voice-assistant/icons/hicolor"
                )
                icons_dir = os.path.expanduser(
                    "~/.local/share/gnome-shell/extensions/"
                    "voice-assistant@scroker.github.io/icons"
                )
                if os.path.exists(icons_dir):
                    icon_theme.add_search_path(icons_dir)
                    icon_theme.add_search_path(os.path.join(icons_dir, "hicolor"))
            except Exception as e:
                _log.warning("Impossibile aggiungere path icone: %s", e)

        self.connect("close-request", self._on_close_request)

        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_ctrl)

        self.entry.connect("activate", self._on_send_text)
        self.send_btn.connect("clicked", self._on_send_text)
        self.mic_btn.connect("clicked", self._on_toggle_mic)
        self.settings_btn.connect("clicked", self._on_open_settings)

        # Connessione D-Bus asincrona per non bloccare la UI al lancio
        Gio.DBusProxy.new_for_bus(
            Gio.BusType.SESSION,
            Gio.DBusProxyFlags.NONE,
            None,
            _DBUS_NAME,
            _DBUS_PATH,
            _DBUS_IFACE,
            None,
            self._on_proxy_ready,
        )

        self.add_assistant_message("Ciao! Come posso aiutarti?")

    # ------------------------------------------------------------------
    # D-Bus
    # ------------------------------------------------------------------

    def _on_proxy_ready(self, source: GLib.Object, result: Gio.AsyncResult) -> None:
        try:
            self._proxy = Gio.DBusProxy.new_for_bus_finish(result)
            self._proxy.connect("g-signal", self._on_dbus_signal)
            threading.Thread(target=self._poll_missing_deps, daemon=True).start()
            _log.debug("D-Bus proxy ready")
        except Exception as e:
            _log.error("Connessione D-Bus fallita: %s", e)

    def _on_dbus_signal(
        self,
        proxy: Gio.DBusProxy,
        sender: str,
        signal_name: str,
        params: GLib.Variant,
    ) -> None:
        """Gestisce i segnali D-Bus emessi dal demone."""
        if signal_name == "TranscriptReceived":
            text, is_final = params.unpack()
            if is_final:
                self._streaming_active = False
                GLib.idle_add(_glib_safe(self.add_user_message, "add_user_message"), text)

        elif signal_name == "ResponseTokenStreamed":
            token, is_complete = params.unpack()
            if is_complete:
                if not self._streaming_active and token:
                    # Risposta fast-path: nessun token precedente, mostra tutto
                    GLib.idle_add(_glib_safe(self.add_assistant_message, "add_assistant_message"), token)
                else:
                    # Fine sessione streaming: chiudi la bolla corrente
                    self._streaming_active = False
                    GLib.idle_add(_glib_safe(self._close_current_bubble, "close_bubble"))
            else:
                self._streaming_active = True
                GLib.idle_add(_glib_safe(self.append_assistant_token, "append_token"), token)

        elif signal_name == "StateChanged":
            state = params.unpack()[0]
            _log.debug("Stato demone: %s", state)
            self._on_state_changed(state)

        elif signal_name == "DependencyRequired":
            package, description, is_critical = params.unpack()
            self._pending_deps.append({
                "package": package,
                "description": description,
                "is_critical": is_critical,
            })
            if self._dep_debounce_id:
                GLib.source_remove(self._dep_debounce_id)
            self._dep_debounce_id = GLib.timeout_add(500, self._flush_pending_deps)

    def _poll_missing_deps(self) -> None:
        """Chiama GetMissingDependencies() al proxy ready per recuperare dep mancanti rilevate prima dell'apertura della GUI."""
        try:
            import json
            result = self._proxy.call_sync(
                "GetMissingDependencies", None, Gio.DBusCallFlags.NONE, 3000, None
            )
            deps = json.loads(result.unpack()[0])
            if deps:
                GLib.idle_add(_glib_safe(self._show_deps_dialog, "show_deps_dialog"), deps)
        except Exception as e:
            _log.error("Impossibile recuperare dipendenze mancanti: %s", e)

    def _flush_pending_deps(self) -> bool:
        deps, self._pending_deps = self._pending_deps, []
        self._dep_debounce_id = None
        if deps:
            self._show_deps_dialog(deps)
        return GLib.SOURCE_REMOVE

    def _show_deps_dialog(self, deps: list) -> None:
        from dependency_installer import show_missing_deps_dialog
        show_missing_deps_dialog(self, deps)

    def _call_daemon(self, method: str, params: GLib.Variant | None = None) -> None:
        """Chiama un metodo D-Bus sul demone in un thread separato."""
        if not self._proxy:
            _log.warning("Proxy non disponibile, metodo '%s' ignorato.", method)
            return

        def _do_call() -> None:
            try:
                self._proxy.call_sync(
                    method,
                    params,
                    Gio.DBusCallFlags.NONE,
                    5000,
                    None,
                )
            except Exception as e:
                _log.error("Errore D-Bus %s: %s", method, e)

        threading.Thread(target=_do_call, daemon=True).start()

    # ------------------------------------------------------------------
    # UI helpers
    # ------------------------------------------------------------------

    def scroll_to_bottom(self) -> None:
        def _scroll() -> bool:
            adj = self.scrolled.get_vadjustment()
            adj.set_value(adj.get_upper() - adj.get_page_size())
            return False
        GLib.idle_add(_scroll)

    def add_user_message(self, text: str) -> None:
        bubble = ChatBubble(text, is_user=True)
        self.chat_box.append(bubble)
        self.current_assistant_bubble = None
        self._streaming_active = False
        self.scroll_to_bottom()

    def add_assistant_message(self, text: str) -> None:
        bubble = ChatBubble(text, is_user=False)
        self.chat_box.append(bubble)
        self.current_assistant_bubble = bubble
        self.scroll_to_bottom()

    def append_assistant_token(self, token: str) -> None:
        if self.current_assistant_bubble is None:
            self.add_assistant_message("")
        self.current_assistant_bubble.append_text(token)
        self.scroll_to_bottom()

    def _close_current_bubble(self) -> None:
        self.current_assistant_bubble = None

    def _on_state_changed(self, state: str) -> None:
        # Aggiorna visivamente il bottone microfono in base allo stato del demone
        is_listening = state == "listening"
        if is_listening:
            self.mic_btn.add_css_class("suggested-action")
        else:
            self.mic_btn.remove_css_class("suggested-action")

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_send_text(self, widget: Gtk.Widget) -> None:
        text = self.entry.get_text().strip()
        if not text:
            return
        self.entry.set_text("")
        self.add_user_message(text)
        self._call_daemon("ProcessTextInput", GLib.Variant("(s)", (text,)))

    def _on_toggle_mic(self, widget: Gtk.Widget) -> None:
        self._call_daemon("TriggerListening")

    def _on_open_settings(self, widget: Gtk.Widget) -> None:
        from settings_window import open_settings_window
        open_settings_window(self)

    def _on_close_request(self, window: Adw.ApplicationWindow) -> bool:
        """Nasconde la finestra invece di distruggerla (riapertura veloce)."""
        self.set_visible(False)
        return True

    def _on_key_pressed(
        self,
        controller: Gtk.EventControllerKey,
        keyval: int,
        keycode: int,
        state: Gdk.ModifierType,
    ) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.set_visible(False)
            return True
        return False
