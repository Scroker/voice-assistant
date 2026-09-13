"""
Componente per la console di gestione delle skill (Direct Action Engine).

Elenca le skill predefinite (sola lettura) e quelle personalizzate
dell'utente, e permette di aggiungerne, modificarne o rimuoverne tramite
il demone via D-Bus (che scrive in ~/.config/voice-assistant/skills/ e
ricarica il router semantico a caldo).
"""

import json
import logging
import threading

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gio', '2.0')
gi.require_version('GLib', '2.0')
from gi.repository import Gtk, Adw, Gio, GLib

_log = logging.getLogger("VoiceAssistant.GUI.Settings.Skills")

_DBUS_NAME = "org.local.VoiceAssistant"
_DBUS_PATH = "/org/local/VoiceAssistant"
_DBUS_IFACE = "org.local.VoiceAssistant"


def _dbus_call_sync(method: str, args_variant=None, timeout_ms: int = 5000):
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    reply = bus.call_sync(
        _DBUS_NAME, _DBUS_PATH, _DBUS_IFACE, method,
        args_variant, None, Gio.DBusCallFlags.NONE, timeout_ms, None,
    )
    return reply.unpack()


def _unpack_pair(reply) -> tuple[bool, str]:
    """Estrae (success, message) da tuple o struct D-Bus restituite da Dasbus."""
    if isinstance(reply, (tuple, list)):
        if len(reply) == 1 and isinstance(reply[0], (tuple, list)) and len(reply[0]) >= 2:
            return bool(reply[0][0]), str(reply[0][1])
        if len(reply) >= 2:
            return bool(reply[0]), str(reply[1])
        if len(reply) == 1:
            return bool(reply[0]), ""
    return False, str(reply)


_KNOWN_SYSTEM_TOOLS = [
    "set_volume",
    "quick_settings",
    "launch_application",
    "media_control",
    "send_notification",
    "take_screenshot",
    "open_file",
    "set_wallpaper",
]


class SkillsSettings:
    """Controlla la vista 'Skills' e il relativo editor."""

    def __init__(
        self,
        builder: Gtk.Builder | None = None,
        settings: Gio.Settings | None = None,
        parent_window: Gtk.Window | None = None,
        nav_view: Adw.NavigationView | None = None,
        skills_page: Adw.NavigationPage | None = None,
        editor_page: Adw.NavigationPage | None = None,
        list_group: Adw.PreferencesGroup | None = None,
        add_btn: Gtk.Button | None = None,
        name_row: Adw.EntryRow | None = None,
        intent_row: Adw.EntryRow | None = None,
        type_checks: dict[str, Gtk.CheckButton] | None = None,
        type_rows: dict[str, Adw.ActionRow] | None = None,
        type_row: Adw.ComboRow | None = None,
        system_group: Adw.PreferencesGroup | None = None,
        system_tool_combo: Adw.ComboRow | None = None,
        tool_row: Adw.EntryRow | None = None,
        args_group: Adw.PreferencesGroup | None = None,
        args_view: Gtk.TextView | None = None,
        command_group: Adw.PreferencesGroup | None = None,
        command_row: Adw.EntryRow | None = None,
        prompt_group: Adw.PreferencesGroup | None = None,
        prompt_view: Gtk.TextView | None = None,
        response_group: Adw.PreferencesGroup | None = None,
        response_row: Adw.EntryRow | None = None,
        triggers_view: Gtk.TextView | None = None,
        save_btn: Adw.ButtonRow | Gtk.Button | None = None,
        delete_btn: Adw.ButtonRow | Gtk.Button | None = None,
        save_group: Adw.PreferencesGroup | None = None,
        delete_group: Adw.PreferencesGroup | None = None,
        editor_title: Adw.WindowTitle | None = None,
        toast_overlay: Adw.ToastOverlay | None = None,
    ):
        self.builder = builder
        self.settings = settings
        self.parent_window = parent_window
        self.toast_overlay = toast_overlay or (builder.get_object("toast_overlay") if builder else None)

        self.nav_view: Adw.NavigationView | None = nav_view
        self.skills_page: Adw.NavigationPage | None = skills_page or (builder.get_object("skills_subpage") if builder else None)
        self.editor_page: Adw.NavigationPage | None = editor_page or (builder.get_object("skill_editor_subpage") if builder else None)
        self.list_group: Adw.PreferencesGroup | None = list_group or (builder.get_object("skills_list_group") if builder else None)
        self.add_btn: Gtk.Button | None = add_btn or (builder.get_object("skills_add_btn") if builder else None)

        self.name_row: Adw.EntryRow | None = name_row or (builder.get_object("skill_editor_name_row") if builder else None)
        self.intent_row: Adw.EntryRow | None = intent_row or (builder.get_object("skill_editor_intent_row") if builder else None)

        self.type_checks: dict[str, Gtk.CheckButton] = dict(type_checks) if type_checks else {}
        if not self.type_checks and builder:
            for key in ("system", "command", "prompt", "response"):
                obj = builder.get_object(f"skill_type_{key}_check")
                if obj:
                    self.type_checks[key] = obj

        self.type_rows: dict[str, Adw.ActionRow] = dict(type_rows) if type_rows else {}
        if not self.type_rows and builder:
            for key in ("system", "command", "prompt", "response"):
                obj = builder.get_object(f"skill_type_{key}_row")
                if obj:
                    self.type_rows[key] = obj

        self.type_row: Adw.ComboRow | None = type_row or (builder.get_object("skill_editor_type_row") if builder else None)
        self.system_group: Adw.PreferencesGroup | None = system_group or (builder.get_object("skill_editor_system_group") if builder else None)
        self.system_tool_combo: Adw.ComboRow | None = system_tool_combo or (builder.get_object("skill_editor_system_tool_combo") if builder else None)
        self.tool_row: Adw.EntryRow | None = tool_row or (builder.get_object("skill_editor_tool_row") if builder else None)
        self.args_group: Adw.PreferencesGroup | None = args_group or (builder.get_object("skill_editor_args_group") if builder else None)
        self.args_view: Gtk.TextView | None = args_view or (builder.get_object("skill_editor_args_view") if builder else None)
        self.command_group: Adw.PreferencesGroup | None = command_group or (builder.get_object("skill_editor_command_group") if builder else None)
        self.command_row: Adw.EntryRow | None = command_row or (builder.get_object("skill_editor_command_row") if builder else None)
        self.prompt_group: Adw.PreferencesGroup | None = prompt_group or (builder.get_object("skill_editor_prompt_group") if builder else None)
        self.prompt_view: Gtk.TextView | None = prompt_view or (builder.get_object("skill_editor_prompt_view") if builder else None)
        self.response_group: Adw.PreferencesGroup | None = response_group or (builder.get_object("skill_editor_response_group") if builder else None)
        self.response_row: Adw.EntryRow | None = response_row or (builder.get_object("skill_editor_response_row") if builder else None)
        self.triggers_view: Gtk.TextView | None = triggers_view or (builder.get_object("skill_editor_triggers_view") if builder else None)
        self.save_btn: Adw.ButtonRow | Gtk.Button | None = save_btn or (builder.get_object("skill_editor_save_btn") if builder else None)
        self.delete_btn: Adw.ButtonRow | Gtk.Button | None = delete_btn or (builder.get_object("skill_editor_delete_btn") if builder else None)
        self.save_group: Adw.PreferencesGroup | None = save_group or (builder.get_object("skill_editor_save_group") if builder else None)
        self.delete_group: Adw.PreferencesGroup | None = delete_group or (builder.get_object("skill_editor_delete_group") if builder else None)
        self.editor_title: Adw.WindowTitle | None = editor_title or (builder.get_object("skill_editor_title") if builder else None)

        self._current_intent: str | None = None
        self._current_skill: dict | None = None
        self._initial_skill_state: dict = {}
        self._loading_editor: bool = False
        self._rows: list = []

        skills_row = builder.get_object("skills_subpage_row") if builder else None
        if skills_row:
            skills_row.connect("activated", self._open_skills)
        if self.add_btn:
            self.add_btn.connect("clicked", lambda *_a: self._open_editor(None))
        if self.save_btn:
            signal = "activated" if isinstance(self.save_btn, Adw.ButtonRow) else "clicked"
            self.save_btn.connect(signal, self._on_save_clicked)
        if self.delete_btn:
            signal = "activated" if isinstance(self.delete_btn, Adw.ButtonRow) else "clicked"
            self.delete_btn.connect(signal, self._on_delete_clicked)

        if self.name_row:
            self.name_row.connect("notify::text", self._on_field_changed)
        if self.intent_row:
            self.intent_row.connect("notify::text", self._on_field_changed)

        for action_type, check in self.type_checks.items():
            if check:
                check.connect("toggled", self._on_type_check_toggled, action_type)

        if self.type_row:
            self.type_row.connect("notify::selected", self._on_action_type_changed)
        if self.system_tool_combo:
            self.system_tool_combo.connect("notify::selected", self._on_system_tool_combo_changed)
        if self.tool_row:
            self.tool_row.connect("notify::text", self._on_field_changed)
        if self.command_row:
            self.command_row.connect("notify::text", self._on_field_changed)
        if self.response_row:
            self.response_row.connect("notify::text", self._on_field_changed)
        if self.args_view:
            self.args_view.get_buffer().connect("changed", self._on_field_changed)
        if self.prompt_view:
            self.prompt_view.get_buffer().connect("changed", self._on_field_changed)
        if self.triggers_view:
            self.triggers_view.get_buffer().connect("changed", self._on_field_changed)

    # ------------------------------------------------------------------
    def _win(self):
        if self.parent_window:
            return self.parent_window
        if self.skills_page and hasattr(self.skills_page, "get_root"):
            return self.skills_page.get_root()
        return None

    def _open_skills(self, *_args) -> None:
        if self.nav_view and self.skills_page:
            if hasattr(self.nav_view, "get_visible_page") and self.nav_view.get_visible_page() == self.skills_page:
                pass
            else:
                self.nav_view.pop_to_page(self.skills_page)
        else:
            win = self._win()
            if win and self.skills_page and hasattr(win, "push_subpage"):
                if hasattr(win, "get_visible_page") and win.get_visible_page() == self.skills_page:
                    pass
                else:
                    win.push_subpage(self.skills_page)
        self.reload()

    def reload(self) -> None:
        threading.Thread(target=self._load_thread, daemon=True).start()

    def _load_thread(self) -> None:
        try:
            (raw_json,) = _dbus_call_sync("GetSkills")
            skills = json.loads(raw_json)
        except Exception as e:
            _log.warning("Impossibile caricare le skill dal demone: %s", e)
            skills = []
        GLib.idle_add(self._populate, skills)

    def _populate(self, skills: list) -> None:
        if not self.list_group:
            return
        for row in self._rows:
            self.list_group.remove(row)
        self._rows.clear()

        if not skills:
            placeholder = Adw.ActionRow(title="Nessuna skill configurata")
            self.list_group.add(placeholder)
            self._rows.append(placeholder)
            return

        for skill in sorted(skills, key=lambda s: (s.get("name") or s.get("intent", "")).lower()):
            is_custom = bool(skill.get("is_custom"))
            triggers = skill.get("triggers") or []
            subtitle = ", ".join(triggers[:3]) + ("…" if len(triggers) > 3 else "")
            row = Adw.ActionRow(
                title=skill.get("name") or skill.get("intent", ""),
                subtitle=subtitle,
                activatable=True,
            )
            badge = Gtk.Label(label="Personalizzata" if is_custom else "Predefinita")
            badge.add_css_class("dim-label")
            badge.add_css_class("caption")
            row.add_suffix(badge)
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic", valign=Gtk.Align.CENTER))
            row.connect("activated", lambda _r, s=skill: self._open_editor(s))
            self.list_group.add(row)
            self._rows.append(row)

    def _on_type_check_toggled(self, button: Gtk.CheckButton, action_type: str) -> None:
        if not button.get_active():
            return
        self._update_action_type_ui(action_type)
        if not self._loading_editor:
            self._on_field_changed()

    def get_selected_action_type(self) -> str:
        for action_type, check in self.type_checks.items():
            if check and check.get_active():
                return action_type
        if self.type_row:
            type_map = {0: "system", 1: "command", 2: "prompt", 3: "response"}
            return type_map.get(self.type_row.get_selected(), "system")
        return "system"

    def set_selected_action_type(self, action_type: str) -> None:
        check = self.type_checks.get(action_type)
        if check:
            check.set_active(True)
        elif self.type_checks:
            default_check = self.type_checks.get("system")
            if default_check:
                default_check.set_active(True)
        if self.type_row:
            rev_map = {"system": 0, "command": 1, "prompt": 2, "response": 3}
            self.type_row.set_selected(rev_map.get(action_type, 0))
        self._update_action_type_ui(action_type)

    def _update_action_type_ui(self, action_type: str) -> None:
        if self.system_group:
            self.system_group.set_visible(action_type == "system")
        if self.args_group:
            self.args_group.set_visible(action_type == "system")
        if self.command_group:
            self.command_group.set_visible(action_type == "command")
        if self.prompt_group:
            self.prompt_group.set_visible(action_type == "prompt")
        if self.response_group:
            self.response_group.set_visible(action_type == "response")

    def _on_action_type_changed(self, *_args) -> None:
        idx = self.type_row.get_selected() if self.type_row else 0
        type_map = {0: "system", 1: "command", 2: "prompt", 3: "response"}
        action_type = type_map.get(idx, "system")
        self._update_action_type_ui(action_type)
        if not self._loading_editor:
            self._on_field_changed()

    def _on_system_tool_combo_changed(self, *_args) -> None:
        if self._loading_editor or not self.system_tool_combo:
            return
        idx = self.system_tool_combo.get_selected()
        if idx < len(_KNOWN_SYSTEM_TOOLS) and self.tool_row:
            self.tool_row.set_text(_KNOWN_SYSTEM_TOOLS[idx])
        self._on_field_changed()

    def _get_editor_state(self) -> dict:
        args_text = ""
        if self.args_view:
            buf = self.args_view.get_buffer()
            args_text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
        triggers_text = ""
        if self.triggers_view:
            buf = self.triggers_view.get_buffer()
            triggers_text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
        prompt_text = ""
        if self.prompt_view:
            buf = self.prompt_view.get_buffer()
            prompt_text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
        return {
            "name": self.name_row.get_text() if self.name_row else "",
            "intent": self.intent_row.get_text() if self.intent_row else "",
            "action_type": self.get_selected_action_type(),
            "tool": self.tool_row.get_text() if self.tool_row else "",
            "args": args_text,
            "command": self.command_row.get_text() if self.command_row else "",
            "prompt": prompt_text,
            "response": self.response_row.get_text() if self.response_row else "",
            "triggers": triggers_text,
        }

    def _update_save_visibility(self) -> None:
        if not self.save_btn:
            return
        is_editable = (self._current_skill is None) or bool(self._current_skill.get("is_custom"))
        if not is_editable:
            self.save_btn.set_visible(False)
            if self.save_group:
                self.save_group.set_visible(False)
            return
        if self._current_skill is None:
            self.save_btn.set_visible(True)
            if self.save_group:
                self.save_group.set_visible(True)
            return
        is_dirty = self._get_editor_state() != self._initial_skill_state
        self.save_btn.set_visible(is_dirty)
        if self.save_group:
            self.save_group.set_visible(is_dirty)

    def _on_field_changed(self, *_args) -> None:
        if self._loading_editor:
            return
        self._update_save_visibility()

    # ------------------------------------------------------------------
    def _open_editor(self, skill: dict | None) -> None:
        self._loading_editor = True
        self._current_skill = skill
        is_editable = (skill is None) or bool(skill.get("is_custom"))
        self._current_intent = skill.get("intent") if skill else None

        action_type = skill.get("action_type") if skill else "system"
        self.set_selected_action_type(action_type)

        for check in self.type_checks.values():
            if check:
                check.set_sensitive(is_editable)
        for row in self.type_rows.values():
            if row:
                row.set_sensitive(is_editable)
        if self.type_row:
            self.type_row.set_sensitive(is_editable)

        if self.name_row:
            self.name_row.set_text(skill.get("name", "") if skill else "")
        if self.intent_row:
            self.intent_row.set_text(skill.get("intent", "") if skill else "")
            self.intent_row.set_sensitive(is_editable)

        tool_val = skill.get("tool", "") if skill else ""
        if self.tool_row:
            self.tool_row.set_text(tool_val)
        if self.system_tool_combo:
            if tool_val in _KNOWN_SYSTEM_TOOLS:
                self.system_tool_combo.set_selected(_KNOWN_SYSTEM_TOOLS.index(tool_val))
            elif tool_val:
                self.system_tool_combo.set_selected(len(_KNOWN_SYSTEM_TOOLS))
            else:
                self.system_tool_combo.set_selected(0)
            self.system_tool_combo.set_sensitive(is_editable)

        if self.command_row:
            self.command_row.set_text(skill.get("command", "") if skill else "")
        if self.response_row:
            self.response_row.set_text(skill.get("response", "") if skill else "")

        if self.prompt_view:
            prompt_val = skill.get("prompt", "") if skill else ""
            self.prompt_view.get_buffer().set_text(prompt_val)

        if self.args_view:
            args = skill.get("args") if skill else None
            self.args_view.get_buffer().set_text(json.dumps(args, indent=2) if args else "{}")
        if self.triggers_view:
            triggers = skill.get("triggers") if skill else []
            self.triggers_view.get_buffer().set_text("\n".join(triggers or []))
        can_delete = is_editable and skill is not None
        if self.delete_btn:
            self.delete_btn.set_visible(can_delete)
        if self.delete_group:
            self.delete_group.set_visible(can_delete)

        for widget in (
            self.name_row,
            self.tool_row,
            self.command_row,
            self.response_row,
            self.prompt_view,
            self.args_view,
            self.triggers_view,
        ):
            if widget:
                widget.set_sensitive(is_editable)

        if self.editor_title:
            self.editor_title.set_title("Modifica Skill" if skill else "Nuova Skill")

        self._on_action_type_changed()
        self._initial_skill_state = self._get_editor_state()
        self._loading_editor = False
        self._update_save_visibility()

        if self.nav_view and self.editor_page:
            if hasattr(self.nav_view, "get_visible_page") and self.nav_view.get_visible_page() == self.editor_page:
                pass
            else:
                self.nav_view.push(self.editor_page)
        else:
            win = self._win()
            if win and self.editor_page and hasattr(win, "push_subpage"):
                if hasattr(win, "get_visible_page") and win.get_visible_page() == self.editor_page:
                    pass
                else:
                    win.push_subpage(self.editor_page)

    def _show_toast(self, message: str, priority: Adw.ToastPriority = Adw.ToastPriority.NORMAL) -> None:
        toast = Adw.Toast.new(message)
        toast.set_priority(priority)
        if self.toast_overlay:
            self.toast_overlay.add_toast(toast)
        elif self.parent_window and hasattr(self.parent_window, "add_toast"):
            self.parent_window.add_toast(toast)
        elif self.parent_window and hasattr(self.parent_window, "toast_overlay") and self.parent_window.toast_overlay:
            self.parent_window.toast_overlay.add_toast(toast)
        else:
            win = self._win()
            if win and hasattr(win, "add_toast"):
                win.add_toast(toast)
            elif win and hasattr(win, "toast_overlay") and win.toast_overlay:
                win.toast_overlay.add_toast(toast)

    def _show_error(self, message: str) -> None:
        self._show_toast(message, priority=Adw.ToastPriority.HIGH)

    def _collect_editor_skill(self):
        name = self.name_row.get_text().strip() if self.name_row else ""
        intent = self.intent_row.get_text().strip() if self.intent_row else ""

        if not intent:
            self._show_error("L'intent è obbligatorio.")
            return None

        triggers_text = ""
        if self.triggers_view:
            buf = self.triggers_view.get_buffer()
            triggers_text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
        triggers = [line.strip() for line in triggers_text.splitlines() if line.strip()]
        if not triggers:
            self._show_error("Serve almeno una frase di attivazione.")
            return None

        action_type = self.get_selected_action_type()

        tool = ""
        args = {}
        command = ""
        prompt = ""
        response = ""

        if action_type == "system":
            tool = self.tool_row.get_text().strip() if self.tool_row else ""
            args_text = ""
            if self.args_view:
                buf = self.args_view.get_buffer()
                args_text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True).strip()
            try:
                args = json.loads(args_text) if args_text else {}
                if not isinstance(args, dict):
                    raise ValueError("args must be a JSON object")
            except Exception:
                self._show_error('Args deve essere un JSON valido, es. {"setting": "wifi", "enabled": true}.')
                return None
        elif action_type == "command":
            command = self.command_row.get_text().strip() if self.command_row else ""
            if not command:
                self._show_error("Il comando terminale è obbligatorio.")
                return None
        elif action_type == "prompt":
            if self.prompt_view:
                buf = self.prompt_view.get_buffer()
                prompt = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True).strip()
            if not prompt:
                self._show_error("Le istruzioni/prompt per l'AI sono obbligatorie.")
                return None
        elif action_type == "response":
            response = self.response_row.get_text().strip() if self.response_row else ""
            if not response:
                self._show_error("Il testo della risposta è obbligatorio.")
                return None

        return {
            "name": name or intent,
            "intent": intent,
            "action_type": action_type,
            "tool": tool,
            "args": args,
            "command": command,
            "prompt": prompt,
            "response": response,
            "triggers": triggers,
        }

    def _on_save_clicked(self, *_args) -> None:
        skill = self._collect_editor_skill()
        if skill is None:
            return
        if self.save_btn:
            self.save_btn.set_sensitive(False)
        threading.Thread(target=self._save_thread, args=(skill,), daemon=True).start()

    def _save_thread(self, skill: dict) -> None:
        try:
            reply = _dbus_call_sync(
                "SaveSkill", GLib.Variant("(s)", (json.dumps(skill),))
            )
            success, message = _unpack_pair(reply)
        except Exception as e:
            success, message = False, str(e)
        GLib.idle_add(self._on_save_done, success, message)

    def _on_save_done(self, success: bool, message: str) -> None:
        if self.save_btn:
            self.save_btn.set_sensitive(True)
        if not success:
            self._show_error(message)
            return
        if self.nav_view:
            self.nav_view.pop()
        else:
            win = self._win()
            if win and hasattr(win, "pop_subpage"):
                win.pop_subpage()
        self._show_toast("Skill salvata con successo")
        self.reload()

    def _on_delete_clicked(self, *_args) -> None:
        if not self._current_intent:
            return
        if self.delete_btn:
            self.delete_btn.set_sensitive(False)
        threading.Thread(target=self._delete_thread, args=(self._current_intent,), daemon=True).start()

    def _delete_thread(self, intent: str) -> None:
        try:
            reply = _dbus_call_sync(
                "DeleteSkill", GLib.Variant("(s)", (intent,))
            )
            success, message = _unpack_pair(reply)
        except Exception as e:
            success, message = False, str(e)
        GLib.idle_add(self._on_delete_done, success, message)

    def _on_delete_done(self, success: bool, message: str) -> None:
        if self.delete_btn:
            self.delete_btn.set_sensitive(True)
        if not success:
            self._show_error(message)
            return
        if self.nav_view:
            self.nav_view.pop()
        else:
            win = self._win()
            if win and hasattr(win, "pop_subpage"):
                win.pop_subpage()
        self._show_toast("Skill eliminata con successo")
        self.reload()
