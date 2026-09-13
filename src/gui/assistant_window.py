import gettext
_ = gettext.gettext
"""
AssistantWindow — Finestra interattiva GTK4 / Libadwaita per l'Assistente Vocale.

Composta tramite componenti riutilizzabili:
- components.resources: registrazione di GResource e temi icone
- components.daemon_client: comunicazione asincrona D-Bus col demone
- components.chat: ChatBubble e ChatView
"""

import logging
import os
import threading
import time

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gdk, Gio, GObject

try:
    Adw.init()
except Exception:
    pass

_log = logging.getLogger("VoiceAssistant.GUI.AssistantWindow")

try:
    import sys as _sys
    _gui_dir = os.path.dirname(os.path.abspath(__file__))
    _daemon_core = os.path.join(os.path.dirname(_gui_dir), "daemon", "core")
    if _daemon_core not in _sys.path:
        _sys.path.insert(0, _daemon_core)
    if _gui_dir not in _sys.path:
        _sys.path.insert(0, _gui_dir)
    from logger import glib_safe as _glib_safe
except Exception:
    def _glib_safe(fn, component=None):  # type: ignore[misc]
        import functools
        @functools.wraps(fn)
        def _fallback_wrapper(*a, **kw):
            res = fn(*a, **kw)
            return True if res is True else False
        return _fallback_wrapper

try:
    from components.resources import register_resources, register_icons
    from components.daemon_client import DaemonClient
    from components.chat import ChatBubble, ChatView
except ImportError:
    from gui.components.resources import register_resources, register_icons
    from gui.components.daemon_client import DaemonClient
    from gui.components.chat import ChatBubble, ChatView

# Assicura registrazione gresource al caricamento del modulo
register_resources()


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
    info_btn = Gtk.Template.Child()
    user_avatar = Gtk.Template.Child()
    user_title = Gtk.Template.Child()
    chats_list = Gtk.Template.Child()
    addons_sidebar = Gtk.Template.Child()
    new_chat_btn = Gtk.Template.Child()
    split_view = Gtk.Template.Child()
    stack_pages = Gtk.Template.Child()

    # MCP Template Children
    mcp_enable_row = Gtk.Template.Child()
    mcp_registry_url_row = Gtk.Template.Child()
    mcp_server_gnome_row = Gtk.Template.Child()
    mcp_servers_group = Gtk.Template.Child()

    # Skills Template Children
    skills_nav_view = Gtk.Template.Child()
    skills_subpage = Gtk.Template.Child()
    skill_editor_subpage = Gtk.Template.Child()
    skills_list_group = Gtk.Template.Child()
    skills_add_btn = Gtk.Template.Child()
    skill_editor_name_row = Gtk.Template.Child()
    skill_editor_intent_row = Gtk.Template.Child()
    skill_editor_tool_row = Gtk.Template.Child()
    skill_editor_triggers_view = Gtk.Template.Child()
    skill_editor_args_view = Gtk.Template.Child()
    skill_editor_delete_btn = Gtk.Template.Child()
    skill_editor_save_btn = Gtk.Template.Child()
    skill_editor_error_row = Gtk.Template.Child()


    def _init_settings(self):
        self._settings = None
        schema_id = "org.gnome.shell.extensions.voice-assistant"
        try:
            source = Gio.SettingsSchemaSource.get_default()
            if source and source.lookup(schema_id, True):
                self._settings = Gio.Settings.new(schema_id)
            else:
                candidates = [
                    os.path.normpath(os.path.join(_gui_dir, "..", "..", "data", "schemas")),
                    os.path.expanduser("~/.local/share/gnome-shell/extensions/voice-assistant@scroker.github.io/schemas"),
                ]
                for c in candidates:
                    if os.path.isdir(c):
                        src = Gio.SettingsSchemaSource.new_from_directory(c, source, False)
                        if src and src.lookup(schema_id, True):
                            self._settings = Gio.Settings.new_full(src.lookup(schema_id, True), None, None)
                            break
        except Exception as e:
            _log.warning(f"Impossibile inizializzare GSettings in AssistantWindow: {e}")

    def _setup_addons_pages(self) -> None:
        self._init_settings()
        try:
            from components.settings.skills import SkillsSettings
            from components.settings.mcp import MCPSettings
        except ImportError:
            from gui.components.settings.skills import SkillsSettings
            from gui.components.settings.mcp import MCPSettings

        self.skills_settings = SkillsSettings(
            builder=None,
            settings=self._settings,
            parent_window=self,
            nav_view=self.skills_nav_view,
            skills_page=self.skills_subpage,
            editor_page=self.skill_editor_subpage,
            list_group=self.skills_list_group,
            add_btn=self.skills_add_btn,
            name_row=self.skill_editor_name_row,
            intent_row=self.skill_editor_intent_row,
            tool_row=self.skill_editor_tool_row,
            triggers_view=self.skill_editor_triggers_view,
            args_view=self.skill_editor_args_view,
            delete_btn=self.skill_editor_delete_btn,
            save_btn=self.skill_editor_save_btn,
        )
        self.skills_settings.reload()

        self.mcp_settings = MCPSettings(
            builder=None,
            settings=self._settings,
            enable_row=self.mcp_enable_row,
            registry_url_row=self.mcp_registry_url_row,
            server_gnome_row=self.mcp_server_gnome_row,
            servers_group=self.mcp_servers_group,
        )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        register_icons(Gdk.Display.get_default())
        self.set_icon_name("vocal-assistant-icon")

        # Inizializza il profilo utente di sistema e avatar
        self._setup_user_profile()

        # Inizializza il componente ChatView
        self.chat_view = ChatView(chat_box=self.chat_box, scrolled=self.scrolled)

        self._current_context_id = ""
        self._generating_context_id = ""
        if hasattr(self, "new_chat_btn") and self.new_chat_btn:
            self.new_chat_btn.connect("clicked", self._on_new_chat)
        self._setup_addons_pages()

        if hasattr(self, "chats_list") and self.chats_list:
            self.chats_list.connect("row-activated", self._on_chat_selected)
            self.chats_list.connect("row-selected", self._on_chat_selected)
        if hasattr(self, "addons_sidebar") and self.addons_sidebar:
            self.addons_sidebar.set_selected(Gtk.INVALID_LIST_POSITION)
            self.addons_sidebar.connect("notify::selected-item", self._on_addons_selected)


        self._pending_deps: list = []
        self._dep_debounce_id: int | None = None
        self._pending_install_deps: bool = False
        self._pending_auto_install: bool = False
        self._deps_dialog_showing: bool = False
        self._last_sent_text: str | None = None
        self._last_sent_time: float = 0.0

        self.connect("close-request", self._on_close_request)

        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_ctrl)

        self.entry.connect("activate", self._on_send_text)
        self.send_btn.connect("clicked", self._on_send_text)
        self.mic_btn.connect("clicked", self._on_toggle_mic)
        self.settings_btn.connect("clicked", self._on_open_settings)
        if self.info_btn:
            self.info_btn.connect("clicked", self._on_open_about)

        # Inizializza il client D-Bus
        self.daemon_client = DaemonClient(
            on_transcript=self._on_transcript_received,
            on_token=self._on_response_token_streamed,
            on_conversation_transcript=self._on_conversation_transcript,
            on_conversation_token=self._on_conversation_token,
            on_conversation_created=self._on_conversation_created,
            on_state_changed=self._on_state_changed,
            on_dependency_required=self._on_dependency_required_signal,
            on_ready=self._on_daemon_ready,
        )

        self.add_assistant_message("Ciao! Come posso aiutarti?")

    # ------------------------------------------------------------------
    # Proprietà e metodi delegati a ChatView (per compatibilità)
    # ------------------------------------------------------------------

    @property
    def current_assistant_bubble(self) -> ChatBubble | None:
        return self.chat_view.current_assistant_bubble

    @current_assistant_bubble.setter
    def current_assistant_bubble(self, bubble: ChatBubble | None) -> None:
        self.chat_view.current_assistant_bubble = bubble

    @property
    def _streaming_active(self) -> bool:
        return self.chat_view.streaming_active

    @_streaming_active.setter
    def _streaming_active(self, val: bool) -> None:
        self.chat_view.streaming_active = val

    @property
    def _proxy(self) -> Gio.DBusProxy | None:
        return self.daemon_client._proxy

    def scroll_to_bottom(self) -> None:
        self.chat_view.scroll_to_bottom()

    def add_user_message(self, text: str) -> ChatBubble:
        return self.chat_view.add_user_message(text)

    def add_assistant_message(self, text: str) -> ChatBubble:
        return self.chat_view.add_assistant_message(text)

    def append_assistant_token(self, token: str) -> None:
        self.chat_view.append_assistant_token(token)

    def _close_current_bubble(self) -> None:
        self.chat_view.close_current_bubble()

    # ------------------------------------------------------------------
    # D-Bus Handlers & Client Integration
    # ------------------------------------------------------------------

    def _refresh_chats_list(self):
        if not hasattr(self, "chats_list") or not self.chats_list:
            return
            
        if hasattr(self.chats_list, "remove_all"):
            self.chats_list.remove_all()
        else:
            child = self.chats_list.get_first_child()
            while child:
                next_child = child.get_next_sibling()
                self.chats_list.remove(child)
                child = next_child
            
        chats = self.daemon_client.list_conversations_sync()
        if not chats:
            new_id = self.daemon_client.create_conversation_sync()
            if new_id:
                chats = self.daemon_client.list_conversations_sync()

        valid_ids = [c["id"] for c in chats] if chats else []
        if not self._current_context_id or self._current_context_id == "voice" or self._current_context_id not in valid_ids:
            if chats:
                self._current_context_id = chats[0]["id"]
            else:
                self._current_context_id = ""

        for c in (chats or []):
            title = c.get("title", "Nuova Chat")
            row = Adw.ActionRow(title=title)
            row.context_id = c["id"]
            row.set_activatable(True)
            row.set_selectable(True)
            row.set_title_lines(1)
            row.set_tooltip_text(title)
            
            del_btn = Gtk.Button(icon_name="user-trash-symbolic")
            del_btn.add_css_class("flat")
            del_btn.add_css_class("circular")
            del_btn.set_valign(Gtk.Align.CENTER)
            
            def on_delete(btn, ctx_id=c["id"]):
                self._on_delete_chat(ctx_id)
                
            del_btn.connect("clicked", on_delete)
            row.add_suffix(del_btn)
            
            chat_icon = Gtk.Image.new_from_icon_name("chat-bubbles-text-symbolic")
            row.add_prefix(chat_icon)
            
            self.chats_list.append(row)
            if self._current_context_id == c["id"]:
                if hasattr(self, "addons_sidebar") and self.addons_sidebar and self.addons_sidebar.get_selected_item() is not None:
                    pass
                else:
                    self.chats_list.select_row(row)

    def open_conversation(self, context_id: str) -> None:
        """Apre esplicitamente una conversazione specificata per ID."""
        if not context_id or context_id == "voice":
            return
        self._current_context_id = context_id
        if hasattr(self, "addons_sidebar") and self.addons_sidebar:
            self.addons_sidebar.set_selected(Gtk.INVALID_LIST_POSITION)
        if hasattr(self, "chat_headerbar") and self.chat_headerbar:
            self.chat_headerbar.set_visible(True)
        if hasattr(self, "stack_pages") and self.stack_pages:
            self.stack_pages.set_visible_child_name("Chat")
        self._refresh_chats_list()
        self._load_current_chat()

    def _load_current_chat(self):
        self.chat_view.clear()
        if not self._current_context_id or self._current_context_id == "voice":
            self.add_assistant_message("Ciao! Come posso aiutarti?")
            return
        msgs = self.daemon_client.get_conversation_messages_sync(self._current_context_id)
        if not msgs:
            self.add_assistant_message("Ciao! Come posso aiutarti?")
        else:
            for m in msgs:
                if m.get("role") == "user":
                    self.add_user_message(m.get("content", ""))
                elif m.get("role") == "assistant":
                    self.add_assistant_message(m.get("content", ""))

    def _on_addons_selected(self, sidebar, pspec):
        item = sidebar.get_selected_item()
        if item is None:
            return

        # Deseleziona chat
        if hasattr(self, "chats_list") and self.chats_list:
            self.chats_list.unselect_all()

        title = item.get_title()
        if hasattr(self, "chat_headerbar") and self.chat_headerbar:
            self.chat_headerbar.set_visible(False)

        if hasattr(self, "stack_pages") and self.stack_pages:
            if title == "Skills":
                self.stack_pages.set_visible_child_name("Skills")
                if hasattr(self, "skills_settings") and self.skills_settings:
                    self.skills_settings.reload()
            elif title == "MCP":
                self.stack_pages.set_visible_child_name("MCP")

    def _on_chat_selected(self, listbox, row):
        if not row:
            return
        if hasattr(self, "addons_sidebar") and self.addons_sidebar:
            if self.addons_sidebar.get_selected_item() is not None:
                self.addons_sidebar.set_selected(Gtk.INVALID_LIST_POSITION)

        ctx_id = getattr(row, "context_id", "")
        if not ctx_id or ctx_id == "voice":
            return
        is_already_selected = (ctx_id == self._current_context_id)
        self._current_context_id = ctx_id

        if hasattr(self, "chat_headerbar") and self.chat_headerbar:
            self.chat_headerbar.set_visible(True)
        if hasattr(self, "stack_pages") and self.stack_pages:
            self.stack_pages.set_visible_child_name("Chat")

        if not is_already_selected:
            self._load_current_chat()

    def _on_new_chat(self, widget):
        new_id = self.daemon_client.create_conversation_sync()
        if new_id:
            self._current_context_id = new_id
            self._refresh_chats_list()
            self._load_current_chat()

    def _on_delete_chat(self, ctx_id):
        success = self.daemon_client.delete_conversation_sync(ctx_id)
        if success:
            if self._current_context_id == ctx_id:
                self._current_context_id = ""
                self._refresh_chats_list()
                self._load_current_chat()
            else:
                self._refresh_chats_list()

    def _on_daemon_ready(self, client: DaemonClient) -> None:
        _log.debug("DaemonClient pronto")
        
        GLib.idle_add(self._refresh_chats_list)
        GLib.idle_add(self._load_current_chat)

        if self._pending_install_deps:
            auto_start = self._pending_auto_install
            self._pending_install_deps = False
            threading.Thread(
                target=self._poll_missing_deps,
                kwargs={"auto_start": auto_start, "force": True},
                daemon=True,
            ).start()
        else:
            threading.Thread(target=self._poll_missing_deps, daemon=True).start()

    def _on_transcript_received(self, text: str, is_final: bool) -> None:
        # Il segnale globale vocale non modifica le finestre di chat GUI
        pass

    def _on_response_token_streamed(self, token: str, is_complete: bool) -> None:
        # Il segnale globale vocale non modifica le finestre di chat GUI
        pass

    def _on_conversation_transcript(self, context_id: str, text: str, is_final: bool) -> None:
        if context_id != self._current_context_id:
            return
        if is_final:
            if (
                getattr(self, "_last_sent_text", None) == text
                and (time.time() - getattr(self, "_last_sent_time", 0.0)) < 3.0
            ):
                self._last_sent_text = None
                return

            def _idle_add_user(t: str) -> bool:
                self.add_user_message(t)
                return False

            GLib.idle_add(_glib_safe(_idle_add_user, "add_user_message"), text)

    def _on_conversation_token(self, context_id: str, token: str, is_complete: bool) -> None:
        if context_id != self._current_context_id:
            return

        if is_complete:
            if not self._streaming_active and token:
                def _idle_add_assistant(t: str) -> bool:
                    self.add_assistant_message(t)
                    return False

                GLib.idle_add(_glib_safe(_idle_add_assistant, "add_assistant_message"), token)
            else:
                self._streaming_active = False

                def _idle_close() -> bool:
                    self._close_current_bubble()
                    return False

                GLib.idle_add(_glib_safe(_idle_close, "close_bubble"))
        else:
            self._streaming_active = True

            def _idle_append(t: str) -> bool:
                self.append_assistant_token(t)
                return False

            GLib.idle_add(_glib_safe(_idle_append, "append_token"), token)

    def _on_conversation_created(self, context_id: str, reason: str) -> None:
        _log.info(f"Ricevuto segnale ConversationCreated({context_id}, {reason})")
        GLib.idle_add(self._refresh_chats_list)

    def _on_dependency_required_signal(self) -> None:
        if self._dep_debounce_id:
            GLib.source_remove(self._dep_debounce_id)
        self._dep_debounce_id = GLib.timeout_add(500, self._trigger_poll_from_signal)

    def _trigger_poll_from_signal(self) -> bool:
        self._dep_debounce_id = None
        threading.Thread(target=self._poll_missing_deps, daemon=True).start()
        return GLib.SOURCE_REMOVE

    def trigger_dependency_installer(self, auto_start: bool = False) -> None:
        """Richiede esplicitamente l'apertura del dialog/installatore di dipendenze."""
        if self.daemon_client.is_ready:
            threading.Thread(
                target=self._poll_missing_deps,
                kwargs={"auto_start": auto_start, "force": True},
                daemon=True,
            ).start()
        else:
            self._pending_install_deps = True
            self._pending_auto_install = auto_start

    def _poll_missing_deps(self, auto_start: bool = False, force: bool = False) -> None:
        """Recupera le dipendenze mancanti dal demone."""
        if not self.daemon_client.is_ready:
            return
        if self._deps_dialog_showing and not force:
            return
        deps = self.daemon_client.get_missing_dependencies_sync()
        if deps:
            GLib.idle_add(
                _glib_safe(self._show_deps_dialog, "show_deps_dialog"),
                deps,
                auto_start,
            )

    def _show_deps_dialog(self, deps: list, auto_start: bool = False) -> None:
        if self._deps_dialog_showing:
            return
        self._deps_dialog_showing = True
        from dependency_installer import show_missing_deps_dialog

        def _on_done(success: bool):
            self._deps_dialog_showing = False
            if success:
                self._pending_deps = []

        show_missing_deps_dialog(self, deps, on_done=_on_done, auto_start=auto_start)

    def _call_daemon(self, method: str, params: GLib.Variant | None = None) -> None:
        self.daemon_client.call_async(method, params)

    def _on_state_changed(self, state: str) -> None:
        is_listening = state == "listening"
        if is_listening:
            self.mic_btn.add_css_class("suggested-action")
        else:
            self.mic_btn.remove_css_class("suggested-action")

    # ------------------------------------------------------------------
    # Event Handlers
    # ------------------------------------------------------------------

    def _on_send_text(self, widget: Gtk.Widget) -> None:
        text = self.entry.get_text().strip()
        if not text:
            return
        self.entry.set_text("")
        self._last_sent_text = text
        self._last_sent_time = time.time()
        self._generating_context_id = self._current_context_id
        self.add_user_message(text)
        self.daemon_client.send_text_in_context(text, self._current_context_id)

    def _on_toggle_mic(self, widget: Gtk.Widget) -> None:
        if not self._current_context_id or self._current_context_id == "voice":
            new_id = self.daemon_client.create_conversation_sync()
            if new_id:
                self._current_context_id = new_id
                self._refresh_chats_list()

        target_ctx = self._current_context_id or "voice"
        self.daemon_client.toggle_listening_in_context(target_ctx)

    def _setup_user_profile(self) -> None:
        """Rileva dinamicamente il nome e l'avatar dell'utente di sistema."""
        try:
            real_name = GLib.get_real_name()
            user_name = GLib.get_user_name()
            display_name = real_name if (real_name and real_name.strip() and real_name != "Unknown") else user_name
            if not display_name or not display_name.strip():
                display_name = "User"

            if hasattr(self, "user_title") and self.user_title:
                self.user_title.set_title(display_name)

            if hasattr(self, "user_avatar") and self.user_avatar:
                self.user_avatar.set_text(display_name)
                self.user_avatar.set_show_initials(True)

                face_candidates = [
                    os.path.expanduser("~/.face"),
                    os.path.expanduser("~/.face.icon"),
                    f"/var/lib/AccountsService/icons/{user_name}",
                ]
                for candidate in face_candidates:
                    if os.path.isfile(candidate):
                        try:
                            texture = Gdk.Texture.new_from_filename(candidate)
                            self.user_avatar.set_custom_image(texture)
                            break
                        except Exception:
                            pass
        except Exception as e:
            _log.debug(f"Errore configurazione profilo utente: {e}")

    def _on_open_about(self, widget: Gtk.Widget) -> None:
        """Apre la finestra di dialogo About con Adw.AboutDialog / Adw.AboutWindow."""
        try:
            app_name = "Assistente Vocale"
            version = "1.0.0"
            developer = "Giorgio Dramis"
            comments = "AI Locale &amp; Offline per GNOME Shell"
            website = "https://github.com/Scroker/voice-assistant"
            issues = "https://github.com/Scroker/voice-assistant/issues"
            copyright_str = "© 2026 Giorgio Dramis"
            icon_name = "vocal-assistant-icon"

            if hasattr(Adw, "AboutDialog"):
                about = Adw.AboutDialog()
                about.set_application_name(app_name)
                about.set_version(version)
                about.set_developer_name(developer)
                about.set_application_icon(icon_name)
                about.set_comments(comments)
                about.set_website(website)
                about.set_issue_url(issues)
                about.set_copyright(copyright_str)
                about.set_license_type(Gtk.License.GPL_3_0)
                about.present(self)
            elif hasattr(Adw, "AboutWindow"):
                about = Adw.AboutWindow(transient_for=self)
                about.set_application_name(app_name)
                about.set_version(version)
                about.set_developer_name(developer)
                about.set_application_icon(icon_name)
                about.set_comments(comments)
                about.set_website(website)
                about.set_issue_url(issues)
                about.set_copyright(copyright_str)
                about.set_license_type(Gtk.License.GPL_3_0)
                about.present()
        except Exception as e:
            _log.error(f"Impossibile aprire AboutDialog: {e}")

    def _on_open_settings(self, widget: Gtk.Widget) -> None:
        from settings_window import open_settings_window
        open_settings_window(parent=self, application=self.get_application())

    def _on_close_request(self, window: Adw.ApplicationWindow) -> bool:
        if hasattr(self, "daemon_client") and self.daemon_client:
            self.daemon_client.close()
        app = self.get_application()
        if app:
            if getattr(app, "_assistant_win", None) is self:
                app._assistant_win = None
            GLib.idle_add(lambda: app.quit() if len(app.get_windows()) <= 1 else None)
        return False

    def _on_key_pressed(
        self,
        controller: Gtk.EventControllerKey,
        keyval: int,
        keycode: int,
        state: Gdk.ModifierType,
    ) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False
