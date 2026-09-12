"""
Gestore centralizzato delle configurazioni dei provider Cloud (LLM, STT, TTS).
Archivia chiavi API, endpoint e modelli personalizzati per ciascun provider in
~/.config/voice-assistant/cloud_providers.json con permessi ristretti (0600).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

_log = logging.getLogger("VoiceAssistant.CloudConfig")

DEFAULT_CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))) / "voice-assistant"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "cloud_providers.json"

DEFAULT_CLOUD_PROVIDERS: Dict[str, Any] = {
    "version": 1,
    "llm": {
        "openai": {
            "api_key": "",
            "endpoint": "https://api.openai.com/v1/chat/completions",
            "model": "gpt-4o-mini",
        },
        "anthropic": {
            "api_key": "",
            "endpoint": "https://api.anthropic.com/v1/messages",
            "model": "claude-haiku-4-5-20251001",
        },
        "deepseek": {
            "api_key": "",
            "endpoint": "https://api.deepseek.com/v1/chat/completions",
            "model": "deepseek-chat",
        },
        "ollama_cloud": {
            "api_key": "",
            "endpoint": "https://ollama.com/v1/chat/completions",
            "model": "llama3.3",
        },
        "custom": {
            "api_key": "",
            "endpoint": "http://localhost:8080/v1/chat/completions",
            "model": "",
        },
    },
    "stt": {
        "openai_cloud": {
            "api_key": "",
            "endpoint": "https://api.openai.com/v1/audio/transcriptions",
            "model": "whisper-1",
        },
        "groq_cloud": {
            "api_key": "",
            "endpoint": "https://api.groq.com/openai/v1/audio/transcriptions",
            "model": "whisper-large-v3",
        },
    },
    "tts": {
        "openai": {
            "api_key": "",
            "endpoint": "https://api.openai.com/v1/audio/speech",
            "model": "tts-1",
            "voice": "alloy",
        },
    },
}


class CloudConfigManager:
    """Carica, aggiorna e persiste le configurazioni dei provider cloud."""

    def __init__(self, config_path: Optional[Path | str] = None, settings=None):
        if config_path:
            self.config_path = Path(config_path)
        else:
            custom_dir = os.environ.get("VOICE_ASSISTANT_CONFIG_DIR")
            if custom_dir:
                self.config_path = Path(custom_dir) / "cloud_providers.json"
            else:
                self.config_path = DEFAULT_CONFIG_PATH

        self.settings = settings
        self._cache: Optional[Dict[str, Any]] = None
        self._last_mtime: Optional[float] = None

    def load(self, force_reload: bool = False) -> Dict[str, Any]:
        """Carica la configurazione da disco. Se non esiste, effettua la migrazione iniziale."""
        current_mtime: Optional[float] = None
        if self.config_path.exists():
            try:
                current_mtime = self.config_path.stat().st_mtime
            except OSError:
                pass

        if not force_reload and self._cache is not None:
            if current_mtime is not None and self._last_mtime == current_mtime:
                return self._cache

        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and "llm" in data:
                        merged = self._merge_with_defaults(data)
                        self._cache = merged
                        self._last_mtime = current_mtime
                        return merged
            except Exception as e:
                _log.warning("Errore lettura cloud_providers.json: %s. Rigenerazione.", e)

        # Migrazione da GSettings o inizializzazione default
        data = self._migrate_from_gsettings()
        self.save(data)
        self._cache = data
        return data

    def save(self, data: Dict[str, Any]) -> None:
        """Salva atomicamente i dati su disco con permessi 0600."""
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            temp_fd, temp_name = tempfile.mkstemp(dir=str(self.config_path.parent), prefix="cloud_prov_", suffix=".tmp")
            with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, str(self.config_path))
            self._cache = data
            try:
                self._last_mtime = self.config_path.stat().st_mtime
            except OSError:
                self._last_mtime = None
        except Exception as e:
            _log.error("Impossibile salvare %s: %s", self.config_path, e)

    def get_provider_config(self, service: str, provider: str) -> Dict[str, Any]:
        """Restituisce la configurazione per uno specifico servizio e provider."""
        data = self.load()
        svc_data = data.get(service.lower(), {})
        prov_data = svc_data.get(provider.lower())
        if prov_data is not None:
            return dict(prov_data)

        # Fallback sui defaults
        def_svc = DEFAULT_CLOUD_PROVIDERS.get(service.lower(), {})
        return dict(def_svc.get(provider.lower(), {}))

    def set_provider_config(self, service: str, provider: str, updates: Dict[str, Any]) -> None:
        """Aggiorna i parametri per uno specifico servizio e provider."""
        data = self.load()
        s_key = service.lower()
        p_key = provider.lower()
        if s_key not in data:
            data[s_key] = {}
        if p_key not in data[s_key]:
            data[s_key][p_key] = dict(DEFAULT_CLOUD_PROVIDERS.get(s_key, {}).get(p_key, {}))

        data[s_key][p_key].update(updates)
        self.save(data)

    def get_api_key(self, service: str, provider: str) -> str:
        """Recupera la chiave API con fallback sensato (es. TTS OpenAI -> LLM OpenAI)."""
        cfg = self.get_provider_config(service, provider)
        key = str(cfg.get("api_key", "")).strip()
        if not key and service.lower() == "tts" and provider.lower() == "openai":
            # Fallback sulla chiave LLM di OpenAI
            llm_cfg = self.get_provider_config("llm", "openai")
            key = str(llm_cfg.get("api_key", "")).strip()
        return key

    def get_endpoint(self, service: str, provider: str) -> str:
        """Recupera l'endpoint configurato per il provider."""
        cfg = self.get_provider_config(service, provider)
        return str(cfg.get("endpoint", "")).strip()

    def get_model(self, service: str, provider: str) -> str:
        """Recupera il modello configurato per il provider."""
        cfg = self.get_provider_config(service, provider)
        return str(cfg.get("model", "")).strip()

    def _merge_with_defaults(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Unisca i dati salvati con lo schema di default per eventuali nuovi provider/campi."""
        import copy
        result = copy.deepcopy(DEFAULT_CLOUD_PROVIDERS)
        for svc in ("llm", "stt", "tts"):
            if svc not in result:
                result[svc] = {}
            user_svc = data.get(svc, {})
            for prov, def_fields in DEFAULT_CLOUD_PROVIDERS.get(svc, {}).items():
                if prov not in result[svc]:
                    result[svc][prov] = copy.deepcopy(def_fields)
                if prov in user_svc:
                    result[svc][prov].update(user_svc[prov])
            # Mantieni eventuali custom providers aggiuntivi
            for prov, u_fields in user_svc.items():
                if prov not in result[svc]:
                    result[svc][prov] = copy.deepcopy(u_fields)
        return result

    def _migrate_from_gsettings(self) -> Dict[str, Any]:
        """Migra le impostazioni attuali da GSettings verso il dizionario iniziale."""
        data = self._merge_with_defaults({})
        settings = self.settings
        if settings is None:
            try:
                from gi.repository import Gio
                settings = Gio.Settings.new("org.gnome.shell.extensions.voice-assistant")
            except Exception:
                settings = None

        if not settings:
            return data

        try:
            schema = None
            try:
                schema = getattr(settings.props, "settings_schema", None)
            except Exception:
                schema = getattr(settings, "settings_schema", None)

            def _get(k: str) -> str:
                if schema and hasattr(schema, "has_key"):
                    if not schema.has_key(k):
                        return ""
                try:
                    return (settings.get_string(k) or "").strip()
                except Exception:
                    return ""

            llm_mode = _get("llm-mode").lower()
            llm_key = _get("llm-api-key")
            llm_ep = _get("llm-endpoint")
            llm_mod = _get("llm-model")

            if llm_key:
                if llm_mode in data["llm"]:
                    data["llm"][llm_mode]["api_key"] = llm_key
                else:
                    data["llm"]["openai"]["api_key"] = llm_key

            if llm_mode in data["llm"]:
                if llm_ep and not llm_ep.startswith("http://localhost:11434"):
                    data["llm"][llm_mode]["endpoint"] = llm_ep
                if llm_mod and not llm_mod.endswith(".gguf"):
                    data["llm"][llm_mode]["model"] = llm_mod

            # TTS
            tts_key = _get("tts-api-key")
            tts_mod = _get("tts-model")
            tts_voice = _get("tts-cloud-voice")
            if tts_key:
                data["tts"]["openai"]["api_key"] = tts_key
            elif llm_key and llm_mode == "openai":
                data["tts"]["openai"]["api_key"] = llm_key
            if tts_mod:
                data["tts"]["openai"]["model"] = tts_mod
            if tts_voice:
                data["tts"]["openai"]["voice"] = tts_voice

            # STT
            stt_extra = _get("stt-extra")
            stt_key = ""
            if stt_extra.strip().startswith("{"):
                try:
                    stt_key = str(json.loads(stt_extra).get("api_key", "")).strip()
                except Exception:
                    pass
            elif stt_extra.strip():
                stt_key = stt_extra.strip()

            if stt_key:
                data["stt"]["openai_cloud"]["api_key"] = stt_key
                data["stt"]["groq_cloud"]["api_key"] = stt_key
        except Exception as e:
            _log.debug("Errore durante migrazione da GSettings: %s", e)

        return data


_GLOBAL_INSTANCE: Optional[CloudConfigManager] = None


def get_cloud_config(config_path: Optional[Path | str] = None, settings=None) -> CloudConfigManager:
    """Restituisce l'istanza singleton o configurata di CloudConfigManager."""
    global _GLOBAL_INSTANCE
    if config_path is not None:
        return CloudConfigManager(config_path=config_path, settings=settings)
    if _GLOBAL_INSTANCE is None:
        _GLOBAL_INSTANCE = CloudConfigManager(settings=settings)
    elif settings is not None and _GLOBAL_INSTANCE.settings is None:
        _GLOBAL_INSTANCE.settings = settings
    return _GLOBAL_INSTANCE
