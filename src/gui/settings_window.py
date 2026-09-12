"""
SettingsWindow — Finestra preferenze nativa per la GUI dell'Assistente Vocale.

Orchestra i moduli di impostazioni specializzati in `components.settings`.
"""

from __future__ import annotations

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gio', '2.0')
from gi.repository import Gtk, Adw, Gio, Gdk

Adw.init()

import os
import sys

_gui_dir = os.path.dirname(os.path.abspath(__file__))
if _gui_dir not in sys.path:
    sys.path.insert(0, _gui_dir)

try:
    from components.resources import register_resources, register_icons
    from components.settings import (
        GeneralSettings,
        AudioSettings,
        DispatchSettings,
        WakeWordSettings,
        STTSettings,
        LLMSettings,
        TTSSettings,
        MCPSettings,
        SkillsSettings,
        ModelSelectorController,
        ModelsStorageManager,
        BugReportSettings,
        AboutSettings,
        SUPPORTED_LANGUAGES,
    )
except ImportError:
    from gui.components.resources import register_resources, register_icons
    from gui.components.settings import (
        GeneralSettings,
        AudioSettings,
        DispatchSettings,
        WakeWordSettings,
        STTSettings,
        LLMSettings,
        TTSSettings,
        MCPSettings,
        SkillsSettings,
        ModelSelectorController,
        ModelsStorageManager,
        BugReportSettings,
        AboutSettings,
        SUPPORTED_LANGUAGES,
    )

_SCHEMA = "org.gnome.shell.extensions.voice-assistant"
_PREFS_UI = "/org/gnome/shell/extensions/voice-assistant/ui/prefs.ui"

_active_dialog: _SettingsDialog | None = None


def open_settings_window(parent=None, application=None) -> _SettingsDialog | None:
    """Apre la finestra/dialog delle preferenze (Adw.PreferencesDialog).
    Se parent è specificato (es. aperta dalla GUI principale),
    il dialog delle preferenze viene presentato come sheet su parent.
    Altrimenti viene presentato in modalità standalone."""
    global _active_dialog
    try:
        app = application or (parent.get_application() if parent and hasattr(parent, "get_application") else None) or Adw.Application.get_default()
        if app:
            try:
                if not app.get_is_registered():
                    app.register(None)
            except Exception:
                pass

        if _active_dialog is not None:
            if parent:
                _active_dialog.set_transient_for(parent)
                _active_dialog.set_modal(True)
                root = _active_dialog.get_root()
                if root and hasattr(root, "set_transient_for"):
                    root.set_transient_for(parent)
            else:
                _active_dialog.set_transient_for(None)
                _active_dialog.set_modal(False)
                root = _active_dialog.get_root()
                if root and hasattr(root, "set_transient_for"):
                    root.set_transient_for(None)
            _active_dialog.present(parent)
            return _active_dialog

        dialog = _SettingsDialog(transient_for=parent)
        if app:
            dialog.set_application(app)
        if parent:
            dialog.set_transient_for(parent)
            dialog.set_modal(True)
        else:
            dialog.set_transient_for(None)
            dialog.set_modal(False)

        def _on_closed(_d):
            global _active_dialog
            if _active_dialog is _d:
                _active_dialog = None
            if parent is None and app:
                GLib.idle_add(lambda: app.quit() if len(app.get_windows()) <= 1 else None)

        dialog.connect("closed", _on_closed)
        _active_dialog = dialog

        dialog.present(parent)

        if parent is None and app:
            root = dialog.get_root()
            if root and isinstance(root, Gtk.Window):
                root._settings_dialog = dialog
                try:
                    root.set_icon_name("vocal-assistant-icon")
                    app.add_window(root)
                except Exception:
                    pass

        return dialog
    except Exception as e:
        print(f"[SettingsWindow] Impossibile aprire dialog impostazioni: {e}")
        return None


class _SettingsDialogMeta(type(Adw.PreferencesDialog)):
    """Metaclasse per garantire compatibilità trasparente su isinstance(win, _SettingsWindow)."""
    def __instancecheck__(cls, instance):
        if super().__instancecheck__(instance):
            return True
        if getattr(instance, "_settings_dialog", None) is not None:
            return True
        return False


class _SettingsDialog(Adw.PreferencesDialog, metaclass=_SettingsDialogMeta):
    """Dialog delle preferenze nativo Libadwaita (Adw.PreferencesDialog)."""

    def __new__(cls, *args, **kwargs):
        Adw.init()
        register_resources()
        register_icons(Gdk.Display.get_default())

        builder = Gtk.Builder()
        ui_path = os.path.normpath(os.path.join(_gui_dir, "..", "..", "data", "ui", "prefs.ui"))
        if os.path.exists(ui_path):
            builder.add_from_file(ui_path)
        else:
            try:
                builder.add_from_resource(_PREFS_UI)
            except Exception:
                raise

        dialog = builder.get_object("preferences_dialog") or builder.get_object("preferences_window")
        if not dialog:
            raise RuntimeError("preferences_dialog non trovato in prefs.ui")
        dialog.__class__ = cls
        dialog._b = builder
        return dialog

    def __init__(self, transient_for=None, modal: bool | None = None):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True

        self.set_title("Preferenze — Assistente Vocale")
        self.set_content_width(860)
        self.set_content_height(600)
        self._transient_parent = transient_for
        self._is_modal = modal if modal is not None else bool(transient_for)
        self._application = None

        self._settings = None
        try:
            source = Gio.SettingsSchemaSource.get_default()
            if source and source.lookup(_SCHEMA, True):
                self._settings = Gio.Settings.new(_SCHEMA)
            else:
                candidates = [
                    os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "schemas")),
                    os.path.expanduser("~/.local/share/gnome-shell/extensions/voice-assistant@scroker.github.io/schemas"),
                ]
                for c in candidates:
                    if os.path.exists(os.path.join(c, "gschemas.compiled")):
                        schema_src = Gio.SettingsSchemaSource.new_from_directory(c, source, False)
                        schema = schema_src.lookup(_SCHEMA, True)
                        if schema:
                            self._settings = Gio.Settings.new_full(schema, None, None)
                            break
        except Exception:
            self._settings = None

        self._setup_components()

    def set_transient_for(self, parent) -> None:
        self._transient_parent = parent
        self._is_modal = bool(parent)
        root = self.get_root()
        if root and hasattr(root, "set_transient_for"):
            try:
                root.set_transient_for(parent)
            except Exception:
                pass

    def get_transient_for(self):
        return getattr(self, "_transient_parent", None)

    def set_modal(self, modal: bool) -> None:
        self._is_modal = bool(modal)

    def get_modal(self) -> bool:
        return getattr(self, "_is_modal", False)

    def set_default_size(self, width: int, height: int) -> None:
        self.set_content_width(width)
        self.set_content_height(height)

    def set_application(self, app) -> None:
        self._application = app
        root = self.get_root()
        if root and hasattr(root, "set_application"):
            try:
                root.set_application(app)
            except Exception:
                pass

    def get_application(self):
        return getattr(self, "_application", None)

    def present(self, parent=None) -> None:
        target = parent if parent is not None else getattr(self, "_transient_parent", None)
        if target is not None:
            self._transient_parent = target
            super().present(target)
        else:
            super().present(None)

    def destroy(self) -> None:
        try:
            self.force_close()
        except Exception:
            self.close()

    def _setup_components(self) -> None:
        """Inizializza i componenti modulari per ciascuna sezione delle preferenze."""
        self.model_selector = ModelSelectorController(self._b, self._settings, parent_window=self)
        self.general_settings = GeneralSettings(self._b, self._settings, parent_window=self)
        self.audio_settings = AudioSettings(self._b, self._settings, parent_window=self)
        self.dispatch_settings = DispatchSettings(self._b, self._settings, parent_window=self)
        self.wakeword_settings = WakeWordSettings(
            self._b,
            self._settings,
            on_open_model_selector=self.model_selector.open_selector,
        )
        self.stt_settings = STTSettings(
            self._b,
            self._settings,
            on_open_model_selector=self.model_selector.open_selector,
        )
        self.llm_settings = LLMSettings(
            self._b,
            self._settings,
            on_open_model_selector=self.model_selector.open_selector,
            parent_window=self,
        )
        self.tts_settings = TTSSettings(
            self._b,
            self._settings,
            on_open_model_selector=self.model_selector.open_selector,
            parent_window=self,
        )
        self.mcp_settings = MCPSettings(self._b, self._settings)
        self.skills_settings = SkillsSettings(self._b, self._settings, parent_window=self)
        self.models_manager = ModelsStorageManager(self._b, self._settings, parent_window=self)
        self.bugreport_settings = BugReportSettings(self._b, self._settings, parent_window=self)
        self.about_settings = AboutSettings(self._b)


_SettingsWindow = _SettingsDialog
