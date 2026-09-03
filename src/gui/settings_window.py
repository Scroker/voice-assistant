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
from gi.repository import Gtk, Adw, Gio, Gdk

_SCHEMA = "org.gnome.shell.extensions.voice-assistant"
_PREFS_UI = "/org/gnome/shell/extensions/voice-assistant/ui/prefs.ui"
_ICON_RESOURCE_BASE = "/org/gnome/shell/extensions/voice-assistant/icons"


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

        self._register_icons()

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
    # Icon theme
    # ------------------------------------------------------------------

    def _register_icons(self) -> None:
        display = Gdk.Display.get_default()
        if not display:
            return
        try:
            theme = Gtk.IconTheme.get_for_display(display)
            # Resource path: GTK looks for {base}/hicolor/{size}/{type}/{name}.svg
            theme.add_resource_path(_ICON_RESOURCE_BASE)
            # Also search the installed extension directory on disk
            icons_dir = os.path.expanduser(
                "~/.local/share/gnome-shell/extensions/"
                "voice-assistant@scroker.github.io/icons"
            )
            if os.path.exists(icons_dir):
                theme.add_search_path(icons_dir)
        except Exception as e:
            print(f"[SettingsWindow] Icon registration failed: {e}")

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

        def _full(subdir: str | None = None) -> str:
            return os.path.join(os.path.expanduser(_base()), subdir) if subdir else os.path.expanduser(_base())

        # --- Base directory row (from Blueprint) ---
        base_row = self._b.get_object("models_path_row")
        if base_row:
            base_row.set_subtitle(_base())

        choose_btn = self._b.get_object("choose_path_btn")
        if choose_btn:
            choose_btn.connect("clicked", self._on_choose_models_dir)

        reset_btn = self._b.get_object("reset_path_btn")
        if reset_btn:
            reset_btn.connect("clicked", lambda _: self._settings.reset("models-dir"))

        # Repurpose clean_unused_btn as a Refresh button
        refresh_btn = self._b.get_object("clean_unused_btn")
        if refresh_btn:
            refresh_btn.set_label("Refresh")
            refresh_btn.get_style_context().remove_class("error")
            first_child = refresh_btn.get_first_child()
            if first_child:
                first_child.set_property("icon-name", "view-refresh-symbolic")

        models_page = self._b.get_object("models_page")
        if not models_page:
            return

        # --- Dynamic model listing (built programmatically) ---
        self._model_groups: list[Adw.PreferencesGroup] = []

        def _fmt_size(n: int) -> str:
            for unit in ("B", "KB", "MB", "GB"):
                if n < 1024:
                    return f"{n:.1f} {unit}"
                n /= 1024
            return f"{n:.1f} TB"

        def _entry_size(path: str) -> int:
            if os.path.isfile(path):
                return os.path.getsize(path)
            total = 0
            try:
                for e in os.scandir(path):
                    total += _entry_size(e.path)
            except OSError:
                pass
            return total

        def _scan(subdir: str | None) -> list[tuple[str, str, int]]:
            """Return list of (name, full_path, size_bytes) sorted by name."""
            root = _full(subdir)
            result = []
            try:
                for e in sorted(os.scandir(root), key=lambda x: x.name.lower()):
                    if e.name.startswith('.'):
                        continue
                    result.append((e.name, e.path, _entry_size(e.path)))
            except FileNotFoundError:
                pass
            return result

        def _make_item_row(name: str, size: int) -> Adw.ActionRow:
            row = Adw.ActionRow()
            row.set_title(name)
            row.set_subtitle(_fmt_size(size))
            return row

        def _build_wakeword_group() -> Adw.PreferencesGroup:
            group = Adw.PreferencesGroup()
            group.set_title("Wake Word")
            engine = self._settings.get_string("wakeword-engine") or "vosk"

            engine_row = Adw.ActionRow()
            engine_row.set_title("Engine")
            engine_row.set_subtitle({"vosk": "Vosk", "openwakeword": "OpenWakeWord", "sherpa-onnx": "Sherpa-ONNX"}.get(engine, engine))
            group.add(engine_row)

            if engine == "vosk":
                ww = self._settings.get_string("wakeword") or "assistente"
                model_row = Adw.ActionRow()
                model_row.set_title("Keyword")
                model_row.set_subtitle(ww)
                group.add(model_row)
                for name, _path, size in _scan("stt"):
                    group.add(_make_item_row(name, size))
            elif engine == "openwakeword":
                kw = self._settings.get_string("oww-model") or "alexa"
                model_row = Adw.ActionRow()
                model_row.set_title("Keyword")
                model_row.set_subtitle(f"{kw} (bundled)")
                group.add(model_row)
            elif engine == "sherpa-onnx":
                model_dir = self._settings.get_string("sherpa-ww-model-dir") or ""
                model_row = Adw.ActionRow()
                model_row.set_title("Model directory")
                model_row.set_subtitle(model_dir or "Not configured")
                group.add(model_row)

            return group

        def _build_dir_group(title: str, subdir: str) -> Adw.PreferencesGroup:
            group = Adw.PreferencesGroup()
            group.set_title(title)

            open_btn = Gtk.Button(
                label="Open",
                valign=Gtk.Align.CENTER,
                has_frame=False,
            )
            sub = subdir
            open_btn.connect("clicked", lambda _b: (
                os.makedirs(_full(sub), exist_ok=True),
                Gio.AppInfo.launch_default_for_uri(f"file://{_full(sub)}", None),
            ))
            group.set_header_suffix(open_btn)

            items = _scan(subdir)
            if items:
                for name, _path, size in items:
                    group.add(_make_item_row(name, size))
            else:
                empty_row = Adw.ActionRow()
                empty_row.set_title("No models found")
                empty_row.set_subtitle(_full(subdir))
                group.add(empty_row)

            return group

        def _refresh(*_):
            for g in self._model_groups:
                try:
                    models_page.remove(g)
                except Exception:
                    pass
            self._model_groups.clear()

            if base_row:
                base_row.set_subtitle(_base())

            for builder in (
                lambda: _build_wakeword_group(),
                lambda: _build_dir_group("Speech Recognition (STT)", "stt"),
                lambda: _build_dir_group("Language Model (LLM)",     "llm"),
                lambda: _build_dir_group("Text-to-Speech (TTS)",     "tts"),
            ):
                g = builder()
                models_page.add(g)
                self._model_groups.append(g)

        _refresh()

        if refresh_btn:
            refresh_btn.connect("clicked", _refresh)

        for key in ("models-dir", "wakeword-engine", "oww-model", "sherpa-ww-model-dir"):
            self._settings.connect(f"changed::{key}", _refresh)

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
