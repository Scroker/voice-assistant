import os
import sys

try:
    from core.cloud_config import get_cloud_config
except ImportError:
    _d = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "daemon"))
    if _d not in sys.path:
        sys.path.insert(0, _d)
    from core.cloud_config import get_cloud_config

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gio', '2.0')
from gi.repository import Gtk, Gio

from .base import bind_setting, bind_radio_group


class TTSSettings:
    """Configura la pagina Text-to-Speech (TTS)."""

    def __init__(self, builder: Gtk.Builder, settings: Gio.Settings | None, on_open_model_selector=None, parent_window: Gtk.Window | None = None):
        self.builder = builder
        self.settings = settings
        self.cloud_config = get_cloud_config(settings=self.settings)
        self.on_open_model_selector = on_open_model_selector
        self.parent_window = parent_window
        self._setup()

    def _update_visibility(self, is_cloud: bool) -> None:
        local_eng = self.builder.get_object("tts_local_engine_group")
        local_grp = self.builder.get_object("tts_local_group")
        cloud_eng = self.builder.get_object("tts_cloud_engine_group")
        cloud_cfg = self.builder.get_object("tts_cloud_config_group")

        piper_radio = self.builder.get_object("tts_engine_piper_radio")
        is_piper = bool(piper_radio and piper_radio.get_active())

        if local_eng:
            local_eng.set_visible(not is_cloud)
        if local_grp:
            local_grp.set_visible((not is_cloud) and is_piper)
        if cloud_eng:
            cloud_eng.set_visible(is_cloud)
        if cloud_cfg:
            cloud_cfg.set_visible(is_cloud)

    def _update_current_voice_label(self) -> None:
        row = self.builder.get_object("current_tts_model_row")
        if not row:
            return
        voice = (self.settings.get_string("tts-voice") if self.settings else "it_IT-paola-medium") or "it_IT-paola-medium"
        row.set_subtitle(f"Piper • {voice}")

    def _open_selector(self, *_) -> None:
        if self.on_open_model_selector:
            prov = "piper"
            if self.settings:
                prov = self.settings.get_string("tts-provider") or self.settings.get_string("tts-engine") or "piper"
            self.on_open_model_selector("tts", prov)

    def _setup(self) -> None:
        self._updating = False
        bind_setting(self.settings, "tts-enabled", self.builder, "tts_enable_row", "active")

        local_mode_radio = self.builder.get_object("tts_mode_local_radio")
        cloud_mode_radio = self.builder.get_object("tts_mode_cloud_radio")

        piper_radio = self.builder.get_object("tts_engine_piper_radio")
        espeak_radio = self.builder.get_object("tts_engine_espeak_radio")
        system_radio = self.builder.get_object("tts_engine_system_radio")
        openai_radio = self.builder.get_object("tts_engine_openai_radio")

        current_engine = "piper"
        if self.settings:
            prov = self.settings.get_string("tts-provider")
            eng = self.settings.get_string("tts-engine")
            current_engine = prov if prov else (eng if eng else "piper")
        if not current_engine:
            current_engine = "piper"
        is_cloud = (current_engine == "openai")

        def _set_engine(engine_val: str) -> None:
            if not self.settings or self._updating:
                return
            self._updating = True
            try:
                self.settings.set_string("tts-provider", engine_val)
                self.settings.set_string("tts-engine", engine_val)
            except Exception:
                pass
            finally:
                self._updating = False

        if local_mode_radio and cloud_mode_radio:
            def _on_mode_local(r, _p):
                if r.get_active() and not self._updating:
                    chosen = "espeak" if (espeak_radio and espeak_radio.get_active()) else ("system" if (system_radio and system_radio.get_active()) else "piper")
                    if chosen == "piper" and piper_radio:
                        piper_radio.set_active(True)
                    elif chosen == "espeak" and espeak_radio:
                        espeak_radio.set_active(True)
                    elif chosen == "system" and system_radio:
                        system_radio.set_active(True)
                    _set_engine(chosen)
                    self._update_visibility(False)

            def _on_mode_cloud(r, _p):
                if r.get_active() and not self._updating:
                    if openai_radio:
                        openai_radio.set_active(True)
                    _set_engine("openai")
                    self._update_visibility(True)

            local_mode_radio.connect("notify::active", _on_mode_local)
            cloud_mode_radio.connect("notify::active", _on_mode_cloud)

        def _make_sub_handler(engine_val):
            def _handler(r, _p):
                if r.get_active() and not self._updating:
                    if engine_val == "openai":
                        if cloud_mode_radio and not cloud_mode_radio.get_active():
                            cloud_mode_radio.set_active(True)
                    else:
                        if local_mode_radio and not local_mode_radio.get_active():
                            local_mode_radio.set_active(True)
                    _set_engine(engine_val)
                    self._update_visibility(engine_val == "openai")
            return _handler

        if piper_radio:
            piper_radio.connect("notify::active", _make_sub_handler("piper"))
        if espeak_radio:
            espeak_radio.connect("notify::active", _make_sub_handler("espeak"))
        if system_radio:
            system_radio.connect("notify::active", _make_sub_handler("system"))
        if openai_radio:
            # Gruppo con dummy button per garantire lo stile grafico circolare di radio button in GTK 4
            dummy_cloud_tts = Gtk.CheckButton()
            openai_radio.set_group(dummy_cloud_tts)

            def _on_openai_radio(r, _p):
                if self._updating:
                    return
                if not r.get_active():
                    # Evita che l'utente deselezioni l'unica opzione cloud disponibile
                    if cloud_mode_radio and cloud_mode_radio.get_active():
                        self._updating = True
                        r.set_active(True)
                        self._updating = False
                    return
                if cloud_mode_radio and not cloud_mode_radio.get_active():
                    cloud_mode_radio.set_active(True)
                _set_engine("openai")
                self._update_visibility(True)

            openai_radio.connect("notify::active", _on_openai_radio)

        # Inizializza stato radio
        self._updating = True
        try:
            if is_cloud:
                if cloud_mode_radio:
                    cloud_mode_radio.set_active(True)
                if openai_radio:
                    openai_radio.set_active(True)
            else:
                if local_mode_radio:
                    local_mode_radio.set_active(True)
                if current_engine == "espeak" and espeak_radio:
                    espeak_radio.set_active(True)
                elif current_engine == "system" and system_radio:
                    system_radio.set_active(True)
                elif piper_radio:
                    piper_radio.set_active(True)
        finally:
            self._updating = False

        self._update_visibility(is_cloud)

        # Configurazione Cloud TTS (API Key, Endpoint, Model, Voice)
        api_key_row = self.builder.get_object("tts_cloud_api_key_row")
        endpoint_row = self.builder.get_object("tts_cloud_endpoint_row")
        model_row = self.builder.get_object("tts_cloud_model_row")
        voice_row = self.builder.get_object("tts_cloud_voice_row")

        openai_tts_cfg = self.cloud_config.get_provider_config("tts", "openai")
        init_api_key = self.cloud_config.get_api_key("tts", "openai")
        init_endpoint = openai_tts_cfg.get("endpoint") or "https://api.openai.com/v1/audio/speech"
        init_model = openai_tts_cfg.get("model") or "tts-1"
        init_voice = openai_tts_cfg.get("voice") or "alloy"

        if api_key_row:
            api_key_row.set_text(init_api_key)
            def _on_tts_api_key_changed(entry, _pspec=None):
                if self._updating:
                    return
                val = entry.get_text().strip()
                self.cloud_config.set_provider_config("tts", "openai", {"api_key": val})
            api_key_row.connect("notify::text", _on_tts_api_key_changed)

        if endpoint_row:
            endpoint_row.set_text(init_endpoint)
            def _on_tts_endpoint_changed(entry, _pspec=None):
                if self._updating:
                    return
                val = entry.get_text().strip()
                self.cloud_config.set_provider_config("tts", "openai", {"endpoint": val})
            endpoint_row.connect("notify::text", _on_tts_endpoint_changed)

        if model_row:
            model_row.set_text(init_model)
            def _on_tts_model_changed(entry, _pspec=None):
                if self._updating:
                    return
                val = entry.get_text().strip()
                self.cloud_config.set_provider_config("tts", "openai", {"model": val})
            model_row.connect("notify::text", _on_tts_model_changed)

        if voice_row:
            voice_row.set_text(init_voice)
            def _on_tts_voice_changed(entry, _pspec=None):
                if self._updating:
                    return
                val = entry.get_text().strip()
                self.cloud_config.set_provider_config("tts", "openai", {"voice": val})
            voice_row.connect("notify::text", _on_tts_voice_changed)

        # Configurazione selettore modello vocale
        self._update_current_voice_label()
        current_tts_row = self.builder.get_object("current_tts_model_row")
        if self.on_open_model_selector and current_tts_row:
            current_tts_row.connect("activated", self._open_selector)

        if self.settings:
            self.settings.connect("changed::tts-voice", lambda *_: self._update_current_voice_label())

            def _on_tts_prov_changed(*_):
                if self._updating or not self.settings:
                    return
                prov = self.settings.get_string("tts-provider") or self.settings.get_string("tts-engine") or "piper"
                is_c = (prov == "openai")
                self._updating = True
                try:
                    if is_c:
                        if cloud_mode_radio:
                            cloud_mode_radio.set_active(True)
                        if openai_radio:
                            openai_radio.set_active(True)
                    else:
                        if local_mode_radio:
                            local_mode_radio.set_active(True)
                        if prov == "espeak" and espeak_radio:
                            espeak_radio.set_active(True)
                        elif prov == "system" and system_radio:
                            system_radio.set_active(True)
                        elif piper_radio:
                            piper_radio.set_active(True)
                finally:
                    self._updating = False
                self._update_visibility(is_c)

            self.settings.connect("changed::tts-provider", _on_tts_prov_changed)
            self.settings.connect("changed::tts-engine", _on_tts_prov_changed)

