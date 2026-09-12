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


class SkillsSettings:
    """Controlla la sottopagina 'Skills' e il relativo editor."""

    def __init__(self, builder: Gtk.Builder, settings: Gio.Settings | None, parent_window: Gtk.Window | None = None):
        self.builder = builder
        self.settings = settings
        self.parent_window = parent_window

        self.skills_page: Adw.NavigationPage | None = builder.get_object("skills_subpage")
        self.editor_page: Adw.NavigationPage | None = builder.get_object("skill_editor_subpage")
        self.list_group: Adw.PreferencesGroup | None = builder.get_object("skills_list_group")
        self.add_btn: Gtk.Button | None = builder.get_object("skills_add_btn")

        self.name_row: Adw.EntryRow | None = builder.get_object("skill_editor_name_row")
        self.intent_row: Adw.EntryRow | None = builder.get_object("skill_editor_intent_row")
        self.tool_row: Adw.EntryRow | None = builder.get_object("skill_editor_tool_row")
        self.args_view: Gtk.TextView | None = builder.get_object("skill_editor_args_view")
        self.triggers_view: Gtk.TextView | None = builder.get_object("skill_editor_triggers_view")
        self.save_btn: Gtk.Button | None = builder.get_object("skill_editor_save_btn")
        self.delete_btn: Gtk.Button | None = builder.get_object("skill_editor_delete_btn")
        self.error_row: Adw.ActionRow | None = builder.get_object("skill_editor_error_row")

        self._current_intent: str | None = None
        self._rows: list = []

        skills_row = builder.get_object("skills_subpage_row")
        if skills_row:
            skills_row.connect("activated", self._open_skills)
        if self.add_btn:
            self.add_btn.connect("clicked", lambda *_a: self._open_editor(None))
        if self.save_btn:
            self.save_btn.connect("clicked", self._on_save_clicked)
        if self.delete_btn:
            self.delete_btn.connect("clicked", self._on_delete_clicked)

    # ------------------------------------------------------------------
    def _win(self):
        if self.parent_window:
            return self.parent_window
        if self.skills_page and hasattr(self.skills_page, "get_root"):
            return self.skills_page.get_root()
        return None

    def _open_skills(self, *_args) -> None:
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

    # ------------------------------------------------------------------
    def _open_editor(self, skill: dict | None) -> None:
        is_editable = (skill is None) or bool(skill.get("is_custom"))
        self._current_intent = skill.get("intent") if skill else None

        if self.name_row:
            self.name_row.set_text(skill.get("name", "") if skill else "")
        if self.intent_row:
            self.intent_row.set_text(skill.get("intent", "") if skill else "")
            self.intent_row.set_sensitive(is_editable)
        if self.tool_row:
            self.tool_row.set_text(skill.get("tool", "") if skill else "")
        if self.args_view:
            args = skill.get("args") if skill else None
            self.args_view.get_buffer().set_text(json.dumps(args, indent=2) if args else "{}")
        if self.triggers_view:
            triggers = skill.get("triggers") if skill else []
            self.triggers_view.get_buffer().set_text("\n".join(triggers or []))
        if self.error_row:
            self.error_row.set_visible(False)
        if self.delete_btn:
            self.delete_btn.set_visible(is_editable and skill is not None)
        if self.save_btn:
            self.save_btn.set_visible(is_editable)
        for widget in (self.name_row, self.tool_row, self.args_view, self.triggers_view):
            if widget:
                widget.set_sensitive(is_editable)

        win = self._win()
        if win and self.editor_page and hasattr(win, "push_subpage"):
            if hasattr(win, "get_visible_page") and win.get_visible_page() == self.editor_page:
                pass
            else:
                win.push_subpage(self.editor_page)

    def _show_error(self, message: str) -> None:
        if self.error_row:
            self.error_row.set_subtitle(message)
            self.error_row.set_visible(True)

    def _collect_editor_skill(self):
        name = self.name_row.get_text().strip() if self.name_row else ""
        intent = self.intent_row.get_text().strip() if self.intent_row else ""
        tool = self.tool_row.get_text().strip() if self.tool_row else ""

        if not intent:
            self._show_error("L'intent è obbligatorio.")
            return None

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

        triggers_text = ""
        if self.triggers_view:
            buf = self.triggers_view.get_buffer()
            triggers_text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
        triggers = [line.strip() for line in triggers_text.splitlines() if line.strip()]
        if not triggers:
            self._show_error("Serve almeno una frase di attivazione.")
            return None

        return {"name": name or intent, "intent": intent, "tool": tool, "args": args, "triggers": triggers}

    def _on_save_clicked(self, *_args) -> None:
        skill = self._collect_editor_skill()
        if skill is None:
            return
        if self.save_btn:
            self.save_btn.set_sensitive(False)
        threading.Thread(target=self._save_thread, args=(skill,), daemon=True).start()

    def _save_thread(self, skill: dict) -> None:
        try:
            success, message = _dbus_call_sync(
                "SaveSkill", GLib.Variant("(s)", (json.dumps(skill),))
            )
        except Exception as e:
            success, message = False, str(e)
        GLib.idle_add(self._on_save_done, success, message)

    def _on_save_done(self, success: bool, message: str) -> None:
        if self.save_btn:
            self.save_btn.set_sensitive(True)
        if not success:
            self._show_error(message)
            return
        win = self._win()
        if win and hasattr(win, "pop_subpage"):
            win.pop_subpage()
        self.reload()

    def _on_delete_clicked(self, *_args) -> None:
        if not self._current_intent:
            return
        if self.delete_btn:
            self.delete_btn.set_sensitive(False)
        threading.Thread(target=self._delete_thread, args=(self._current_intent,), daemon=True).start()

    def _delete_thread(self, intent: str) -> None:
        try:
            success, message = _dbus_call_sync(
                "DeleteSkill", GLib.Variant("(s)", (intent,))
            )
        except Exception as e:
            success, message = False, str(e)
        GLib.idle_add(self._on_delete_done, success, message)

    def _on_delete_done(self, success: bool, message: str) -> None:
        if self.delete_btn:
            self.delete_btn.set_sensitive(True)
        if not success:
            self._show_error(message)
            return
        win = self._win()
        if win and hasattr(win, "pop_subpage"):
            win.pop_subpage()
        self.reload()
