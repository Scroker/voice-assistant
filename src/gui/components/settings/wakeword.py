"""
Componente per la configurazione del motore di Wake Word.
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gio', '2.0')
from gi.repository import Gtk, Gio

from .base import bind_setting, bind_radio_group


class WakeWordSettings:
    """Configura la pagina Wake Word e la visibilità dinamica dei widget."""

    def __init__(self, builder: Gtk.Builder, settings: Gio.Settings | None, on_open_model_selector=None):
        self.builder = builder
        self.settings = settings
        self.on_open_model_selector = on_open_model_selector
        self._setup()

    def _setup(self) -> None:
        bind_radio_group(self.settings, "wakeword-engine", self.builder, {
            "ww_engine_vosk_radio":   "vosk",
            "ww_engine_oww_radio":    "openwakeword",
            "ww_engine_sherpa_radio": "sherpa-onnx",
        })
        bind_setting(self.settings, "wakeword", self.builder, "wakeword_row", "text")
        if self.builder.get_object("sherpa_model_dir_row"):
            bind_setting(self.settings, "sherpa-ww-model-dir", self.builder, "sherpa_model_dir_row", "text")

        bind_radio_group(self.settings, "oww-model", self.builder, {
            "oww_alexa_radio":       "alexa",
            "oww_hey_jarvis_radio":  "hey_jarvis",
            "oww_hey_mycroft_radio": "hey_mycroft",
            "oww_hey_rhasspy_radio": "hey_rhasspy",
        })

        vosk_row = self.builder.get_object("current_vosk_ww_model_row")
        if vosk_row and self.on_open_model_selector:
            vosk_row.connect("activated", lambda _: self.on_open_model_selector("wakeword", "vosk"))

        sherpa_row = self.builder.get_object("current_sherpa_model_row")
        if sherpa_row and self.on_open_model_selector:
            sherpa_row.connect("activated", lambda _: self.on_open_model_selector("wakeword", "sherpa-onnx"))

        self._update_model_subtitles()
        if self.settings:
            self.settings.connect("changed::vosk-ww-model", lambda *_: self._update_model_subtitles())
            self.settings.connect("changed::sherpa-model", lambda *_: self._update_model_subtitles())

        self.apply_engine_visibility()
        for wid in ("ww_engine_vosk_radio", "ww_engine_oww_radio", "ww_engine_sherpa_radio"):
            r = self.builder.get_object(wid)
            if r:
                r.connect("notify::active", lambda *_: self.apply_engine_visibility())

    def _update_model_subtitles(self) -> None:
        if not self.settings:
            return
        try:
            vosk_m = self.settings.get_string("vosk-ww-model") or "vosk-model-small-it-0.22"
            vosk_row = self.builder.get_object("current_vosk_ww_model_row")
            if vosk_row:
                vosk_row.set_subtitle(f"Vosk • {vosk_m}")
        except Exception:
            pass

        try:
            sherpa_m = self.settings.get_string("sherpa-model") or "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01"
            sherpa_row = self.builder.get_object("current_sherpa_model_row")
            if sherpa_row:
                sherpa_row.set_subtitle(f"Sherpa • {sherpa_m}")
        except Exception:
            pass

    def apply_engine_visibility(self) -> None:
        """Aggiorna la visibilità dei campi in base al motore selezionato."""
        oww = self.builder.get_object("ww_engine_oww_radio")
        sherpa = self.builder.get_object("ww_engine_sherpa_radio")
        vosk = self.builder.get_object("ww_engine_vosk_radio")
        is_oww = bool(oww and oww.get_active())
        is_sherpa = bool(sherpa and sherpa.get_active())
        is_vosk = bool(vosk and vosk.get_active()) or (not is_oww and not is_sherpa)

        for wid, visible in [
            ("wakeword_config_group",     not is_oww),
            ("wakeword_row",              not is_oww),
            ("current_vosk_ww_model_row", is_vosk),
            ("current_sherpa_model_row",  is_sherpa),
            ("oww_keyword_group",         is_oww),
            ("sherpa_model_dir_row",      is_sherpa),
        ]:
            w = self.builder.get_object(wid)
            if w:
                w.set_visible(visible)
