"""
Componente per la configurazione dei filtri audio applicati al segnale del microfono.
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gio', '2.0')
from gi.repository import Gtk, Adw, Gio

from .base import bind_setting, has_setting_key

# widget_id -> chiave GSettings, per gli interruttori di attivazione di ogni stadio.
_SWITCH_BINDINGS = {
    "audio_aec_row": "audio-aec-enabled",
    "audio_highpass_row": "audio-highpass-enabled",
    "audio_agc_row": "audio-agc-enabled",
    "audio_gate_row": "audio-noise-gate-enabled",
}

# widget_id -> chiave GSettings, per i parametri numerici di ogni stadio.
_SPIN_BINDINGS = {
    "audio_highpass_cutoff_row": "audio-highpass-cutoff",
    "audio_agc_target_row": "audio-agc-target-rms",
    "audio_agc_max_gain_row": "audio-agc-max-gain",
    "audio_gate_threshold_row": "audio-noise-gate-threshold",
    "audio_gate_attenuation_row": "audio-noise-gate-attenuation",
}

# Interruttore di stadio -> parametri che disabilita quando è spento.
_STAGE_DEPENDENCIES = {
    "audio-highpass-enabled": ("audio_highpass_cutoff_row",),
    "audio-agc-enabled": ("audio_agc_target_row", "audio_agc_max_gain_row"),
    "audio-noise-gate-enabled": ("audio_gate_threshold_row", "audio_gate_attenuation_row"),
}

_RESET_KEYS = tuple(_SWITCH_BINDINGS.values()) + tuple(_SPIN_BINDINGS.values())


class AudioSettings:
    """Configura la sottopagina Filtri Audio."""

    def __init__(self, builder: Gtk.Builder, settings: Gio.Settings | None, parent_window: Gtk.Window | None = None):
        self.builder = builder
        self.settings = settings
        self.parent_window = parent_window
        self._setup()

    def _setup(self) -> None:
        for widget_id, key in _SWITCH_BINDINGS.items():
            bind_setting(self.settings, key, self.builder, widget_id, "active")

        for widget_id, key in _SPIN_BINDINGS.items():
            bind_setting(self.settings, key, self.builder, widget_id, "value")

        for key in _STAGE_DEPENDENCIES:
            if not has_setting_key(self.settings, key):
                continue
            self._sync_stage_sensitivity(key)
            self.settings.connect(f"changed::{key}", lambda _s, _k, sk=key: self._sync_stage_sensitivity(sk))

        reset_btn = self.builder.get_object("audio_reset_btn")
        if reset_btn:
            sig = "activated" if isinstance(reset_btn, Adw.ButtonRow) else "clicked"
            reset_btn.connect(sig, self._on_reset)

    def _sync_stage_sensitivity(self, key: str) -> None:
        """Rende insensibili i parametri di uno stadio quando lo stadio è disattivato."""
        if not self.settings:
            return
        enabled = self.settings.get_boolean(key)
        for widget_id in _STAGE_DEPENDENCIES[key]:
            widget = self.builder.get_object(widget_id)
            if widget:
                widget.set_sensitive(enabled)

    def _on_reset(self, _btn) -> None:
        if not self.settings:
            return

        dlg = Adw.AlertDialog(
            heading="Ripristinare i valori predefiniti?",
            body="Tutti i filtri audio verranno riattivati e i loro parametri riportati ai valori di fabbrica.",
        )
        dlg.add_response("cancel", "Annulla")
        dlg.add_response("reset", "Ripristina")
        dlg.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")
        dlg.set_close_response("cancel")

        def _do_reset(_dialog, response: str) -> None:
            if response != "reset":
                return
            for key in _RESET_KEYS:
                if has_setting_key(self.settings, key):
                    self.settings.reset(key)

        dlg.connect("response", _do_reset)
        dlg.present(self.parent_window)
