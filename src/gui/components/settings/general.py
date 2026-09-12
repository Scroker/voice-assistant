"""
Componente per le impostazioni generali e la selezione della lingua.
"""

import os
import sys
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gio', '2.0')
from gi.repository import Gtk, Adw, Gio

from .base import bind_setting

def _load_supported_languages() -> list[dict[str, str]]:
    try:
        from core.locale_utils import get_supported_languages
        return get_supported_languages()
    except ImportError:
        d = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "daemon"))
        if d not in sys.path:
            sys.path.insert(0, d)
        try:
            from core.locale_utils import get_supported_languages
            return get_supported_languages()
        except Exception:
            pass
    return [
        {"code": "it", "name": "Italiano", "english_name": "Italian"},
        {"code": "en", "name": "English", "english_name": "English"},
        {"code": "de", "name": "Deutsch", "english_name": "German"},
        {"code": "fr", "name": "Français", "english_name": "French"},
        {"code": "es", "name": "Español", "english_name": "Spanish"},
        {"code": "pt", "name": "Português", "english_name": "Portuguese"},
        {"code": "nl", "name": "Nederlands", "english_name": "Dutch"},
        {"code": "ru", "name": "Русский", "english_name": "Russian"},
        {"code": "zh", "name": "中文", "english_name": "Chinese"},
        {"code": "ja", "name": "日本語", "english_name": "Japanese"},
        {"code": "ko", "name": "한국어", "english_name": "Korean"},
        {"code": "pl", "name": "Polski", "english_name": "Polish"},
        {"code": "uk", "name": "Українська", "english_name": "Ukrainian"},
        {"code": "tr", "name": "Türkçe", "english_name": "Turkish"},
        {"code": "sv", "name": "Svenska", "english_name": "Swedish"},
    ]


SUPPORTED_LANGUAGES = _load_supported_languages()


def get_system_language_code() -> str:
    """Rileva la lingua di sistema o restituisce 'en' come fallback."""
    try:
        from core.locale_utils import get_system_language
        return get_system_language()
    except ImportError:
        d = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "daemon"))
        if d not in sys.path:
            sys.path.insert(0, d)
        try:
            from core.locale_utils import get_system_language
            return get_system_language()
        except Exception:
            return "en"


def get_language_label(code: str) -> str:
    """Restituisce l'etichetta leggibile per il codice lingua specificato."""
    try:
        from core.locale_utils import get_language_label as _core_get_label
        return _core_get_label(code)
    except Exception:
        for item in SUPPORTED_LANGUAGES:
            if item["code"] == code:
                return f"{item['name']} ({item['code']})"
        return f"{code.upper()} ({code})"


class LanguageSelector:
    """Gestisce la vista e la logica di selezione lingua."""

    def __init__(self, builder: Gtk.Builder, settings: Gio.Settings | None, parent_window: Gtk.Window | None = None):
        self.builder = builder
        self.settings = settings
        self.parent_window = parent_window
        self.lang_rows: list[Adw.ActionRow] = []
        self._setup()

    def _setup(self) -> None:
        lang_selection_row = self.builder.get_object("lang_selection_row")
        content_nav = self.builder.get_object("content_navigation_view")
        lang_nav_page = self.builder.get_object("lang_nav_page")
        lang_search_entry = self.builder.get_object("lang_search_entry")
        lang_list_box = self.builder.get_object("lang_list_box")

        if not lang_selection_row:
            return

        saved_c = self.settings.get_string("language") if self.settings else ""
        curr_code = saved_c.strip() if saved_c and saved_c.strip() else get_system_language_code()

        lang_selection_row.set_subtitle(get_language_label(curr_code))

        def _open_lang_page(*_):
            win = self.parent_window or (lang_selection_row.get_root() if hasattr(lang_selection_row, "get_root") else None)
            if win and hasattr(win, "push_subpage") and lang_nav_page:
                win.push_subpage(lang_nav_page)
            elif content_nav and lang_nav_page:
                content_nav.push(lang_nav_page)

        lang_selection_row.connect("activated", _open_lang_page)

        if not lang_list_box:
            return

        first_radio = None

        for item in SUPPORTED_LANGUAGES:
            code = item["code"]
            row = Adw.ActionRow()
            row.set_title(item["name"])
            row.set_subtitle(f"{item['english_name']} • {code}")
            row.set_activatable(True)

            radio = Gtk.CheckButton()
            radio.set_can_focus(False)
            radio.set_valign(Gtk.Align.CENTER)
            if first_radio is None:
                first_radio = radio
            else:
                radio.set_group(first_radio)

            if code == curr_code:
                radio.set_active(True)

            row.add_prefix(radio)
            row._lang_item = item  # type: ignore[attr-defined]
            row._radio = radio  # type: ignore[attr-defined]

            def _on_row_activated(r, target_code=code):
                if hasattr(r, "_radio"):
                    r._radio.set_active(True)
                if self.settings:
                    self.settings.set_string("language", target_code)
                lang_selection_row.set_subtitle(get_language_label(target_code))
                win = self.parent_window or (lang_selection_row.get_root() if hasattr(lang_selection_row, "get_root") else None)
                if win and hasattr(win, "pop_subpage"):
                    win.pop_subpage()
                elif content_nav:
                    content_nav.pop()

            row.connect("activated", _on_row_activated)
            lang_list_box.append(row)
            self.lang_rows.append(row)

        if lang_search_entry:
            def _filter_row(row):
                if not hasattr(row, "_lang_item"):
                    return True
                q = (lang_search_entry.get_text() or "").strip().lower()
                if not q:
                    return True
                it = row._lang_item
                return (
                    q in it["code"].lower()
                    or q in it["name"].lower()
                    or q in it["english_name"].lower()
                )

            lang_list_box.set_filter_func(_filter_row)
            lang_search_entry.connect("search-changed", lambda _: lang_list_box.invalidate_filter())

        if self.settings:
            def _on_settings_lang_changed(*_):
                raw_c = self.settings.get_string("language") if self.settings else ""
                new_c = raw_c.strip() if raw_c and raw_c.strip() else get_system_language_code()
                lang_selection_row.set_subtitle(get_language_label(new_c))
                for r in self.lang_rows:
                    if hasattr(r, "_lang_item") and hasattr(r, "_radio"):
                        if r._lang_item["code"] == new_c:
                            r._radio.set_active(True)

            self.settings.connect("changed::language", _on_settings_lang_changed)


class GeneralSettings:
    """Configura la pagina Generale delle impostazioni."""

    def __init__(self, builder: Gtk.Builder, settings: Gio.Settings | None, parent_window: Gtk.Window | None = None):
        self.builder = builder
        self.settings = settings
        self.parent_window = parent_window
        self._setup()

    def _setup(self) -> None:
        bind_setting(self.settings, "enabled", self.builder, "enable_switch_row", "active")
        self.language_selector = LanguageSelector(self.builder, self.settings, parent_window=self.parent_window)

        models_subpage_row = self.builder.get_object("models_subpage_row")
        models_subpage = self.builder.get_object("models_subpage")
        if models_subpage_row and models_subpage:
            def _open_models(*_):
                win = self.parent_window or (models_subpage_row.get_root() if hasattr(models_subpage_row, "get_root") else None)
                if win and hasattr(win, "push_subpage"):
                    win.push_subpage(models_subpage)
            models_subpage_row.connect("activated", _open_models)


        audio_subpage_row = self.builder.get_object("audio_subpage_row")
        audio_subpage = self.builder.get_object("audio_subpage")
        if audio_subpage_row and audio_subpage:
            def _open_audio(*_):
                win = self.parent_window or (audio_subpage_row.get_root() if hasattr(audio_subpage_row, "get_root") else None)
                if win and hasattr(win, "push_subpage"):
                    win.push_subpage(audio_subpage)
            audio_subpage_row.connect("activated", _open_audio)

        bugreport_subpage_row = self.builder.get_object("bugreport_subpage_row")
        bugreport_subpage = self.builder.get_object("bugreport_subpage")
        if bugreport_subpage_row and bugreport_subpage:
            def _open_bugreport(*_):
                win = self.parent_window or (bugreport_subpage_row.get_root() if hasattr(bugreport_subpage_row, "get_root") else None)
                if win and hasattr(win, "push_subpage"):
                    win.push_subpage(bugreport_subpage)
            bugreport_subpage_row.connect("activated", _open_bugreport)
