"""
AssistantWindow — Modern GTK4 / Libadwaita Chat & Voice Window for Voice Assistant
Allows live streaming text rendering, voice activation, and text entry input.
Built with Blueprint Compiler (data/ui/assistant_window.blp).
"""

import sys
import os
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gdk, Gio

try:
    Adw.init()
except Exception:
    pass

# Registrazione risorsa gresource per garantire che il template .ui e le icone siano sempre disponibili
res_file = os.path.expanduser("~/.local/share/gnome-shell/extensions/voice-assistant@scroker.github.io/org.gnome.shell.extensions.voice-assistant.gresource")
if os.path.exists(res_file):
    try:
        resource = Gio.Resource.load(res_file)
        Gio.resources_register(resource)
    except Exception:
        pass


class ChatBubble(Gtk.Box):
    """Componente bolla di testo stile Libadwaita per la chat dell'assistente vocale."""
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

    def append_text(self, text: str):
        current = self.label.get_text()
        self.label.set_text(current + text)


@Gtk.Template(resource_path="/org/gnome/shell/extensions/voice-assistant/ui/assistant_window.ui")
class AssistantWindow(Adw.ApplicationWindow):
    """Finestra fluttuante interattiva Libadwaita dell'Assistente Vocale basata su Blueprint."""
    __gtype_name__ = 'AssistantWindow'

    chat_box = Gtk.Template.Child()
    scrolled = Gtk.Template.Child()
    entry = Gtk.Template.Child()
    send_btn = Gtk.Template.Child()
    mic_btn = Gtk.Template.Child()
    settings_btn = Gtk.Template.Child()

    def __init__(self, dbus_proxy=None, **kwargs):
        super().__init__(**kwargs)
        self.dbus_proxy = dbus_proxy
        self.current_assistant_bubble = None

        # Registrazione percorsi icone da gresource per GtkIconTheme
        display = Gdk.Display.get_default()
        if display:
            try:
                icon_theme = Gtk.IconTheme.get_for_display(display)
                icon_theme.add_resource_path("/org/gnome/shell/extensions/voice-assistant/icons")
                icon_theme.add_resource_path("/org/gnome/shell/extensions/voice-assistant/icons/hicolor")
                
                icons_dir = os.path.expanduser("~/.local/share/gnome-shell/extensions/voice-assistant@scroker.github.io/icons")
                if os.path.exists(icons_dir):
                    icon_theme.add_search_path(icons_dir)
                    icon_theme.add_search_path(os.path.join(icons_dir, "hicolor"))
            except Exception as e:
                print(f"[AssistantWindow] Impossibile aggiungere resource path icone: {e}")

        # Gestione chiusura finestra (Nasconde invece di distruggere per riaperture veloci)
        self.connect("close-request", self._on_close_request)

        # Scorciatoia tasto ESC per chiudere/nascondere la finestra
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_controller)

        # Connessione eventi sui controlli definiti nel template Blueprint
        self.entry.connect("activate", self._on_send_text)
        self.send_btn.connect("clicked", self._on_send_text)
        self.mic_btn.connect("clicked", self._on_toggle_mic)
        self.settings_btn.connect("clicked", self._on_open_settings)

        # Messaggio di benvenuto iniziale dell'ASSISTENTE
        self.add_assistant_message("Ciao! Come posso aiutarti?")

    def scroll_to_bottom(self):
        def _scroll():
            adj = self.scrolled.get_vadjustment()
            adj.set_value(adj.get_upper() - adj.get_page_size())
            return False
        GLib.idle_add(_scroll)

    def add_user_message(self, text: str):
        bubble = ChatBubble(text, is_user=True)
        self.chat_box.append(bubble)
        self.current_assistant_bubble = None
        self.scroll_to_bottom()

    def add_assistant_message(self, text: str):
        bubble = ChatBubble(text, is_user=False)
        self.chat_box.append(bubble)
        self.current_assistant_bubble = bubble
        self.scroll_to_bottom()

    def append_assistant_token(self, token: str):
        if self.current_assistant_bubble is None:
            self.add_assistant_message("")
        self.current_assistant_bubble.append_text(token)
        self.scroll_to_bottom()

    def _on_send_text(self, widget):
        text = self.entry.get_text().strip()
        if not text:
            return
        self.entry.set_text("")
        self.add_user_message(text)

        if self.dbus_proxy:
            import threading
            def _send():
                try:
                    self.dbus_proxy.ProcessTextInput(text)
                except Exception as e:
                    print(f"[AssistantWindow] Errore invio D-Bus: {e}")
            threading.Thread(target=_send, daemon=True).start()

    def _on_toggle_mic(self, widget):
        if self.dbus_proxy:
            try:
                self.dbus_proxy.TriggerListening()
            except Exception as e:
                print(f"[AssistantWindow] Errore trigger mic D-Bus: {e}")

    def _on_open_settings(self, widget):
        if self.dbus_proxy and hasattr(self.dbus_proxy, "OpenSettings"):
            try:
                self.dbus_proxy.OpenSettings()
                return
            except Exception as e:
                print(f"[AssistantWindow] Errore D-Bus OpenSettings: {e}")
        
        try:
            import subprocess
            subprocess.Popen(["gnome-extensions", "prefs", "voice-assistant@scroker.github.io"])
        except Exception as e:
            print(f"[AssistantWindow] Errore apertura impostazioni: {e}")

    def _on_close_request(self, window):
        """Nasconde la finestra alla chiusura anziché distruggerla."""
        self.set_visible(False)
        return True

    def _on_key_pressed(self, controller, keyval, keycode, state):
        """Nasconde la finestra quando si preme il tasto ESC."""
        if keyval == Gdk.KEY_Escape:
            self.set_visible(False)
            return True
        return False


def launch_gui_app(dbus_proxy=None):
    app = Adw.Application(application_id="org.local.VoiceAssistant.GUI", flags=Gio.ApplicationFlags.FLAGS_NONE)
    def on_activate(app_instance):
        win = AssistantWindow(application=app_instance, dbus_proxy=dbus_proxy)
        win.present()
    app.connect("activate", on_activate)
    return app
