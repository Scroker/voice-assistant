"""
SettingsWindow — Preferences window native for the GUI.

Opens prefs.ui from GResource as an Adw.Window transient_for the GUI.
Used only when settings are opened from the GUI window.
When opened from the GNOME Shell extension panel, the standard
gnome-extensions prefs mechanism is used instead (no parent relationship).
"""

from __future__ import annotations

import os
import shutil
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib, Gdk

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
            ("row_models",     "models_page",     "Storage and Models"),
            ("row_bugreport",  "bugreport_page",  "Bug Reporting"),
            ("row_about",      "about_page",      "About"),
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
        self._setup_bugreport()
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
            base = os.path.expanduser(_base())
            return os.path.join(base, subdir) if subdir else base

        # --- All structure comes from Blueprint ---
        base_row    = self._b.get_object("models_path_row")
        choose_btn  = self._b.get_object("choose_path_btn")
        reset_btn   = self._b.get_object("reset_path_btn")
        grp_ww      = self._b.get_object("ww_models_group")
        grp_stt     = self._b.get_object("stt_models_group")
        grp_llm     = self._b.get_object("llm_models_group")
        grp_tts     = self._b.get_object("tts_models_group")
        clean_btn   = self._b.get_object("clean_unused_btn")
        stt_open    = self._b.get_object("stt_open_btn")
        llm_open    = self._b.get_object("llm_open_btn")
        tts_open    = self._b.get_object("tts_open_btn")

        if base_row:
            base_row.set_subtitle(_base())
        if choose_btn:
            choose_btn.connect("clicked", self._on_choose_models_dir)
        if reset_btn:
            reset_btn.connect("clicked", lambda _: self._settings.reset("models-dir"))

        # Connect "Open folder" buttons defined in Blueprint
        for btn, subdir in ((stt_open, "stt"), (llm_open, "llm"), (tts_open, "tts")):
            if btn:
                d = subdir
                btn.connect("clicked", lambda _b, sub=d: (
                    os.makedirs(_full(sub), exist_ok=True),
                    Gio.AppInfo.launch_default_for_uri(f"file://{_full(sub)}", None),
                ))

        # --- Helpers ---
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

        def _scan(subdir: str) -> list[tuple[str, str, int]]:
            result = []
            try:
                for e in sorted(os.scandir(_full(subdir)), key=lambda x: x.name.lower()):
                    if not e.name.startswith('.'):
                        result.append((e.name, e.path, _entry_size(e.path)))
            except FileNotFoundError:
                pass
            return result

        def _active_match(name: str, active: str) -> bool:
            if not active:
                return False
            n, a = name.lower(), active.lower()
            return n == a or a in n or n in a

        def _make_row(name: str, size: int, is_active: bool = False) -> Adw.ActionRow:
            row = Adw.ActionRow(title=name, subtitle=_fmt_size(size))
            if is_active:
                row.add_suffix(Gtk.Image(icon_name="check-plain-symbolic", valign=Gtk.Align.CENTER))
            return row

        def _swap_rows(grp: Adw.PreferencesGroup | None, rows: list) -> None:
            if not grp:
                return
            for r in getattr(grp, "_current_rows", []):
                try:
                    grp.remove(r)
                except Exception:
                    pass
            for r in rows:
                grp.add(r)
            grp._current_rows = rows  # type: ignore[attr-defined]

        # --- Refresh: repopulate Blueprint groups with current data ---
        def _refresh(*_):
            if base_row:
                base_row.set_subtitle(_base())

            active_stt = self._settings.get_string("stt-model")
            active_llm = self._settings.get_string("llm-model")
            active_tts = self._settings.get_string("tts-voice")
            engine     = self._settings.get_string("wakeword-engine") or "vosk"

            ww_rows: list = [
                Adw.ActionRow(title="Engine",
                    subtitle={"vosk": "Vosk", "openwakeword": "OpenWakeWord",
                              "sherpa-onnx": "Sherpa-ONNX"}.get(engine, engine))
            ]
            if engine == "vosk":
                ww = self._settings.get_string("wakeword") or "assistente"
                ww_rows.append(Adw.ActionRow(title="Keyword", subtitle=ww))
                ww_rows += [_make_row(n, sz, _active_match(n, active_stt)) for n, _, sz in _scan("stt")]
            elif engine == "openwakeword":
                kw = self._settings.get_string("oww-model") or "alexa"
                ww_rows.append(Adw.ActionRow(title="Keyword", subtitle=f"{kw} (bundled)"))
            elif engine == "sherpa-onnx":
                md = self._settings.get_string("sherpa-ww-model-dir") or ""
                ww_rows.append(Adw.ActionRow(title="Model directory", subtitle=md or "Not configured"))

            def _dir_rows(subdir: str, active: str) -> list:
                items = _scan(subdir)
                if items:
                    return [_make_row(n, sz, _active_match(n, active)) for n, _, sz in items]
                return [Adw.ActionRow(title="No models found", subtitle=_full(subdir))]

            _swap_rows(grp_ww,  ww_rows)
            _swap_rows(grp_stt, _dir_rows("stt", active_stt))
            _swap_rows(grp_llm, _dir_rows("llm", active_llm))
            _swap_rows(grp_tts, _dir_rows("tts", active_tts))

        _refresh()

        # --- Clean: remove model files not matched by any active setting ---
        def _on_clean(*_):
            active = {
                "stt": self._settings.get_string("stt-model"),
                "llm": self._settings.get_string("llm-model"),
                "tts": self._settings.get_string("tts-voice"),
            }
            unused = [
                path
                for sub, act in active.items()
                for _, path, _ in _scan(sub)
                if not _active_match(os.path.basename(path), act)
            ]
            if not unused:
                dlg = Adw.AlertDialog(heading="Nothing to clean",
                                      body="All models in the storage directory are in use.")
                dlg.add_response("ok", "OK")
                dlg.present(self)
                return

            names = "\n".join(f"  • {os.path.basename(p)}" for p in unused)
            dlg = Adw.AlertDialog(
                heading=f"Remove {len(unused)} unused model(s)?",
                body=f"These items will be permanently deleted from disk:\n{names}",
            )
            dlg.add_response("cancel", "Cancel")
            dlg.add_response("delete", "Delete")
            dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
            dlg.set_default_response("cancel")
            dlg.set_close_response("cancel")

            def _do_delete(_, response: str) -> None:
                if response != "delete":
                    return
                for path in unused:
                    try:
                        shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
                    except Exception as e:
                        print(f"[SettingsWindow] Cannot remove {path}: {e}")
                _refresh()

            dlg.connect("response", _do_delete)
            dlg.present(self)

        if clean_btn:
            clean_btn.connect("activated", _on_clean)

        for key in ("models-dir", "wakeword-engine", "oww-model", "sherpa-ww-model-dir",
                    "stt-model", "llm-model", "tts-voice"):
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

    def _setup_bugreport(self):
        self._bind("bugreport-enabled",   "bugreport_enable_row",    "active")
        self._bind("bugreport-endpoint",  "bugreport_endpoint_row",  "text")
        self._bind("bugreport-api-key",   "bugreport_apikey_row",    "text")
        self._bind("bugreport-product",   "bugreport_product_row",   "text")
        self._bind("bugreport-component", "bugreport_component_row", "text")
        test_btn = self._b.get_object("test_bugreport_btn")
        if test_btn:
            test_btn.connect("clicked", self._on_test_bugreport)

    def _on_test_bugreport(self, _btn):
        import threading
        import urllib.request
        import urllib.error
        import json as _json

        endpoint = self._settings.get_string("bugreport-endpoint").strip()
        api_key  = self._settings.get_string("bugreport-api-key").strip()

        if not endpoint or not api_key:
            dlg = Adw.AlertDialog(
                heading=_("Configurazione incompleta"),
                body=_("Inserisci endpoint e API key prima di testare la connessione."),
            )
            dlg.add_response("ok", _("OK"))
            dlg.present(self)
            return

        def _do_test():
            url = endpoint.rstrip("/") + "/rest/version"
            req = urllib.request.Request(url)
            req.add_header("X-BUGZILLA-API-KEY", api_key)
            try:
                with urllib.request.urlopen(req, timeout=8) as resp:
                    data = _json.loads(resp.read())
                    version = data.get("version", "sconosciuta")
                    GLib.idle_add(_show_result, True, f"Connessione OK — Bugzilla {version}")
            except urllib.error.HTTPError as e:
                GLib.idle_add(_show_result, False, f"Errore HTTP {e.code}: {e.reason}")
            except Exception as e:
                GLib.idle_add(_show_result, False, str(e))

        def _show_result(ok, msg):
            dlg = Adw.AlertDialog(
                heading=_("Connessione riuscita") if ok else _("Connessione fallita"),
                body=msg,
            )
            dlg.add_response("ok", _("OK"))
            dlg.present(self)

        threading.Thread(target=_do_test, daemon=True).start()

    def _setup_about(self):
        doc_btn = self._b.get_object("doc_btn")
        if doc_btn:
            doc_btn.connect("clicked", lambda _: Gio.AppInfo.launch_default_for_uri(
                "https://github.com/Scroker/voice-assistant", None
            ))
