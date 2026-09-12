"""
Componente per la configurazione del motore Speech-to-Text (STT).
"""

import json
import os
import sys
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gio', '2.0')
from gi.repository import Gtk, Gio

try:
    from core.cloud_config import get_cloud_config
except ImportError:
    d = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "daemon"))
    if d not in sys.path:
        sys.path.insert(0, d)
    from core.cloud_config import get_cloud_config

from .base import bind_setting, bind_radio_group


def get_default_model(provider_name: str, language: str | None = None) -> str:
    """Risolve il modello predefinito tramite daemon.providers se disponibile."""
    try:
        from providers import get_default_model as _get_def
        return _get_def(provider_name, language)
    except ImportError:
        d = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "daemon"))
        if d not in sys.path:
            sys.path.insert(0, d)
        from providers import get_default_model as _get_def
        return _get_def(provider_name, language)


class STTSettings:
    """Configura la pagina Speech Engine (STT)."""

    def __init__(self, builder: Gtk.Builder, settings: Gio.Settings | None, on_open_model_selector=None):
        self.builder = builder
        self.settings = settings
        self.cloud_config = get_cloud_config(settings=self.settings)
        self.on_open_model_selector = on_open_model_selector
        self._updating = False
        self._setup()

    def _update_visibility(self, is_cloud: bool, local_provider: str = "vosk") -> None:
        local_eng = self.builder.get_object("stt_local_engine_group")
        local_grp = self.builder.get_object("stt_local_group")
        hw_grp = self.builder.get_object("whisper_hardware_group")
        cloud_grp = self.builder.get_object("stt_cloud_group")
        cloud_cfg_grp = self.builder.get_object("stt_cloud_config_group")

        if local_eng:
            local_eng.set_visible(not is_cloud)
        if local_grp:
            local_grp.set_visible(not is_cloud)
        if hw_grp:
            hw_grp.set_visible(not is_cloud and local_provider == "whisper")
        if cloud_grp:
            cloud_grp.set_visible(is_cloud)
        if cloud_cfg_grp:
            cloud_cfg_grp.set_visible(is_cloud)

    def _update_current_model_label(self) -> None:
        row = self.builder.get_object("current_model_row")
        if not row:
            return
        provider = (self.settings.get_string("stt-provider") if self.settings else "vosk") or "vosk"
        model = (self.settings.get_string("stt-model") if self.settings else "vosk-model-small-it-0.22") or "vosk-model-small-it-0.22"

        display_names = {
            "vosk": "Vosk",
            "whisper": "Whisper",
            "openai_cloud": "OpenAI Cloud STT",
            "groq_cloud": "Groq Cloud STT",
        }
        provider_display = display_names.get(provider, provider.capitalize())
        row.set_subtitle(f"{provider_display} • {model}")

    def _setup(self) -> None:
        local_mode_radio = self.builder.get_object("stt_mode_local_radio")
        cloud_mode_radio = self.builder.get_object("stt_mode_cloud_radio")

        vosk_radio = self.builder.get_object("stt_engine_vosk_radio")
        whisper_radio = self.builder.get_object("stt_engine_whisper_radio")

        openai_radio = self.builder.get_object("stt_cloud_openai_radio")
        groq_radio = self.builder.get_object("stt_cloud_groq_radio")

        provider = (self.settings.get_string("stt-provider") if self.settings else "vosk") or "vosk"
        is_cloud = provider in ("openai_cloud", "groq_cloud")

        # 1. Inizializzazione controlli modalità Macro (Locale / Cloud)
        if local_mode_radio and cloud_mode_radio:
            if is_cloud:
                cloud_mode_radio.set_active(True)
            else:
                local_mode_radio.set_active(True)

            def _on_mode_local(r, _p):
                if r.get_active() and not self._updating:
                    chosen = "whisper" if (whisper_radio and whisper_radio.get_active()) else "vosk"
                    self._update_visibility(False, chosen)
                    if self.settings:
                        curr = self.settings.get_string("stt-provider")
                        if curr in ("openai_cloud", "groq_cloud"):
                            self.settings.set_string("stt-provider", chosen)
                            self._sync_default_model(chosen)

            def _on_mode_cloud(r, _p):
                if r.get_active() and not self._updating:
                    self._update_visibility(True)
                    if self.settings:
                        curr = self.settings.get_string("stt-provider")
                        if curr not in ("openai_cloud", "groq_cloud"):
                            chosen = "groq_cloud" if (groq_radio and groq_radio.get_active()) else "openai_cloud"
                            self.settings.set_string("stt-provider", chosen)
                            self._sync_default_model(chosen)

            local_mode_radio.connect("notify::active", _on_mode_local)
            cloud_mode_radio.connect("notify::active", _on_mode_cloud)

        # 2. Inizializzazione motori locali (Vosk / Whisper)
        if vosk_radio and whisper_radio:
            if provider == "whisper":
                whisper_radio.set_active(True)
            else:
                vosk_radio.set_active(True)

            def _on_vosk(r, _p):
                if r.get_active() and not self._updating:
                    if self.settings:
                        self.settings.set_string("stt-provider", "vosk")
                        self._sync_default_model("vosk")
                    self._update_visibility(False, "vosk")

            def _on_whisper(r, _p):
                if r.get_active() and not self._updating:
                    if self.settings:
                        self.settings.set_string("stt-provider", "whisper")
                        self._sync_default_model("whisper")
                    self._update_visibility(False, "whisper")

            vosk_radio.connect("notify::active", _on_vosk)
            whisper_radio.connect("notify::active", _on_whisper)

        local_p = "whisper" if (whisper_radio and whisper_radio.get_active()) else "vosk"
        self._update_visibility(is_cloud, local_p)

        # 3. Inizializzazione provider Cloud (OpenAI / Groq)
        if openai_radio and groq_radio and self.settings:
            if provider == "groq_cloud":
                groq_radio.set_active(True)
            elif provider == "openai_cloud":
                openai_radio.set_active(True)

            def _on_openai(r, _p):
                if r.get_active() and not self._updating and self.settings:
                    self.settings.set_string("stt-provider", "openai_cloud")
                    self._sync_default_model("openai_cloud")

            def _on_groq(r, _p):
                if r.get_active() and not self._updating and self.settings:
                    self.settings.set_string("stt-provider", "groq_cloud")
                    self._sync_default_model("groq_cloud")

            openai_radio.connect("notify::active", _on_openai)
            groq_radio.connect("notify::active", _on_groq)

        # 4. Hardware acceleration Whisper
        bind_radio_group(self.settings, "stt-hardware", self.builder, {
            "hw_cpu_radio":  "cpu",
            "hw_cuda_radio": "cuda",
        })

        # 5. Cloud API Key (serializzata in JSON dentro stt-extra)
        self._setup_api_key()
        bind_setting(self.settings, "stt-model", self.builder, "stt_cloud_model_row", "text")

        # 6. Etichetta del modello attivo e selettore subpage
        self._update_current_model_label()
        current_row = self.builder.get_object("current_model_row")

        def _open_stt_selector(*_):
            if self.on_open_model_selector:
                prov = "vosk"
                if self.settings:
                    prov = self.settings.get_string("stt-provider") or "vosk"
                self.on_open_model_selector("stt", prov)

        if self.on_open_model_selector and current_row:
            current_row.connect("activated", _open_stt_selector)

        # 7. Sottoscrizione reattiva alle modifiche GSettings
        if self.settings:
            self.settings.connect("changed::stt-provider", self._on_settings_provider_changed)
            self.settings.connect("changed::stt-model", lambda *_: self._update_current_model_label())

    def _sync_default_model(self, provider: str) -> None:
        if not self.settings:
            return
        curr_model = self.settings.get_string("stt-model") or ""
        raw_l = self.settings.get_string("language") or ""
        lang = raw_l.strip() if raw_l and raw_l.strip() else None

        if provider == "vosk" and not curr_model.startswith("vosk"):
            self.settings.set_string("stt-model", get_default_model("vosk", lang))
        elif provider == "whisper" and not curr_model.startswith("whisper") and curr_model not in ("tiny", "base", "small", "medium", "large-v3"):
            self.settings.set_string("stt-model", get_default_model("whisper", lang))
        elif provider in ("openai_cloud", "groq_cloud"):
            saved_m = self.cloud_config.get_model("stt", provider)
            if not saved_m:
                saved_m = get_default_model(provider)
                self.cloud_config.set_provider_config("stt", provider, {"model": saved_m})
            self.settings.set_string("stt-model", saved_m)

    def _setup_api_key(self) -> None:
        api_row = self.builder.get_object("stt_cloud_api_key_row")
        model_row = self.builder.get_object("stt_cloud_model_row")
        if not self.settings:
            return

        provider = (self.settings.get_string("stt-provider") or "vosk").lower()
        if provider not in ("openai_cloud", "groq_cloud"):
            provider = "openai_cloud"

        if api_row:
            cur_key = self.cloud_config.get_api_key("stt", provider)
            api_row.set_text(cur_key)

            def _on_key_changed(entry, _pspec=None):
                if self._updating:
                    return
                new_key = entry.get_text().strip()
                cur_prov = (self.settings.get_string("stt-provider") or "openai_cloud").lower()
                if cur_prov in ("openai_cloud", "groq_cloud"):
                    self.cloud_config.set_provider_config("stt", cur_prov, {"api_key": new_key})
                raw = self.settings.get_string("stt-extra") or ""
                data = {}
                if raw.strip().startswith("{"):
                    try:
                        data = json.loads(raw)
                    except Exception:
                        data = {}
                data["api_key"] = new_key
                self.settings.set_string("stt-extra", json.dumps(data))

            api_row.connect("notify::text", _on_key_changed)

        if model_row:
            cur_mod = self.cloud_config.get_model("stt", provider) or (self.settings.get_string("stt-model") or "")
            model_row.set_text(cur_mod)

            def _on_model_changed(entry, _pspec=None):
                if self._updating:
                    return
                new_model = entry.get_text().strip()
                cur_prov = (self.settings.get_string("stt-provider") or "openai_cloud").lower()
                if cur_prov in ("openai_cloud", "groq_cloud"):
                    self.cloud_config.set_provider_config("stt", cur_prov, {"model": new_model})
                self.settings.set_string("stt-model", new_model)

            model_row.connect("notify::text", _on_model_changed)

    def _on_settings_provider_changed(self, settings: Gio.Settings, key: str) -> None:
        provider = settings.get_string("stt-provider") or "vosk"
        is_cloud = provider in ("openai_cloud", "groq_cloud")

        local_mode_radio = self.builder.get_object("stt_mode_local_radio")
        cloud_mode_radio = self.builder.get_object("stt_mode_cloud_radio")
        vosk_radio = self.builder.get_object("stt_engine_vosk_radio")
        whisper_radio = self.builder.get_object("stt_engine_whisper_radio")
        openai_radio = self.builder.get_object("stt_cloud_openai_radio")
        groq_radio = self.builder.get_object("stt_cloud_groq_radio")

        self._updating = True
        try:
            if is_cloud:
                if cloud_mode_radio:
                    cloud_mode_radio.set_active(True)
                if provider == "groq_cloud" and groq_radio:
                    groq_radio.set_active(True)
                elif openai_radio:
                    openai_radio.set_active(True)

                api_row = self.builder.get_object("stt_cloud_api_key_row")
                model_row = self.builder.get_object("stt_cloud_model_row")
                if api_row:
                    api_row.set_text(self.cloud_config.get_api_key("stt", provider))
                if model_row:
                    c_mod = self.cloud_config.get_model("stt", provider) or get_default_model(provider)
                    model_row.set_text(c_mod)
            else:
                if local_mode_radio:
                    local_mode_radio.set_active(True)
                if provider == "whisper" and whisper_radio:
                    whisper_radio.set_active(True)
                elif vosk_radio:
                    vosk_radio.set_active(True)
        finally:
            self._updating = False

        self._update_visibility(is_cloud, provider)
        self._update_current_model_label()

