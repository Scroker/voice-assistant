"""
Componente per la configurazione del Large Language Model (LLM).
"""

import os
import sys

try:
    from core.model_registry import get_model_registry
    from core.cloud_config import get_cloud_config
except ImportError:
    _d = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "daemon"))
    if _d not in sys.path:
        sys.path.insert(0, _d)
    from core.model_registry import get_model_registry
    from core.cloud_config import get_cloud_config

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gio', '2.0')
from gi.repository import Gtk, Adw, Gio, GLib

from .base import bind_setting


PROVIDER_DEFAULTS = {
    "openai": {
        "name": "OpenAI",
        "endpoint": "https://api.openai.com/v1/chat/completions",
        "default_model": "gpt-4o-mini",
    },
    "anthropic": {
        "name": "Anthropic Claude",
        "endpoint": "https://api.anthropic.com/v1/messages",
        "default_model": "claude-haiku-4-5-20251001",
    },
    "deepseek": {
        "name": "DeepSeek",
        "endpoint": "https://api.deepseek.com/v1/chat/completions",
        "default_model": "deepseek-chat",
    },
    "ollama_cloud": {
        "name": "Ollama Cloud",
        "endpoint": "https://ollama.com/v1/chat/completions",
        "default_model": "llama3.3",
    },
    "ollama": {
        "name": "Ollama (Local)",
        "endpoint": "http://localhost:11434/v1/chat/completions",
        "default_model": "llama3.2:3b",
    },
    "local": {
        "name": "Local GGUF",
        "endpoint": "",
        "default_model": "Llama-3.2-1B-Instruct-Q4_K_M.gguf",
    },
    "custom": {
        "name": "Custom HTTP",
        "endpoint": "http://localhost:8080/v1/chat/completions",
        "default_model": "",
    },
}

KNOWN_ENDPOINTS = {
    "",
    "http://localhost:11434",
    "http://localhost:11434/v1/chat/completions",
    "http://127.0.0.1:11434",
    "http://127.0.0.1:11434/v1/chat/completions",
    "https://api.openai.com",
    "https://api.openai.com/v1/chat/completions",
    "https://api.anthropic.com",
    "https://api.anthropic.com/v1/messages",
    "https://api.deepseek.com",
    "https://api.deepseek.com/v1/chat/completions",
    "https://ollama.com",
    "https://ollama.com/v1",
    "https://ollama.com/v1/chat/completions",
    "http://localhost:8080/v1/chat/completions",
}

KNOWN_DEFAULT_MODELS = {
    "",
    "Llama-3.2-1B-Instruct-Q4_K_M.gguf",
    "llama3.2:3b",
    "llama3.2:1b",
    "llama3.2",
    "gpt-4o-mini",
    "claude-haiku-4-5-20251001",
    "claude-3-5-haiku-latest",
    "deepseek-chat",
    "llama3.3",
    "qwen2.5",
}


class LLMSettings:
    """Configura la pagina Artificial Intelligence (LLM)."""

    def __init__(self, builder: Gtk.Builder, settings: Gio.Settings | None, on_open_model_selector=None, parent_window: Gtk.Window | None = None):
        self.builder = builder
        self.settings = settings
        self.cloud_config = get_cloud_config(settings=self.settings)
        self.on_open_model_selector = on_open_model_selector
        self.parent_window = parent_window
        self._updating = False
        self._setup()

    def _update_visibility(self, is_cloud: bool) -> None:
        local_eng = self.builder.get_object("llm_local_engine_group")
        local_grp = self.builder.get_object("llm_local_group")
        cloud_eng = self.builder.get_object("llm_cloud_engine_group")
        cloud_cfg = self.builder.get_object("llm_cloud_config_group")

        if local_eng:
            local_eng.set_visible(not is_cloud)
        if local_grp:
            local_grp.set_visible(not is_cloud)
        if cloud_eng:
            cloud_eng.set_visible(is_cloud)
        if cloud_cfg:
            cloud_cfg.set_visible(is_cloud)

    def _sync_default_model(self, mode: str) -> None:
        """Assicura che per la modalità corrente sia configurato un modello valido."""
        if not self.settings:
            return
        curr_model = (self.settings.get_string("llm-model") or "").strip()

        if mode == "local":
            clean_curr = curr_model.split("/")[-1].split(":")[-1]
            from .models import _DEFAULT_MODELS_DIR
            models_dir = _DEFAULT_MODELS_DIR
            if self.settings:
                models_dir = self.settings.get_string("models-dir") or models_dir
            reg = get_model_registry(models_dir=models_dir)
            installed_ggufs = reg.get_installed_models("gguf")

            # Se il modello corrente è valido e registrato, normalizzalo a clean_curr se necessario
            if clean_curr and reg.is_installed("gguf", clean_curr):
                if curr_model != clean_curr:
                    self.settings.set_string("llm-model", clean_curr)
                return

            # Se non è installato ma abbiamo modelli GGUF registrati, seleziona il primo installato
            if installed_ggufs:
                chosen = installed_ggufs[0]["id"]
                self.settings.set_string("llm-model", chosen)
            elif not curr_model or not curr_model.endswith(".gguf"):
                def_m = PROVIDER_DEFAULTS.get("local", {}).get("default_model", "Llama-3.2-1B-Instruct-Q4_K_M.gguf")
                self.settings.set_string("llm-model", def_m)
        elif mode == "ollama":
            # Per Ollama locale: non usare cloud_config, usa GSettings llm-model direttamente.
            # Se il modello corrente è valido e non è un file .gguf o cloud, mantienilo
            is_invalid = (
                not curr_model
                or curr_model.endswith(".gguf")
                or any(curr_model.startswith(p) for p in ("gpt-", "claude-", "deepseek-"))
            )
            if is_invalid:
                installed_ollama = []
                try:
                    ep = (self.settings.get_string("llm-endpoint") or "http://localhost:11434").rstrip("/")
                    if ep.endswith("/v1"):
                        ep = ep[:-3]
                    elif ep.endswith("/v1/chat/completions"):
                        ep = ep[:-20]
                    import urllib.request
                    req = urllib.request.Request(f"{ep}/api/tags", headers={"User-Agent": "VoiceAssistant/1.0"})
                    with urllib.request.urlopen(req, timeout=1.5) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                        for m in data.get("models", []):
                            tag = m.get("name", "")
                            if tag:
                                installed_ollama.append(tag)
                except Exception:
                    pass

                if installed_ollama:
                    chosen = installed_ollama[0]
                else:
                    chosen = ""
                self.settings.set_string("llm-model", chosen)
        else:
            # Per modalità cloud (openai, anthropic, deepseek, ollama_cloud, custom), sincronizza con cloud_providers.json
            saved_m = self.cloud_config.get_model("llm", mode)
            if not saved_m or saved_m.endswith(".gguf"):
                def_m = PROVIDER_DEFAULTS.get(mode, {}).get("default_model", "")
                if def_m:
                    saved_m = def_m
                    self.cloud_config.set_provider_config("llm", mode, {"model": saved_m})
            if saved_m and saved_m != curr_model:
                self.settings.set_string("llm-model", saved_m)

        self._update_current_model_label()

    def _update_current_model_label(self) -> None:
        local_row = self.builder.get_object("current_llm_model_row")
        cloud_row = self.builder.get_object("llm_cloud_model_row")
        mode = (self.settings.get_string("llm-mode") if self.settings else "local") or "local"
        model = (self.settings.get_string("llm-model") if self.settings else "") or ""

        if mode in ("openai", "anthropic", "deepseek", "ollama_cloud", "custom"):
            c_model = self.cloud_config.get_model("llm", mode)
            if c_model:
                model = c_model

        if local_row:
            if mode == "local":
                clean_name = model.split("/")[-1].split(":")[-1]
                if clean_name.endswith(".gguf"):
                    display_name = clean_name[:-5]
                else:
                    display_name = clean_name or "Nessun modello selezionato"
                local_row.set_subtitle(f"Locale GGUF • {display_name}")
            elif mode == "ollama":
                mode_display = "Ollama (Locale)"
                if not model:
                    local_row.set_subtitle(f"{mode_display} • Nessun modello installato")
                else:
                    is_installed = False
                    try:
                        ep = (self.settings.get_string("llm-endpoint") or "http://localhost:11434").rstrip("/")
                        if ep.endswith("/v1"):
                            ep = ep[:-3]
                        elif ep.endswith("/v1/chat/completions"):
                            ep = ep[:-20]
                        import urllib.request
                        req = urllib.request.Request(f"{ep}/api/tags", headers={"User-Agent": "VoiceAssistant/1.0"})
                        with urllib.request.urlopen(req, timeout=1.0) as resp:
                            tags_data = json.loads(resp.read().decode("utf-8"))
                            norm_curr = model.lower() if ":" in model else f"{model.lower()}:latest"
                            for m in tags_data.get("models", []):
                                t_name = m.get("name", "").lower()
                                norm_tag = t_name if ":" in t_name else f"{t_name}:latest"
                                if norm_curr == norm_tag:
                                    is_installed = True
                                    break
                    except Exception:
                        pass
                    if is_installed:
                        local_row.set_subtitle(f"{mode_display} • {model}")
                    else:
                        local_row.set_subtitle(f"{mode_display} • {model} (Non scaricato)")
            else:
                mode_display = PROVIDER_DEFAULTS.get(mode, {}).get("name", mode.capitalize())
                local_row.set_subtitle(f"{mode_display} • {model}")

        if cloud_row:
            cloud_row.set_subtitle(model or "Nessun modello selezionato")

    def _apply_provider(self, mode_val: str) -> None:
        """Applica il provider selezionato aggiornando llm-mode, endpoint, api-key e modello."""
        if not self.settings:
            return

        self._updating = True
        try:
            self.settings.set_string("llm-mode", mode_val)
            if "llm-provider" in self.settings.keys():
                try:
                    self.settings.set_string("llm-provider", mode_val)
                except Exception:
                    pass

            url_row = self.builder.get_object("llm_url_row")
            api_key_row = self.builder.get_object("llm_api_key_row")

            if mode_val in ("openai", "anthropic", "deepseek", "ollama_cloud", "custom"):
                prov_cfg = self.cloud_config.get_provider_config("llm", mode_val)
                ep = prov_cfg.get("endpoint") or PROVIDER_DEFAULTS.get(mode_val, {}).get("endpoint", "")
                mod = prov_cfg.get("model") or PROVIDER_DEFAULTS.get(mode_val, {}).get("default_model", "")
                key = prov_cfg.get("api_key", "")

                self.settings.set_string("llm-endpoint", ep)
                self.settings.set_string("llm-url", ep)
                self.settings.set_string("llm-model", mod)
                self.settings.set_string("llm-api-key", key)

                if url_row:
                    url_row.set_text(ep)
                if api_key_row:
                    api_key_row.set_text(key)
            else:
                cfg = PROVIDER_DEFAULTS.get(mode_val, {})
                def_ep = cfg.get("endpoint", "")
                if def_ep:
                    self.settings.set_string("llm-endpoint", def_ep)
                    self.settings.set_string("llm-url", def_ep)
                    if url_row:
                        url_row.set_text(def_ep)
                self._sync_default_model(mode_val)
        finally:
            self._updating = False

        self._update_current_model_label()

    def _setup(self) -> None:
        bind_setting(self.settings, "llm-enabled", self.builder, "llm_enable_row", "active")

        local_mode_radio = self.builder.get_object("llm_mode_local_radio")
        cloud_mode_radio = self.builder.get_object("llm_mode_cloud_radio")

        local_gguf = self.builder.get_object("llm_local_gguf_radio")
        local_ollama = self.builder.get_object("llm_local_ollama_radio")

        cloud_openai = self.builder.get_object("llm_cloud_openai_radio")
        cloud_anthropic = self.builder.get_object("llm_cloud_anthropic_radio")
        cloud_deepseek = self.builder.get_object("llm_cloud_deepseek_radio")
        cloud_ollama = self.builder.get_object("llm_cloud_ollama_radio")
        cloud_custom = self.builder.get_object("llm_cloud_custom_radio")

        current_mode = self.settings.get_string("llm-mode") if self.settings else "local"
        if not current_mode:
            current_mode = "local"
        self._sync_default_model(current_mode)
        is_cloud = current_mode in ("openai", "anthropic", "deepseek", "ollama_cloud", "custom")

        # Inizializza stato radio con guardie attive per evitare riscritture involontarie
        self._updating = True
        try:
            if local_mode_radio and cloud_mode_radio:
                if is_cloud:
                    cloud_mode_radio.set_active(True)
                else:
                    local_mode_radio.set_active(True)

            self._update_visibility(is_cloud)

            # Inizializza stato radio secondari
            if current_mode == "ollama" and local_ollama:
                local_ollama.set_active(True)
            elif local_gguf:
                local_gguf.set_active(True)

            if current_mode == "anthropic" and cloud_anthropic:
                cloud_anthropic.set_active(True)
            elif current_mode == "deepseek" and cloud_deepseek:
                cloud_deepseek.set_active(True)
            elif current_mode == "ollama_cloud" and cloud_ollama:
                cloud_ollama.set_active(True)
            elif current_mode == "custom" and cloud_custom:
                cloud_custom.set_active(True)
            elif cloud_openai:
                cloud_openai.set_active(True)
        finally:
            self._updating = False

        if local_mode_radio and cloud_mode_radio:
            def _on_mode_local(r, _p):
                if r.get_active() and not self._updating:
                    self._update_visibility(False)
                    if self.settings and self.settings.get_string("llm-mode") in ("openai", "anthropic", "deepseek", "ollama_cloud", "custom"):
                        chosen = "ollama" if (local_ollama and local_ollama.get_active()) else "local"
                        self._apply_provider(chosen)

            def _on_mode_cloud(r, _p):
                if r.get_active() and not self._updating:
                    self._update_visibility(True)
                    if self.settings and self.settings.get_string("llm-mode") in ("local", "ollama"):
                        chosen = (
                            "anthropic" if (cloud_anthropic and cloud_anthropic.get_active())
                            else ("deepseek" if (cloud_deepseek and cloud_deepseek.get_active())
                            else ("ollama_cloud" if (cloud_ollama and cloud_ollama.get_active())
                            else ("custom" if (cloud_custom and cloud_custom.get_active())
                            else "openai")))
                        )
                        self._apply_provider(chosen)

            local_mode_radio.connect("notify::active", _on_mode_local)
            cloud_mode_radio.connect("notify::active", _on_mode_cloud)

        def _make_sub_handler(mode_val):
            def _handler(r, _p):
                if r.get_active() and not self._updating and self.settings:
                    if mode_val in ("openai", "anthropic", "deepseek", "ollama_cloud", "custom"):
                        if cloud_mode_radio and not cloud_mode_radio.get_active():
                            cloud_mode_radio.set_active(True)
                    else:
                        if local_mode_radio and not local_mode_radio.get_active():
                            local_mode_radio.set_active(True)
                    self._apply_provider(mode_val)
            return _handler

        if local_gguf:
            local_gguf.connect("notify::active", _make_sub_handler("local"))
        if local_ollama:
            local_ollama.connect("notify::active", _make_sub_handler("ollama"))
        if cloud_openai:
            cloud_openai.connect("notify::active", _make_sub_handler("openai"))
        if cloud_anthropic:
            cloud_anthropic.connect("notify::active", _make_sub_handler("anthropic"))
        if cloud_deepseek:
            cloud_deepseek.connect("notify::active", _make_sub_handler("deepseek"))
        if cloud_ollama:
            cloud_ollama.connect("notify::active", _make_sub_handler("ollama_cloud"))
        if cloud_custom:
            cloud_custom.connect("notify::active", _make_sub_handler("custom"))

        # Setup API Key e Prompt di Sistema
        self._setup_api_key_row()
        bind_setting(self.settings, "llm-system-prompt", self.builder, "llm_system_prompt_row", "text")

        # Unificazione endpoint URL (sincronizza sia llm-endpoint che llm-url e cloud_config)
        self._setup_endpoint_row()

        # Etichetta modello attivo e selettore subpage
        self._update_current_model_label()
        current_llm_row = self.builder.get_object("current_llm_model_row")
        cloud_llm_row = self.builder.get_object("llm_cloud_model_row")

        def _open_llm_selector(*_):
            if self.on_open_model_selector:
                prov = "gguf"
                if self.settings:
                    mode = self.settings.get_string("llm-mode") or "local"
                    if mode == "local":
                        prov = "gguf"
                    elif mode == "ollama":
                        prov = "ollama"
                    else:
                        prov = mode
                self.on_open_model_selector("llm", prov)

        if self.on_open_model_selector:
            if current_llm_row:
                current_llm_row.connect("activated", _open_llm_selector)
            if cloud_llm_row:
                cloud_llm_row.connect("activated", _open_llm_selector)

        # Sottopagina Tools (MCP)
        mcp_row = self.builder.get_object("mcp_subpage_row")
        mcp_subpage = self.builder.get_object("mcp_subpage")
        if mcp_row and mcp_subpage:
            def _open_mcp(*_):
                win = self.parent_window or (mcp_row.get_root() if hasattr(mcp_row, "get_root") else None)
                if win and hasattr(win, "push_subpage"):
                    win.push_subpage(mcp_subpage)
            mcp_row.connect("activated", _open_mcp)

        # Sottopagina Dispatch dei Comandi
        dispatch_subpage_row = self.builder.get_object("dispatch_subpage_row")
        dispatch_subpage = self.builder.get_object("dispatch_subpage")
        if dispatch_subpage_row and dispatch_subpage:
            def _open_dispatch(*_):
                win = self.parent_window or (dispatch_subpage_row.get_root() if hasattr(dispatch_subpage_row, "get_root") else None)
                if win and hasattr(win, "push_subpage"):
                    win.push_subpage(dispatch_subpage)
            dispatch_subpage_row.connect("activated", _open_dispatch)

        # Sottoscrizione reattiva alle modifiche GSettings
        if self.settings:
            self.settings.connect("changed::llm-mode", self._on_settings_mode_changed)
            self.settings.connect("changed::llm-model", lambda *_: self._update_current_model_label())

    def _setup_api_key_row(self) -> None:
        api_row = self.builder.get_object("llm_api_key_row")
        if not api_row or not self.settings:
            return

        current_mode = (self.settings.get_string("llm-mode") or "local").strip()
        if current_mode in ("openai", "anthropic", "deepseek", "ollama_cloud", "custom"):
            initial_key = self.cloud_config.get_api_key("llm", current_mode)
        else:
            initial_key = (self.settings.get_string("llm-api-key") or "").strip()

        api_row.set_text(initial_key)

        def _on_key_changed(entry, _pspec=None):
            if self._updating:
                return
            val = entry.get_text().strip()
            self.settings.set_string("llm-api-key", val)
            mode = (self.settings.get_string("llm-mode") or "local").strip()
            if mode in ("openai", "anthropic", "deepseek", "ollama_cloud", "custom"):
                self.cloud_config.set_provider_config("llm", mode, {"api_key": val})

        api_row.connect("notify::text", _on_key_changed)

    def _setup_endpoint_row(self) -> None:
        url_row = self.builder.get_object("llm_url_row")
        if not url_row or not self.settings:
            return

        current_mode = (self.settings.get_string("llm-mode") or "local").strip()
        if current_mode in ("openai", "anthropic", "deepseek", "ollama_cloud", "custom"):
            current_ep = self.cloud_config.get_endpoint("llm", current_mode)
            if not current_ep:
                current_ep = (self.settings.get_string("llm-endpoint") or self.settings.get_string("llm-url") or "").strip()
        else:
            current_ep = (self.settings.get_string("llm-endpoint") or self.settings.get_string("llm-url") or "").strip()

        # Auto-healing: se la modalità è cloud ma l'endpoint è ancora fermo su localhost:11434 dal vecchio default gschema
        if current_mode in ("openai", "anthropic", "deepseek", "ollama_cloud") and current_ep in ("http://localhost:11434", "http://localhost:11434/v1/chat/completions", "http://127.0.0.1:11434", ""):
            new_ep = PROVIDER_DEFAULTS.get(current_mode, {}).get("endpoint", current_ep)
            self.cloud_config.set_provider_config("llm", current_mode, {"endpoint": new_ep})
            self.settings.set_string("llm-endpoint", new_ep)
            self.settings.set_string("llm-url", new_ep)
            current_ep = new_ep

        url_row.set_text(current_ep)

        def _on_url_changed(entry, _pspec=None):
            if self._updating:
                return
            val = entry.get_text().strip()
            self.settings.set_string("llm-endpoint", val)
            self.settings.set_string("llm-url", val)
            mode = (self.settings.get_string("llm-mode") or "local").strip()
            if mode in ("openai", "anthropic", "deepseek", "ollama_cloud", "custom"):
                self.cloud_config.set_provider_config("llm", mode, {"endpoint": val})

        url_row.connect("notify::text", _on_url_changed)

    def _on_settings_mode_changed(self, settings: Gio.Settings, key: str) -> None:
        mode = settings.get_string("llm-mode") or "local"
        is_cloud = mode in ("openai", "anthropic", "deepseek", "ollama_cloud", "custom")

        local_mode_radio = self.builder.get_object("llm_mode_local_radio")
        cloud_mode_radio = self.builder.get_object("llm_mode_cloud_radio")
        local_gguf = self.builder.get_object("llm_local_gguf_radio")
        local_ollama = self.builder.get_object("llm_local_ollama_radio")
        cloud_openai = self.builder.get_object("llm_cloud_openai_radio")
        cloud_anthropic = self.builder.get_object("llm_cloud_anthropic_radio")
        cloud_deepseek = self.builder.get_object("llm_cloud_deepseek_radio")
        cloud_ollama = self.builder.get_object("llm_cloud_ollama_radio")
        cloud_custom = self.builder.get_object("llm_cloud_custom_radio")

        url_row = self.builder.get_object("llm_url_row")
        api_key_row = self.builder.get_object("llm_api_key_row")

        self._updating = True
        try:
            if is_cloud:
                if cloud_mode_radio:
                    cloud_mode_radio.set_active(True)
                if mode == "anthropic" and cloud_anthropic:
                    cloud_anthropic.set_active(True)
                elif mode == "deepseek" and cloud_deepseek:
                    cloud_deepseek.set_active(True)
                elif mode == "ollama_cloud" and cloud_ollama:
                    cloud_ollama.set_active(True)
                elif mode == "custom" and cloud_custom:
                    cloud_custom.set_active(True)
                elif cloud_openai:
                    cloud_openai.set_active(True)

                if api_key_row:
                    api_key_row.set_text(self.cloud_config.get_api_key("llm", mode))
                if url_row:
                    url_row.set_text(self.cloud_config.get_endpoint("llm", mode))
            else:
                if local_mode_radio:
                    local_mode_radio.set_active(True)
                if mode == "ollama" and local_ollama:
                    local_ollama.set_active(True)
                elif local_gguf:
                    local_gguf.set_active(True)
        finally:
            self._updating = False

        self._update_visibility(is_cloud)
        self._sync_default_model(mode)
        self._update_current_model_label()


