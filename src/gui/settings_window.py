"""
SettingsWindow — Preferences window native for the GUI.

Opens prefs.ui from GResource as an Adw.Window transient_for the GUI.
Used only when settings are opened from the GUI window.
When opened from the GNOME Shell extension panel, the standard
gnome-extensions prefs mechanism is used instead (no parent relationship).
"""

from __future__ import annotations

import os
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio

_SCHEMA = "org.gnome.shell.extensions.voice-assistant"
_PREFS_UI = "/org/gnome/shell/extensions/voice-assistant/ui/prefs.ui"


def open_settings_window(parent=None, application=None) -> None:
    """Open the settings window, optionally transient_for *parent*.

    *application* should be the active Adw.Application so the window is
    registered with it (required when no chat window is open).
    """
    try:
        win = _SettingsWindow(transient_for=parent)
        app = application or Adw.Application.get_default()
        if app:
            win.set_application(app)
        win.present()
    except Exception as e:
        print(f"[SettingsWindow] Cannot open settings window: {e}")


class _SettingsWindow(Adw.Window):

    def __init__(self, transient_for=None):
        super().__init__()
        self.set_title("Preferenze — Assistente Vocale")
        self.set_default_size(860, 600)
        if transient_for:
            self.set_transient_for(transient_for)
            self.set_modal(False)

        try:
            self._settings = Gio.Settings.new(_SCHEMA)
        except Exception:
            self._settings = None

        builder = Gtk.Builder()
        builder.add_from_resource(_PREFS_UI)
        self._b = builder

        split_view = builder.get_object("split_view")
        if not split_view:
            raise RuntimeError("split_view not found in prefs.ui")
        self.set_content(split_view)

        try:
            bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 600px"))
            bp.add_setter(split_view, "collapsed", True)
            self.add_breakpoint(bp)
        except Exception:
            pass

        self._setup_navigation()
        self._setup_bindings()

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _setup_navigation(self):
        sidebar = self._b.get_object("sidebar_list_box")
        stack = self._b.get_object("stack")
        content_title = self._b.get_object("content_title")
        main_nav_page = self._b.get_object("main_content_nav_page")
        split_view = self._b.get_object("split_view")

        if not sidebar or not stack:
            return

        page_defs = [
            ("row_general",  "general_page",  "General"),
            ("row_wakeword", "wakeword_page", "Wake Word"),
            ("row_stt",      "stt_page",      "Speech Engine (STT)"),
            ("row_llm",      "llm_page",      "Artificial Intelligence (LLM)"),
            ("row_tts",      "tts_page",      "Text-to-Speech (TTS)"),
            ("row_mcp",      "mcp_page",      "Tools (MCP)"),
            ("row_models",   "models_page",   "Storage and Models"),
            ("row_about",    "about_page",    "About"),
        ]

        row_map = {}
        for row_id, page_id, title in page_defs:
            row = self._b.get_object(row_id)
            page = self._b.get_object(page_id)
            if row and page:
                row_map[row] = (page, title)

        def on_row_selected(listbox, row):
            if not row or row not in row_map:
                return
            page, title = row_map[row]
            stack.set_visible_child(page)
            if content_title:
                content_title.set_title(title)
            if main_nav_page:
                main_nav_page.set_title(title)
            if split_view:
                split_view.set_show_content(True)

        sidebar.connect("row-selected", on_row_selected)
        first = sidebar.get_row_at_index(0)
        if first:
            sidebar.select_row(first)

    # ------------------------------------------------------------------
    # GSettings helpers
    # ------------------------------------------------------------------

    def _bind(self, key: str, widget_id: str, prop: str,
              flags=Gio.SettingsBindFlags.DEFAULT) -> None:
        if not self._settings:
            return
        widget = self._b.get_object(widget_id)
        if not widget:
            return
        try:
            self._settings.bind(key, widget, prop, flags)
        except Exception as e:
            print(f"[SettingsWindow] bind {key}→{widget_id}.{prop}: {e}")

    def _radio_group(self, key: str, radio_value_map: dict) -> None:
        """Connect a group of Gtk.CheckButton radio buttons to a GSettings string key."""
        if not self._settings:
            return
        current = self._settings.get_string(key)
        for widget_id, value in radio_value_map.items():
            radio = self._b.get_object(widget_id)
            if not radio:
                continue
            if value == current:
                radio.set_active(True)

            def _on_active(r, _pspec, v=value, k=key):
                if r.get_active():
                    self._settings.set_string(k, v)

            radio.connect("notify::active", _on_active)

    # ------------------------------------------------------------------
    # Bindings setup
    # ------------------------------------------------------------------

    def _setup_bindings(self):
        self._setup_general()
        self._setup_wakeword()
        self._setup_stt()
        self._setup_llm()
        self._setup_tts()
        self._setup_models()
        self._setup_about()

    def _setup_general(self):
        self._bind("enabled", "enable_switch_row", "active")

    def _setup_wakeword(self):
        self._radio_group("language", {
            "lang_it_radio": "it",
            "lang_en_radio": "en",
        })
        self._radio_group("wakeword-engine", {
            "ww_engine_vosk_radio":   "vosk",
            "ww_engine_oww_radio":    "openwakeword",
            "ww_engine_sherpa_radio": "sherpa-onnx",
        })
        self._bind("wakeword", "wakeword_row", "text")
        self._bind("sherpa-ww-model-dir", "sherpa_model_dir_row", "text")
        self._radio_group("oww-model", {
            "oww_alexa_radio":       "alexa",
            "oww_hey_jarvis_radio":  "hey_jarvis",
            "oww_hey_mycroft_radio": "hey_mycroft",
            "oww_hey_rhasspy_radio": "hey_rhasspy",
        })
        # Wire engine-dependent visibility
        self._apply_engine_visibility()
        for wid in ("ww_engine_vosk_radio", "ww_engine_oww_radio", "ww_engine_sherpa_radio"):
            r = self._b.get_object(wid)
            if r:
                r.connect("notify::active", lambda *_: self._apply_engine_visibility())

    def _apply_engine_visibility(self):
        oww = self._b.get_object("ww_engine_oww_radio")
        sherpa = self._b.get_object("ww_engine_sherpa_radio")
        is_oww = bool(oww and oww.get_active())
        is_sherpa = bool(sherpa and sherpa.get_active())
        for wid, visible in [
            ("wakeword_row",       not is_oww),
            ("oww_keyword_group",  is_oww),
            ("sherpa_model_dir_row", is_sherpa),
        ]:
            w = self._b.get_object(wid)
            if w:
                w.set_visible(visible)

    def _setup_stt(self):
        # Local vs Cloud mode
        local_radio = self._b.get_object("stt_mode_local_radio")
        cloud_radio = self._b.get_object("stt_mode_cloud_radio")
        openai_radio = self._b.get_object("stt_cloud_openai_radio")
        groq_radio = self._b.get_object("stt_cloud_groq_radio")

        if local_radio and cloud_radio and self._settings:
            provider = self._settings.get_string("stt-provider") or "vosk"
            is_cloud = provider in ("openai_cloud", "groq_cloud")
            if is_cloud:
                cloud_radio.set_active(True)
            else:
                local_radio.set_active(True)

            def _on_local(r, _p):
                if r.get_active():
                    self._settings.set_string("stt-provider", "vosk")
            def _on_cloud(r, _p):
                if r.get_active() and self._settings.get_string("stt-provider") not in ("openai_cloud", "groq_cloud"):
                    self._settings.set_string("stt-provider", "openai_cloud")
                    self._settings.set_string("stt-model", "whisper-1")

            local_radio.connect("notify::active", _on_local)
            cloud_radio.connect("notify::active", _on_cloud)

        if openai_radio and groq_radio and self._settings:
            provider = self._settings.get_string("stt-provider") or "vosk"
            if provider == "groq_cloud":
                groq_radio.set_active(True)
            elif provider == "openai_cloud":
                openai_radio.set_active(True)

            def _on_openai(r, _p):
                if r.get_active():
                    self._settings.set_string("stt-provider", "openai_cloud")
                    self._settings.set_string("stt-model", "whisper-1")
            def _on_groq(r, _p):
                if r.get_active():
                    self._settings.set_string("stt-provider", "groq_cloud")
                    self._settings.set_string("stt-model", "whisper-large-v3")

            openai_radio.connect("notify::active", _on_openai)
            groq_radio.connect("notify::active", _on_groq)

        self._radio_group("stt-hardware", {
            "hw_cpu_radio":  "cpu",
            "hw_cuda_radio": "cuda",
        })
        self._bind("stt-extra", "stt_cloud_api_key_row", "text")
        self._bind("stt-model", "stt_cloud_model_row", "text")

    def _setup_llm(self):
        self._bind("llm-enabled", "llm_enable_row", "active")
        self._radio_group("llm-mode", {
            "llm_mode_local_radio":     "local",
            "llm_mode_ollama_radio":    "ollama",
            "llm_mode_openai_radio":    "openai",
            "llm_mode_anthropic_radio": "anthropic",
            "llm_mode_custom_radio":    "custom",
        })
        self._bind("llm-api-key",       "llm_api_key_row",       "text")
        self._bind("llm-system-prompt", "llm_system_prompt_row", "text")
        self._bind("llm-url",           "llm_url_row",           "text")
        self._bind("llm-model",         "llm_model_row",         "text")

    def _setup_tts(self):
        self._bind("tts-enabled", "tts_enable_row", "active")
        self._radio_group("tts-engine", {
            "tts_engine_piper_radio":  "piper",
            "tts_engine_espeak_radio": "espeak",
            "tts_engine_openai_radio": "openai",
            "tts_engine_system_radio": "system",
        })
        self._bind("tts-voice", "tts_voice_row", "text")

    _DEFAULT_MODELS_DIR = "~/.local/share/voice-assistant/models"

    def _setup_models(self):
        if not self._settings:
            return

        def _base() -> str:
            return self._settings.get_string("models-dir") or self._DEFAULT_MODELS_DIR

        def _expand(path: str) -> str:
            return os.path.expanduser(path)

        def _update_paths(*_):
            base = _base()
            exp = _expand(base)
            row = self._b.get_object("models_path_row")
            if row:
                row.set_subtitle(base)
            for widget_id, subdir in (
                ("stt_path_row", "stt"),
                ("llm_path_row", "llm"),
                ("tts_path_row", "tts"),
            ):
                r = self._b.get_object(widget_id)
                if r:
                    r.set_subtitle(os.path.join(exp, subdir))

        _update_paths()
        self._settings.connect("changed::models-dir", _update_paths)

        choose_btn = self._b.get_object("choose_path_btn")
        if choose_btn:
            choose_btn.connect("clicked", self._on_choose_models_dir)

        reset_btn = self._b.get_object("reset_path_btn")
        if reset_btn:
            reset_btn.connect("clicked", lambda _: self._settings.reset("models-dir"))

        # Per-subfolder "open in Files" buttons
        def _make_opener(subdir: str | None = None):
            def _open(_btn):
                base = _expand(_base())
                path = os.path.join(base, subdir) if subdir else base
                os.makedirs(path, exist_ok=True)
                Gio.AppInfo.launch_default_for_uri(f"file://{path}", None)
            return _open

        for btn_id, sub in (
            ("open_stt_btn",    "stt"),
            ("open_llm_btn",    "llm"),
            ("open_tts_btn",    "tts"),
            ("open_models_btn", None),
        ):
            btn = self._b.get_object(btn_id)
            if btn:
                btn.connect("clicked", _make_opener(sub))

    def _on_choose_models_dir(self, _btn):
        chooser = Gtk.FileChooserNative(
            title="Seleziona directory modelli",
            action=Gtk.FileChooserAction.SELECT_FOLDER,
            transient_for=self,
            modal=True,
        )
        def on_response(dialog, response):
            if response == Gtk.ResponseType.ACCEPT:
                folder = dialog.get_file()
                if folder and self._settings:
                    self._settings.set_string("models-dir", folder.get_path())
            dialog.destroy()
        chooser.connect("response", on_response)
        chooser.show()

    def _setup_about(self):
        doc_btn = self._b.get_object("doc_btn")
        if doc_btn:
            doc_btn.connect("clicked", lambda _: Gio.AppInfo.launch_default_for_uri(
                "https://github.com/Scroker/voice-assistant", None
            ))
