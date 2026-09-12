"""
Controller per la sottopagina di selezione dei modelli (STT / LLM / TTS).
Gestisce la ricerca, la visualizzazione di modelli disponibili, installati e in download,
le schede "Tutti", "Installati" e "In download", l'annullamento dei download,
l'eliminazione dei modelli scaricati e l'integrazione D-Bus con il demone.
"""

import json
import logging
import os
import shutil
import sys
import threading
from pathlib import Path
from typing import Callable, Any, Optional

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
gi.require_version('GLib', '2.0')
from gi.repository import Gtk, Adw, Gio, GLib

_log = logging.getLogger("VoiceAssistant.GUI.Settings.ModelSelector")

_DBUS_NAME = "org.local.VoiceAssistant"
_DBUS_PATH = "/org/local/VoiceAssistant"
_DBUS_IFACE = "org.local.VoiceAssistant"


class ModelSelectorController:
    """Controlla la NavigationPage model_selector_page e le relative schede."""

    def __init__(self, builder: Gtk.Builder, settings: Gio.Settings | None, parent_window: Gtk.Window | None = None):
        self.builder = builder
        self.settings = settings
        self.cloud_config = get_cloud_config(settings=self.settings)
        self.parent_window = parent_window

        self.nav_view: Adw.NavigationView | None = builder.get_object("content_navigation_view")
        self.page: Adw.NavigationPage | None = builder.get_object("model_selector_page")
        self.scrolled_window: Gtk.ScrolledWindow | None = builder.get_object("selector_scrolled_window")
        self.search_entry: Gtk.SearchEntry | None = builder.get_object("search_entry")
        self.view_stack: Adw.ViewStack | None = builder.get_object("selector_view_stack")
        self.header_bar: Adw.HeaderBar | None = builder.get_object("selector_header_bar")
        self.view_switcher: Adw.ViewSwitcher | None = builder.get_object("selector_view_switcher")
        self.view_switcher_title = self.view_switcher
        self.breakpoint: Adw.Breakpoint | None = builder.get_object("selector_breakpoint")
        self.breakpoint_bin: Adw.BreakpointBin | None = builder.get_object("selector_breakpoint_bin")
        self._default_condition = self.breakpoint.get_condition() if self.breakpoint else None
        self.window_title: Adw.WindowTitle = Adw.WindowTitle()
        self.view_switcher_bar: Adw.ViewSwitcherBar | None = builder.get_object("selector_view_switcher_bar")
        self.group_all: Adw.PreferencesGroup | None = builder.get_object("models_group_all")
        self.group_installed: Adw.PreferencesGroup | None = builder.get_object("models_group_installed")
        self.group_downloading: Adw.PreferencesGroup | None = builder.get_object("models_group_downloading")
        self.hf_custom_group: Adw.PreferencesGroup | None = builder.get_object("llm_hf_custom_group")
        self.hf_dialog_row: Adw.ActionRow | None = builder.get_object("hf_download_dialog_row")
        self.hf_header_btn: Gtk.Button | None = builder.get_object("hf_download_dialog_btn")
        self._custom_entry_row: Adw.ActionRow | None = None

        if self.hf_dialog_row:
            self.hf_dialog_row.connect("activated", self._show_hf_download_dialog)
        if self.hf_header_btn:
            self.hf_header_btn.connect("clicked", self._show_hf_download_dialog)

        if self.view_stack:
            self.view_stack.set_vhomogeneous(False)
            self.view_stack.set_hhomogeneous(False)
            self.view_stack.connect("notify::visible-child", self._on_visible_child_changed)

        self.current_service: str = "stt"
        self.current_provider: str = "vosk"
        self.on_selected_callback: Callable[[str], None] | None = None

        self._all_rows: list[Adw.ActionRow] = []
        self._installed_rows: list[Adw.ActionRow] = []
        self._downloading_rows: list[Adw.ActionRow] = []
        self._items: dict[str, dict] = {}
        self._installed_placeholder: Adw.ActionRow | None = None
        self._downloading_placeholder: Adw.ActionRow | None = None

        if self.search_entry:
            self.search_entry.connect("search-changed", self._on_search_changed)

        # Sottoscrizione al segnale D-Bus DownloadProgress
        self._bus = None
        self._signal_sub_id = None
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            if self._bus:
                self._signal_sub_id = self._bus.signal_subscribe(
                    _DBUS_NAME,
                    _DBUS_IFACE,
                    "DownloadProgress",
                    _DBUS_PATH,
                    None,
                    Gio.DBusSignalFlags.NONE,
                    self._on_dbus_download_progress,
                    None,
                )
        except Exception as e:
            _log.warning("Impossibile connettersi al bus D-Bus per DownloadProgress: %s", e)

    def _on_dbus_download_progress(self, _conn, _sender, _path, _iface, _signal, params, _user_data=None):
        try:
            provider, model_name, percent = params.unpack()
            GLib.idle_add(self._handle_download_progress, provider, model_name, percent)
        except Exception as e:
            _log.error("Errore disimballaggio DownloadProgress: %s", e)

    def _show_hf_download_dialog(self, *_args) -> None:
        """Apre un dialog Adw.AlertDialog per scaricare un modello GGUF da Hugging Face."""
        target_win = self.parent_window or (self.page.get_root() if self.page and hasattr(self.page, "get_root") else None)

        dlg = Adw.AlertDialog(
            heading="Download da Hugging Face",
            body="Inserisci l'ID del repository Hugging Face o il percorso del modello GGUF da scaricare (es. bartowski/Llama-3.2-1B-Instruct-GGUF):",
        )

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        listbox = Gtk.ListBox()
        listbox.add_css_class("boxed-list")
        entry_row = Adw.EntryRow(title="Repository ID / Modello")
        listbox.append(entry_row)
        box.append(listbox)
        dlg.set_extra_child(box)

        dlg.add_response("cancel", "Annulla")
        dlg.add_response("download", "Scarica")
        dlg.set_response_appearance("download", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("download")
        dlg.set_close_response("cancel")

        def _on_response(_d, response_id: str):
            if response_id != "download":
                return
            repo_id = entry_row.get_text().strip()
            if not repo_id:
                return

            try:
                bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                bus.call(
                    _DBUS_NAME,
                    _DBUS_PATH,
                    _DBUS_IFACE,
                    "DownloadModel",
                    GLib.Variant("(ss)", ("llm", repo_id)),
                    None,
                    Gio.DBusCallFlags.NONE,
                    -1,
                    None,
                )
                if self.view_stack:
                    self.view_stack.set_visible_child_name("downloading")
                if target_win:
                    confirm = Adw.AlertDialog(
                        heading="Download Avviato",
                        body=f"Il download del modello '{repo_id}' da Hugging Face è iniziato in background.",
                    )
                    confirm.add_response("ok", "OK")
                    confirm.present(target_win)
            except Exception as e:
                _log.error("Errore avvio download Hugging Face %s: %s", repo_id, e)

        dlg.connect("response", _on_response)
        entry_row.connect("entry-activated", lambda *_: dlg.response("download") if hasattr(dlg, "response") else None)

        if target_win:
            dlg.present(target_win)

    def _on_visible_child_changed(self, _stack, _param) -> None:
        """Quando si cambia scheda, reimposta la posizione di scorrimento in cima."""
        if self.scrolled_window:
            adj = self.scrolled_window.get_vadjustment()
            if adj:
                adj.set_value(0)

    def open_selector(
        self,
        service_type: str,
        provider: str = "",
        on_selected: Callable[[str], None] | None = None,
    ) -> None:
        """Apre la sottopagina di selezione modelli per il servizio e provider specificati."""
        self.current_service = service_type
        if not provider:
            if self.settings:
                if service_type in ("wakeword", "ww"):
                    engine = self.settings.get_string("wakeword-engine") or "vosk"
                    provider = "sherpa-onnx" if engine == "sherpa-onnx" else "vosk"
                elif service_type == "stt":
                    provider = self.settings.get_string("stt-provider") or "vosk"
                elif service_type == "llm":
                    mode = self.settings.get_string("llm-mode") or "local"
                    if mode == "local":
                        provider = "gguf"
                    elif mode == "ollama":
                        provider = "ollama"
                    else:
                        provider = mode
                elif service_type == "tts":
                    provider = self.settings.get_string("tts-provider") or self.settings.get_string("tts-engine") or "piper"
            else:
                provider = "vosk" if service_type in ("stt", "wakeword", "ww") else ("gguf" if service_type == "llm" else "piper")

        self.current_provider = provider
        self.on_selected_callback = on_selected

        is_cloud_llm = (self.current_service == "llm" and self.current_provider.lower() in (
            "openai", "anthropic", "deepseek", "ollama_cloud", "custom"
        ))

        provider_names = {
            "ollama_cloud": "Ollama Cloud",
            "openai": "OpenAI",
            "anthropic": "Anthropic Claude",
            "deepseek": "DeepSeek",
            "custom": "Custom Endpoint",
        }

        if self.page:
            if service_type in ("wakeword", "ww"):
                prov_label = "Sherpa-ONNX" if "sherpa" in provider.lower() else "Vosk"
                title = f"Modelli Wake Word ({prov_label})"
            elif service_type == "tts":
                title = f"Voci TTS ({provider.capitalize()})"
            elif service_type == "stt":
                title = f"Modelli STT ({provider.capitalize()})"
            elif is_cloud_llm:
                title = f"Modelli LLM ({provider_names.get(provider.lower(), provider.capitalize())})"
            else:
                title = f"Modelli LLM ({provider.capitalize()})"
            self.page.set_title(title)

        if self.breakpoint and self._default_condition:
            if is_cloud_llm:
                self.breakpoint.set_condition(None)
            else:
                self.breakpoint.set_condition(self._default_condition)

        if self.header_bar:
            if is_cloud_llm:
                sub = provider_names.get(provider.lower(), provider.capitalize())
                self.window_title.set_title("Modelli LLM")
                self.window_title.set_subtitle(sub)
                self.header_bar.set_title_widget(self.window_title)
            elif self.view_switcher:
                self.header_bar.set_title_widget(self.view_switcher)

        if self.view_switcher_bar:
            if is_cloud_llm:
                self.view_switcher_bar.set_visible(False)
                self.view_switcher_bar.set_reveal(False)
            else:
                self.view_switcher_bar.set_visible(True)
        if self.view_stack and is_cloud_llm:
            self.view_stack.set_visible_child_name("all")

        # Mostra Hugging Face download solo se si stanno selezionando modelli LLM locali (GGUF o Ollama Local)
        is_local_llm = (self.current_service == "llm" and self.current_provider.lower() in ("gguf", "llama", "local", "ollama"))
        if self.hf_custom_group:
            self.hf_custom_group.set_visible(is_local_llm)
        if self.hf_header_btn:
            self.hf_header_btn.set_visible(is_local_llm)

        if self.search_entry:
            self.search_entry.set_text("")

        self._clear_groups()

        if self.page:
            win = self.parent_window or (self.page.get_root() if hasattr(self.page, "get_root") else None)
            if win and hasattr(win, "push_subpage"):
                if hasattr(win, "get_visible_page") and win.get_visible_page() == self.page:
                    pass
                else:
                    win.push_subpage(self.page)
            elif self.nav_view:
                self.nav_view.push(self.page)

        # Carica i modelli in background per non bloccare la UI
        threading.Thread(target=self._load_models_thread, daemon=True).start()

    def _clear_groups(self) -> None:
        if self._custom_entry_row and self.group_all:
            try:
                self.group_all.remove(self._custom_entry_row)
            except Exception:
                pass
            self._custom_entry_row = None

        if self.group_installed and self._installed_placeholder:
            try:
                self.group_installed.remove(self._installed_placeholder)
            except Exception:
                pass
            self._installed_placeholder = None

        if self.group_downloading and self._downloading_placeholder:
            try:
                self.group_downloading.remove(self._downloading_placeholder)
            except Exception:
                pass
            self._downloading_placeholder = None

        for grp, rows in [
            (self.group_all, self._all_rows),
            (self.group_installed, self._installed_rows),
            (self.group_downloading, self._downloading_rows),
        ]:
            if grp:
                for r in list(rows):
                    try:
                        grp.remove(r)
                    except Exception:
                        pass
        self._all_rows.clear()
        self._installed_rows.clear()
        self._downloading_rows.clear()
        self._items.clear()

    def _update_placeholders(self) -> None:
        """Mostra o nasconde i placeholder per le schede Installati e In download quando sono vuote."""
        if self.group_installed:
            if not self._installed_rows:
                if not self._installed_placeholder:
                    row = Adw.ActionRow(
                        title="Nessun modello installato",
                        subtitle="Scarica un modello dalla scheda 'Tutti' per utilizzarlo offline.",
                    )
                    row.set_sensitive(False)
                    self._installed_placeholder = row
                    self.group_installed.add(row)
            else:
                if self._installed_placeholder:
                    try:
                        self.group_installed.remove(self._installed_placeholder)
                    except Exception:
                        pass
                    self._installed_placeholder = None

        if self.group_downloading:
            if not self._downloading_rows:
                if not self._downloading_placeholder:
                    row = Adw.ActionRow(
                        title="Nessun download in corso",
                        subtitle="I modelli in fase di scaricamento verranno mostrati qui.",
                    )
                    row.set_sensitive(False)
                    self._downloading_placeholder = row
                    self.group_downloading.add(row)
            else:
                if self._downloading_placeholder:
                    try:
                        self.group_downloading.remove(self._downloading_placeholder)
                    except Exception:
                        pass
                    self._downloading_placeholder = None

    def _fetch_cloud_models(self, provider: str) -> list[dict]:
        """Recupera modelli disponibili via API o fallback su catalogo locale."""
        p = provider.lower()
        api_key = self.cloud_config.get_api_key("llm", p)
        if not api_key and self.settings:
            api_key = (self.settings.get_string("llm-api-key") or "").strip()

        if p == "ollama_cloud":
            try:
                import urllib.request
                req = urllib.request.Request("https://ollama.com/v1/models", headers={"User-Agent": "VoiceAssistant"})
                with urllib.request.urlopen(req, timeout=4.0) as resp:
                    raw_data = json.loads(resp.read().decode("utf-8"))
                    cloud_list = raw_data.get("data", [])
                    if cloud_list:
                        live_models = []
                        for item in cloud_list:
                            mid = item.get("id", "")
                            if mid:
                                live_models.append({
                                    "id": mid,
                                    "name": mid,
                                    "subtitle": "Ollama Cloud API",
                                    "size_text": "Cloud API",
                                    "provider": "ollama_cloud",
                                })
                        if live_models:
                            return live_models
            except Exception as e:
                _log.info("Impossibile recuperare modelli Ollama Cloud (%s), uso catalogo fallback", e)
            return self._load_local_catalog("llm", "ollama_cloud")

        elif p == "openai":
            if api_key:
                try:
                    import urllib.request
                    req = urllib.request.Request(
                        "https://api.openai.com/v1/models",
                        headers={"Authorization": f"Bearer {api_key}", "User-Agent": "VoiceAssistant"},
                    )
                    with urllib.request.urlopen(req, timeout=4.0) as resp:
                        raw_data = json.loads(resp.read().decode("utf-8"))
                        items = raw_data.get("data", [])
                        if items:
                            chat_models = []
                            for item in items:
                                mid = item.get("id", "")
                                if mid and (mid.startswith(("gpt-", "o1", "o3", "chatgpt-")) or "turbo" in mid):
                                    chat_models.append({
                                        "id": mid,
                                        "name": mid,
                                        "subtitle": "OpenAI Cloud API",
                                        "size_text": "Cloud API",
                                        "provider": "openai",
                                    })
                            if chat_models:
                                priority = ["gpt-4o-mini", "gpt-4o", "o3-mini", "o1-mini", "o1", "gpt-4-turbo", "gpt-3.5-turbo"]
                                def _sort_key(m):
                                    mid = m["id"]
                                    if mid in priority:
                                        return (0, priority.index(mid))
                                    return (1, mid)
                                chat_models.sort(key=_sort_key)
                                return chat_models
                except Exception as e:
                    _log.info("Impossibile recuperare modelli OpenAI da API (%s), uso catalogo fallback", e)
            return self._load_local_catalog("llm", "openai")

        elif p == "anthropic":
            if api_key:
                try:
                    import urllib.request
                    req = urllib.request.Request(
                        "https://api.anthropic.com/v1/models",
                        headers={
                            "x-api-key": api_key,
                            "anthropic-version": "2023-06-01",
                            "User-Agent": "VoiceAssistant",
                        },
                    )
                    with urllib.request.urlopen(req, timeout=4.0) as resp:
                        raw_data = json.loads(resp.read().decode("utf-8"))
                        items = raw_data.get("data", [])
                        if items:
                            claude_models = []
                            for item in items:
                                mid = item.get("id", "")
                                name = item.get("display_name") or mid
                                if mid:
                                    claude_models.append({
                                        "id": mid,
                                        "name": name,
                                        "subtitle": f"{mid} • Anthropic API",
                                        "size_text": "Cloud API",
                                        "provider": "anthropic",
                                    })
                            if claude_models:
                                return claude_models
                except Exception as e:
                    _log.info("Impossibile recuperare modelli Anthropic da API (%s), uso catalogo fallback", e)
            return self._load_local_catalog("llm", "anthropic")

        elif p == "deepseek":
            if api_key:
                try:
                    import urllib.request
                    req = urllib.request.Request(
                        "https://api.deepseek.com/models",
                        headers={"Authorization": f"Bearer {api_key}", "User-Agent": "VoiceAssistant"},
                    )
                    with urllib.request.urlopen(req, timeout=4.0) as resp:
                        raw_data = json.loads(resp.read().decode("utf-8"))
                        items = raw_data.get("data", [])
                        if items:
                            ds_models = []
                            for item in items:
                                mid = item.get("id", "")
                                if mid:
                                    ds_models.append({
                                        "id": mid,
                                        "name": mid,
                                        "subtitle": "DeepSeek API",
                                        "size_text": "Cloud API",
                                        "provider": "deepseek",
                                    })
                            if ds_models:
                                return ds_models
                except Exception as e:
                    _log.info("Impossibile recuperare modelli DeepSeek da API (%s), uso catalogo fallback", e)
            return self._load_local_catalog("llm", "deepseek")

        elif p == "custom":
            ep = (self.settings.get_string("llm-endpoint") or self.settings.get_string("llm-url") or "").strip() if self.settings else ""
            if ep:
                base = ep.replace("/chat/completions", "").replace("/completions", "").rstrip("/")
                for url in (f"{base}/v1/models", f"{base}/models"):
                    try:
                        import urllib.request
                        headers = {"User-Agent": "VoiceAssistant"}
                        if api_key:
                            headers["Authorization"] = f"Bearer {api_key}"
                        req = urllib.request.Request(url, headers=headers)
                        with urllib.request.urlopen(req, timeout=3.0) as resp:
                            raw_data = json.loads(resp.read().decode("utf-8"))
                            items = raw_data.get("data", [])
                            if items:
                                c_models = []
                                for item in items:
                                    mid = item.get("id", "")
                                    if mid:
                                        c_models.append({
                                            "id": mid,
                                            "name": mid,
                                            "subtitle": f"Custom Endpoint ({base})",
                                            "size_text": "HTTP API",
                                            "provider": "custom",
                                        })
                                if c_models:
                                    return c_models
                    except Exception:
                        pass
            return []

        return self._load_local_catalog("llm", provider)

    def _fetch_ollama_models(self) -> list[dict]:
        """Interroga l'endpoint Ollama locale (/api/tags) e unisce il catalogo curato."""
        curated_ollama = [
            {"id": "llama3.2:1b", "name": "Llama 3.2 1B", "subtitle": "Meta • Veloce e leggero", "size_text": "~1.3 GB", "provider": "ollama"},
            {"id": "llama3.2", "name": "Llama 3.2 3B", "subtitle": "Meta • Consigliato", "size_text": "~2.0 GB", "provider": "ollama"},
            {"id": "mistral", "name": "Mistral 7B", "subtitle": "Mistral AI • Alta qualità", "size_text": "~4.1 GB", "provider": "ollama"},
            {"id": "gemma2:2b", "name": "Gemma 2 2B", "subtitle": "Google • Compatto", "size_text": "~1.6 GB", "provider": "ollama"},
            {"id": "qwen2.5:3b", "name": "Qwen 2.5 3B", "subtitle": "Alibaba • Multilingua", "size_text": "~1.9 GB", "provider": "ollama"},
            {"id": "phi3:mini", "name": "Phi-3 Mini 3.8B", "subtitle": "Microsoft • Ottime capacità", "size_text": "~2.2 GB", "provider": "ollama"},
            {"id": "deepseek-r1:1.5b", "name": "DeepSeek R1 1.5B", "subtitle": "DeepSeek • Ragionamento", "size_text": "~1.1 GB", "provider": "ollama"},
            {"id": "deepseek-r1:7b", "name": "DeepSeek R1 7B", "subtitle": "DeepSeek • Ragionamento avanzato", "size_text": "~4.7 GB", "provider": "ollama"},
        ]
        endpoint = "http://localhost:11434"
        if self.settings:
            try:
                endpoint = self.settings.get_string("llm-endpoint") or endpoint
            except Exception:
                pass
        endpoint = endpoint.rstrip("/")
        if endpoint.endswith("/v1"):
            endpoint = endpoint[:-3]
        elif endpoint.endswith("/v1/chat/completions"):
            endpoint = endpoint[:-20]

        installed_tags = {}
        try:
            import urllib.request
            req = urllib.request.Request(f"{endpoint}/api/tags", headers={"User-Agent": "VoiceAssistant/1.0"})
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                for m in data.get("models", []):
                    tag = m.get("name", "")
                    if tag:
                        size_bytes = m.get("size", 0)
                        size_mb = round(size_bytes / (1024 * 1024))
                        size_str = f"{size_mb} MB" if size_mb < 1024 else f"{size_mb / 1024:.1f} GB"
                        installed_tags[tag.lower()] = {
                            "name": tag,
                            "size_text": size_str,
                        }
        except Exception as e:
            _log.info("Impossibile contattare Ollama /api/tags (%s), uso catalogo fallback", e)

        models = []
        seen_ids = set()

        for tag_low, info in installed_tags.items():
            tag_name = info["name"]
            models.append({
                "id": tag_name,
                "name": tag_name,
                "subtitle": f"Installato • {info['size_text']}",
                "size_text": info["size_text"],
                "installed": True,
                "provider": "ollama",
            })
            seen_ids.add(tag_name.lower())

        for cm in curated_ollama:
            mid = cm["id"].lower()
            if mid not in seen_ids:
                item = dict(cm)
                item["installed"] = False
                models.append(item)
                seen_ids.add(mid)

        return models

    def _load_models_thread(self) -> None:
        is_cloud_llm = (self.current_service == "llm" and self.current_provider.lower() in (
            "openai", "anthropic", "deepseek", "ollama_cloud", "custom"
        ))

        if is_cloud_llm:
            models = self._fetch_cloud_models(self.current_provider.lower())
            GLib.idle_add(self._populate_ui, models, {})
            return

        models = []
        downloading = {}

        # 1. Prova a interrogare il demone via D-Bus
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            reply = bus.call_sync(
                _DBUS_NAME,
                _DBUS_PATH,
                _DBUS_IFACE,
                "GetAvailableModels",
                GLib.Variant("(s)", (self.current_provider,)),
                None,
                Gio.DBusCallFlags.NONE,
                3000,
                None,
            )
            raw_json = reply.unpack()[0]
            models = json.loads(raw_json)
        except Exception as e:
            _log.info("D-Bus GetAvailableModels non disponibile (%s), fallback diretto", e)
            if self.current_provider.lower() == "ollama":
                models = self._fetch_ollama_models()
            else:
                models = self._load_local_catalog(self.current_service, self.current_provider)

        if not models:
            if self.current_provider.lower() == "ollama":
                models = self._fetch_ollama_models()
            else:
                models = self._load_local_catalog(self.current_service, self.current_provider)

        # Se il servizio è LLM e il provider è locale/gguf, includi i modelli registrati nel ModelRegistry
        if self.current_service == "llm" and self.current_provider.lower() in ("gguf", "llama", "local"):
            from .models import _DEFAULT_MODELS_DIR
            models_dir = _DEFAULT_MODELS_DIR
            if self.settings:
                models_dir = self.settings.get_string("models-dir") or models_dir
            reg = get_model_registry(models_dir=models_dir)
            installed_ggufs = reg.get_installed_models("gguf")
            seen_files = {
                (m.get("file") or m.get("id", "")).split(":")[-1].lower()
                for m in models
            }
            seen_files.update({
                (m.get("file") or m.get("id", "")).split("/")[-1].lower()
                for m in models
            })
            local_entries = []
            for entry in installed_ggufs:
                fname = entry.get("filename") or entry.get("id") or ""
                if fname.lower() not in seen_files:
                    size_str = entry.get("size_text") or "Locale"
                    name = entry.get("name") or fname
                    local_entries.append({
                        "id": entry.get("id", fname),
                        "name": name,
                        "subtitle": f"Locale • {size_str}",
                        "file": fname,
                        "size_text": size_str,
                        "installed": True,
                        "provider": "llm",
                    })
            if local_entries:
                models = local_entries + models

        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            dl_reply = bus.call_sync(
                _DBUS_NAME,
                _DBUS_PATH,
                _DBUS_IFACE,
                "GetDownloadingModels",
                None,
                None,
                Gio.DBusCallFlags.NONE,
                1500,
                None,
            )
            downloading = json.loads(dl_reply.unpack()[0])
        except Exception:
            downloading = {}

        GLib.idle_add(self._populate_ui, models, downloading)

    def _load_local_catalog(self, service: str, provider: str) -> list[dict]:
        """Carica modelli di fallback quando il demone non è attivo."""
        p = provider.lower()
        if service in ("wakeword", "ww"):
            if p == "vosk":
                return [
                    {"id": "vosk-model-small-it-0.22", "name": "Italiano (Small) - 47 MB", "size_text": "47 MB", "lang": "it"},
                    {"id": "vosk-model-it-0.22", "name": "Italiano (Completo) - 1.2 GB", "size_text": "1.2 GB", "lang": "it"},
                    {"id": "vosk-model-small-en-us-0.15", "name": "Inglese (Small) - 40 MB", "size_text": "40 MB", "lang": "en"},
                    {"id": "vosk-model-en-us-0.22", "name": "Inglese (Completo) - 1.8 GB", "size_text": "1.8 GB", "lang": "en"},
                ]
            elif p in ("sherpa", "sherpa-onnx", "sherpa_onnx"):
                try:
                    from services.catalog_manager import catalog_service
                    return catalog_service.get_sherpa_models()
                except Exception:
                    return [
                        {"id": "sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20", "name": "Sherpa Zipformer Bilingual ZH/EN (Consigliato)", "size_text": "31.4 MB", "lang": "multilingual"},
                        {"id": "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01", "name": "Sherpa Zipformer Gigaspeech", "size_text": "16.8 MB", "lang": "en"},
                        {"id": "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01-mobile", "name": "Sherpa Zipformer Gigaspeech Mobile", "size_text": "14.9 MB", "lang": "en"},
                        {"id": "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01", "name": "Sherpa Zipformer Wenetspeech", "size_text": "31.1 MB", "lang": "zh"},
                        {"id": "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01-mobile", "name": "Sherpa Zipformer Wenetspeech Mobile", "size_text": "14.6 MB", "lang": "zh"},
                    ]
        elif service == "stt":
            if p == "vosk":
                return [
                    {"id": "vosk-model-small-it-0.22", "name": "Italiano (Small) - 47 MB", "size_text": "47 MB", "lang": "it"},
                    {"id": "vosk-model-it-0.22", "name": "Italiano (Completo) - 1.2 GB", "size_text": "1.2 GB", "lang": "it"},
                    {"id": "vosk-model-small-en-us-0.15", "name": "Inglese (Small) - 40 MB", "size_text": "40 MB", "lang": "en"},
                    {"id": "vosk-model-en-us-0.22", "name": "Inglese (Completo) - 1.8 GB", "size_text": "1.8 GB", "lang": "en"},
                ]
            elif p == "whisper":
                return [
                    {"id": "tiny", "name": "Whisper Tiny - 75 MB (Molto veloce)", "size_text": "75 MB"},
                    {"id": "base", "name": "Whisper Base - 145 MB (Bilanciato)", "size_text": "145 MB"},
                    {"id": "small", "name": "Whisper Small - 480 MB (Accurato)", "size_text": "480 MB"},
                    {"id": "medium", "name": "Whisper Medium - 1.5 GB (Alta precisione)", "size_text": "1.5 GB"},
                    {"id": "large-v3", "name": "Whisper Large v3 - 3.1 GB (Massima precisione)", "size_text": "3.1 GB"},
                ]
            elif p in ("openai_cloud", "cloud_stt", "groq_cloud"):
                return [
                    {"id": "whisper-1", "name": "OpenAI Whisper-1 (Cloud)", "size_text": "Cloud API"},
                ]
        elif service == "llm":
            if p == "ollama":
                return [
                    {"id": "llama3.2:1b", "name": "Llama 3.2 1B (Veloce, leggero)", "size_text": "~1.3 GB", "provider": "ollama"},
                    {"id": "llama3.2", "name": "Llama 3.2 3B (Consigliato)", "size_text": "~2.0 GB", "provider": "ollama"},
                    {"id": "mistral", "name": "Mistral 7B (Alta qualità)", "size_text": "~4.1 GB", "provider": "ollama"},
                    {"id": "gemma2:2b", "name": "Gemma 2 2B (Google)", "size_text": "~1.6 GB", "provider": "ollama"},
                    {"id": "qwen2.5:3b", "name": "Qwen 2.5 3B (Alibaba)", "size_text": "~1.9 GB", "provider": "ollama"},
                    {"id": "phi3:mini", "name": "Phi-3 Mini 3.8B (Microsoft)", "size_text": "~2.2 GB", "provider": "ollama"},
                    {"id": "deepseek-r1:1.5b", "name": "DeepSeek R1 1.5B (Ragionamento)", "size_text": "~1.1 GB", "provider": "ollama"},
                    {"id": "deepseek-r1:7b", "name": "DeepSeek R1 7B (Ragionamento avanzato)", "size_text": "~4.7 GB", "provider": "ollama"},
                ]
            elif p == "deepseek":
                return [
                    {"id": "deepseek-chat", "name": "DeepSeek-V3 Chat (Consigliato - Veloce, economico)", "size_text": "Cloud API", "provider": "deepseek"},
                    {"id": "deepseek-reasoner", "name": "DeepSeek-R1 Reasoner (Ragionamento avanzato)", "size_text": "Cloud API", "provider": "deepseek"},
                ]
            elif p == "ollama_cloud":
                return [
                    {"id": "llama3.3", "name": "Llama 3.3 70B (Consigliato - Cloud)", "size_text": "Ollama Cloud", "provider": "ollama_cloud"},
                    {"id": "deepseek-r1", "name": "DeepSeek R1 (Ragionamento - Cloud)", "size_text": "Ollama Cloud", "provider": "ollama_cloud"},
                    {"id": "qwen2.5", "name": "Qwen 2.5 72B (Multilingua - Cloud)", "size_text": "Ollama Cloud", "provider": "ollama_cloud"},
                    {"id": "llama3.2:3b", "name": "Llama 3.2 3B (Veloce - Cloud)", "size_text": "Ollama Cloud", "provider": "ollama_cloud"},
                    {"id": "mistral", "name": "Mistral 7B (Alta qualità - Cloud)", "size_text": "Ollama Cloud", "provider": "ollama_cloud"},
                ]
            elif p == "openai":
                return [
                    {"id": "gpt-4o-mini", "name": "GPT-4o Mini (Consigliato - Veloce, leggero)", "size_text": "Cloud API", "provider": "openai"},
                    {"id": "gpt-4o", "name": "GPT-4o (Alta intelligenza multimodale)", "size_text": "Cloud API", "provider": "openai"},
                    {"id": "gpt-4-turbo", "name": "GPT-4 Turbo", "size_text": "Cloud API", "provider": "openai"},
                    {"id": "gpt-3.5-turbo", "name": "GPT-3.5 Turbo", "size_text": "Cloud API", "provider": "openai"},
                ]
            elif p == "anthropic":
                return [
                    {"id": "claude-haiku-4-5-20251001", "name": "Claude Haiku 4.5 (Consigliato - Ultra veloce)", "size_text": "Cloud API", "provider": "anthropic"},
                    {"id": "claude-3-5-haiku-latest", "name": "Claude 3.5 Haiku", "size_text": "Cloud API", "provider": "anthropic"},
                    {"id": "claude-3-5-sonnet-latest", "name": "Claude 3.5 Sonnet (Alta precisione)", "size_text": "Cloud API", "provider": "anthropic"},
                ]
            else:
                return [
                    {"id": "bartowski/Llama-3.2-1B-Instruct-GGUF:Llama-3.2-1B-Instruct-Q4_K_M.gguf", "name": "Llama 3.2 1B Instruct (GGUF - Consigliato)", "file": "Llama-3.2-1B-Instruct-Q4_K_M.gguf", "size_text": "~800 MB", "provider": "llm"},
                    {"id": "bartowski/Llama-3.2-3B-Instruct-GGUF:Llama-3.2-3B-Instruct-Q4_K_M.gguf", "name": "Llama 3.2 3B Instruct (GGUF)", "file": "Llama-3.2-3B-Instruct-Q4_K_M.gguf", "size_text": "~2.0 GB", "provider": "llm"},
                    {"id": "google/gemma-2-2b-it-GGUF:gemma-2-2b-it-Q4_K_M.gguf", "name": "Gemma 2 2B IT (GGUF)", "file": "gemma-2-2b-it-Q4_K_M.gguf", "size_text": "~1.6 GB", "provider": "llm"},
                    {"id": "Qwen/Qwen2.5-1.5B-Instruct-GGUF:qwen2.5-1.5b-instruct-q4_k_m.gguf", "name": "Qwen 2.5 1.5B Instruct (GGUF)", "file": "qwen2.5-1.5b-instruct-q4_k_m.gguf", "size_text": "~1.0 GB", "provider": "llm"},
                    {"id": "Qwen/Qwen2.5-3B-Instruct-GGUF:qwen2.5-3b-instruct-q4_k_m.gguf", "name": "Qwen 2.5 3B Instruct (GGUF)", "file": "qwen2.5-3b-instruct-q4_k_m.gguf", "size_text": "~2.0 GB", "provider": "llm"},
                    {"id": "bartowski/Mistral-7B-Instruct-v0.3-GGUF:Mistral-7B-Instruct-v0.3-Q4_K_M.gguf", "name": "Mistral 7B Instruct v0.3 (GGUF)", "file": "Mistral-7B-Instruct-v0.3-Q4_K_M.gguf", "size_text": "~4.4 GB", "provider": "llm"},
                    {"id": "bartowski/DeepSeek-R1-Distill-Qwen-1.5B-GGUF:DeepSeek-R1-Distill-Qwen-1.5B-Q4_K_M.gguf", "name": "DeepSeek R1 Distill 1.5B (GGUF)", "file": "DeepSeek-R1-Distill-Qwen-1.5B-Q4_K_M.gguf", "size_text": "~1.1 GB", "provider": "llm"},
                ]
        elif service == "tts":
            return [
                {"id": "it_IT-paola-medium", "name": "Paola (Italiano - Medium)", "lang": "it", "size_text": "~60 MB"},
                {"id": "it_IT-riccardo-x_low", "name": "Riccardo (Italiano - X-Low)", "lang": "it", "size_text": "~20 MB"},
                {"id": "en_US-lessac-medium", "name": "Lessac (English - Medium)", "lang": "en", "size_text": "~60 MB"},
                {"id": "en_US-amy-medium", "name": "Amy (English - Medium)", "lang": "en", "size_text": "~60 MB"},
                {"id": "de_DE-thorsten-medium", "name": "Thorsten (Deutsch - Medium)", "lang": "de", "size_text": "~60 MB"},
                {"id": "fr_FR-siwis-medium", "name": "Siwis (Français - Medium)", "lang": "fr", "size_text": "~60 MB"},
                {"id": "es_ES-sharvard-medium", "name": "Sharvard (Español - Medium)", "lang": "es", "size_text": "~60 MB"},
            ]
        return []

    def _get_installed_models_set(self) -> set[str]:
        from .models import _DEFAULT_MODELS_DIR
        models_dir = _DEFAULT_MODELS_DIR
        if self.settings:
            models_dir = self.settings.get_string("models-dir") or models_dir
        reg = get_model_registry(models_dir=models_dir)
        installed = reg.get_installed_model_ids(self.current_provider)
        if self.current_service == "stt":
            installed = installed.union(reg.get_installed_model_ids("vosk")).union(reg.get_installed_model_ids("whisper"))
        elif self.current_service in ("wakeword", "ww"):
            installed = installed.union(reg.get_installed_model_ids("vosk")).union(reg.get_installed_model_ids("sherpa-onnx"))
        return installed

    @staticmethod
    def _models_match(id1: str, id2: str, provider: str = "") -> bool:
        """Confronta in modo robusto due ID di modelli a seconda del provider."""
        s1 = (id1 or "").strip().lower()
        s2 = (id2 or "").strip().lower()
        if not s1 or not s2:
            return False
        if s1 == s2:
            return True

        if provider.lower() == "ollama":
            norm1 = s1 if ":" in s1 else f"{s1}:latest"
            norm2 = s2 if ":" in s2 else f"{s2}:latest"
            return norm1 == norm2

        b1 = s1.split("/")[-1].split(":")[-1]
        b2 = s2.split("/")[-1].split(":")[-1]
        if b1 == b2:
            return True

        stem1 = b1[:-5] if b1.endswith(".gguf") else (b1[:-4] if b1.endswith(".bin") else b1)
        stem2 = b2[:-5] if b2.endswith(".gguf") else (b2[:-4] if b2.endswith(".bin") else b2)
        if stem1 == stem2:
            return True

        for pfx in ("vosk-model-", "whisper-", "models--systran--faster-whisper-"):
            if b1.startswith(pfx) and b1[len(pfx):] == b2:
                return True
            if b2.startswith(pfx) and b2[len(pfx):] == b1:
                return True
            if stem1.startswith(pfx) and stem1[len(pfx):] == stem2:
                return True
            if stem2.startswith(pfx) and stem2[len(pfx):] == stem1:
                return True

        return False

    def _delete_model_files(self, provider: str, model_id: str) -> None:
        """Elimina tramite il ModelRegistry i file registrati associati al modello."""
        from .models import _DEFAULT_MODELS_DIR
        models_dir = _DEFAULT_MODELS_DIR
        if self.settings:
            models_dir = self.settings.get_string("models-dir") or models_dir
        reg = get_model_registry(models_dir=models_dir)
        deleted = reg.delete_model(provider, model_id)
        if deleted:
            _log.info("Modello %s eliminato con successo dal ModelRegistry (%s)", model_id, provider)
        else:
            _log.warning("Nessun file trovato nel ModelRegistry per modello %s (%s)", model_id, provider)

    def _populate_ui(self, models: list[dict], downloading: dict) -> bool:
        self._clear_groups()

        is_cloud_llm = (self.current_service == "llm" and self.current_provider.lower() in (
            "openai", "anthropic", "deepseek", "ollama_cloud", "custom"
        ))

        current_model = ""
        if self.settings:
            if self.current_service in ("wakeword", "ww"):
                if self.current_provider == "vosk":
                    current_model = self.settings.get_string("vosk-ww-model") or ""
                elif self.current_provider in ("sherpa", "sherpa-onnx", "sherpa_onnx"):
                    current_model = self.settings.get_string("sherpa-model") or ""
            elif self.current_service == "stt":
                current_model = self.settings.get_string("stt-model") or ""
            elif self.current_service == "llm":
                if is_cloud_llm:
                    current_model = self.cloud_config.get_model("llm", self.current_provider) or (self.settings.get_string("llm-model") if self.settings else "")
                else:
                    current_model = self.settings.get_string("llm-model") or ""
            elif self.current_service == "tts":
                current_model = self.settings.get_string("tts-voice") or ""

        if is_cloud_llm:
            if self.group_all:
                provider_names = {
                    "ollama_cloud": "Ollama Cloud",
                    "openai": "OpenAI",
                    "anthropic": "Anthropic Claude",
                    "deepseek": "DeepSeek",
                    "custom": "Custom Endpoint",
                }
                prov_label = provider_names.get(self.current_provider.lower(), self.current_provider.capitalize())
                self.group_all.set_title(f"Modelli {prov_label} Disponibili")

                # Riga speciale per inserimento modello personalizzato
                custom_row = Adw.ActionRow(
                    title="Inserisci modello personalizzato…",
                    subtitle="Specifica manualmente l'ID di un modello non in elenco",
                )
                custom_row.set_activatable(True)
                custom_row.add_prefix(Gtk.Image.new_from_icon_name("document-edit-symbolic"))
                custom_row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
                custom_row.connect("activated", self._show_custom_model_dialog)
                self.group_all.add(custom_row)
                self._custom_entry_row = custom_row

            curr_low = (current_model or "").strip().lower()

            for m in models:
                m_id = m.get("id") or m.get("name") or ""
                m_name = m.get("name") or m_id
                m_sub = m.get("subtitle") or m.get("size_text") or "Cloud API"

                row = Adw.ActionRow(title=m_name, subtitle=m_sub)
                row.set_activatable(True)
                row._model_id = m_id
                row._search_key = f"{m_name} {m_sub} {m_id}".lower()

                if curr_low and curr_low == m_id.lower():
                    c_img = Gtk.Image.new_from_icon_name("check-plain-symbolic")
                    c_img.set_valign(Gtk.Align.CENTER)
                    row.add_suffix(c_img)

                row.connect("activated", self._on_cloud_row_activated, m_id)

                if self.group_all:
                    self.group_all.add(row)
                    self._all_rows.append(row)

            self._update_placeholders()
            return False

        if self.current_provider.lower() == "ollama" and self.group_all:
            self.group_all.set_title("Modelli Ollama")
            custom_row = Adw.ActionRow(
                title="Scarica modello personalizzato da Ollama…",
                subtitle="Specifica il tag di un modello Ollama da scaricare (es. smollm:135m, llama3.1:8b)",
            )
            custom_row.set_activatable(True)
            custom_row.add_prefix(Gtk.Image.new_from_icon_name("document-edit-symbolic"))
            custom_row.add_suffix(Gtk.Image.new_from_icon_name("folder-download-symbolic"))
            custom_row.connect("activated", self._show_custom_ollama_dialog)
            self.group_all.add(custom_row)
            self._custom_entry_row = custom_row

        installed_set = self._get_installed_models_set()
        installed_set_lower = {str(x).lower() for x in installed_set}

        for m in models:
            m_id = m.get("id") or m.get("name") or ""
            m_name = m.get("name") or m_id
            m_size = m.get("size_text") or m.get("size") or ""
            m_lang = m.get("lang") or ""

            subtitle_parts = []
            if m_size:
                subtitle_parts.append(str(m_size))
            if m_lang:
                subtitle_parts.append(f"Lingua: {m_lang}")
            subtitle = " • ".join(subtitle_parts) if subtitle_parts else m_id

            m_id_low = m_id.lower()
            m_base_low = m_id.split("/")[-1].lower()
            m_file_low = m_id.split(":")[-1].lower() if ":" in m_id else m_base_low
            m_file_stem = m_file_low[:-5] if m_file_low.endswith(".gguf") else (m_file_low[:-4] if m_file_low.endswith(".bin") else m_file_low)
            m_base_stem = m_base_low[:-5] if m_base_low.endswith(".gguf") else (m_base_low[:-4] if m_base_low.endswith(".bin") else m_base_low)

            curr_low = current_model.lower() if current_model else ""

            if self.current_provider.lower() == "ollama":
                is_installed = bool(m.get("installed"))
                is_active = is_installed and bool(
                    curr_low and self._models_match(curr_low, m_id_low, provider="ollama")
                )
            else:
                curr_file_low = curr_low.split(":")[-1] if ":" in curr_low else curr_low.split("/")[-1]

                is_active = bool(
                    curr_low and (
                        curr_low == m_id_low or
                        m_id_low in curr_low or
                        (curr_file_low and (curr_file_low == m_file_low or curr_file_low == m_base_low))
                    )
                )
                in_local_set = (
                    m_id_low in installed_set_lower or
                    m_base_low in installed_set_lower or
                    m_file_low in installed_set_lower or
                    m_file_stem in installed_set_lower or
                    m_base_stem in installed_set_lower or
                    f"vosk-model-{m_id_low}" in installed_set_lower or
                    f"whisper-{m_id_low}" in installed_set_lower or
                    f"whisper-{m_base_low}" in installed_set_lower or
                    f"models--systran--faster-whisper-{m_id_low}" in installed_set_lower or
                    f"models--systran--faster-whisper-{m_base_low}" in installed_set_lower
                )
                is_installed = bool(m.get("installed")) or in_local_set

                # Per i provider locali, il modello può essere attivo solo se è effettivamente installato
                if self.current_provider.lower() in ("gguf", "llama", "local", "vosk", "whisper", "piper", "sherpa", "sherpa-onnx", "sherpa_onnx"):
                    is_active = is_active and is_installed
            
            # Controllo download attivo
            is_dl = False
            pct = 0
            p_curr = self.current_provider.lower()
            for k, p in downloading.items():
                dl_prov, _, dl_mod = k.partition(":")
                if self._same_provider_family(dl_prov, p_curr):
                    if self._models_match(dl_mod, m_id, provider=p_curr):
                        is_dl = True
                        pct = p
                        break

            state = "downloading" if is_dl else ("installed" if is_installed else "available")

            # Creazione della riga per "Tutti"
            row_all = Adw.ActionRow(title=m_name, subtitle=subtitle)
            row_all.set_activatable(True)
            row_all._model_id = m_id
            search_key = f"{m_name} {subtitle} {m_id}".lower()
            row_all._search_key = search_key

            suffix_all = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, valign=Gtk.Align.CENTER)
            row_all.add_suffix(suffix_all)
            row_all.connect("activated", self._on_row_activated, m_id)

            if self.group_all:
                self.group_all.add(row_all)
                self._all_rows.append(row_all)

            item = {
                "model_id": m_id,
                "name": m_name,
                "subtitle": subtitle,
                "search_key": search_key,
                "state": state,
                "percent": pct,
                "is_active": is_active,
                "row_all": row_all,
                "suffix_all": suffix_all,
                "row_installed": None,
                "suffix_installed": None,
                "row_downloading": None,
                "suffix_downloading": None,
            }
            self._items[m_id] = item

            # Aggiorna la vista e le schede (Installati / In download)
            self._render_model_suffixes(item)

        self._update_placeholders()
        return False

    def _render_model_suffixes(self, item: dict) -> None:
        """Sincronizza i widget di suffisso e la presenza nelle schede Tutti, Installati e In download."""
        m_id = item["model_id"]
        state = item["state"]
        pct = item["percent"]
        is_active = item["is_active"]

        # --- 1. Scheda "TUTTI" (suffix_all) ---
        suffix_all = item["suffix_all"]
        while child := suffix_all.get_first_child():
            suffix_all.remove(child)

        if is_active:
            check_img = Gtk.Image.new_from_icon_name("check-plain-symbolic")
            check_img.set_valign(Gtk.Align.CENTER)
            suffix_all.append(check_img)

        if state == "downloading":
            pbar = Gtk.ProgressBar(valign=Gtk.Align.CENTER, hexpand=False)
            pbar.set_size_request(80, -1)
            pbar.set_fraction(max(0.0, min(1.0, pct / 100.0)))
            lbl = Gtk.Label(label=f"{pct}%", valign=Gtk.Align.CENTER)
            lbl.add_css_class("dim-label")
            cancel_btn = Gtk.Button(icon_name="process-stop-symbolic", valign=Gtk.Align.CENTER)
            cancel_btn.set_tooltip_text("Annulla scaricamento")
            cancel_btn.add_css_class("flat")
            cancel_btn.connect("clicked", lambda _b, mid=m_id: self._on_cancel_clicked(mid))

            suffix_all.append(pbar)
            suffix_all.append(lbl)
            suffix_all.append(cancel_btn)

        elif state == "installed":
            del_btn = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
            del_btn.set_tooltip_text("Elimina modello")
            del_btn.add_css_class("flat")
            del_btn.connect("clicked", lambda _b, mid=m_id: self._on_delete_clicked(mid))
            suffix_all.append(del_btn)

        else:  # state == "available"
            if self.current_provider in ("vosk", "whisper", "piper", "gguf", "sherpa", "sherpa-onnx", "sherpa_onnx", "ollama"):
                dl_btn = Gtk.Button(icon_name="folder-download-symbolic", valign=Gtk.Align.CENTER)
                dl_btn.set_tooltip_text("Scarica modello")
                dl_btn.add_css_class("flat")
                dl_btn.connect("clicked", lambda _b, mid=m_id: self._on_download_clicked(mid))
                suffix_all.append(dl_btn)

        # --- 2. Scheda "INSTALLATI" (models_group_installed) ---
        if state == "installed":
            if not item["row_installed"]:
                row_inst = Adw.ActionRow(title=item["name"], subtitle=item["subtitle"])
                row_inst.set_activatable(True)
                row_inst._model_id = m_id
                row_inst._search_key = item["search_key"]
                row_inst.connect("activated", self._on_row_activated, m_id)

                suffix_inst = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, valign=Gtk.Align.CENTER)
                row_inst.add_suffix(suffix_inst)

                if self.group_installed:
                    self.group_installed.add(row_inst)
                    self._installed_rows.append(row_inst)

                item["row_installed"] = row_inst
                item["suffix_installed"] = suffix_inst

            # Aggiorna suffisso in scheda installati
            s_inst = item["suffix_installed"]
            while child := s_inst.get_first_child():
                s_inst.remove(child)

            if is_active:
                c_img = Gtk.Image.new_from_icon_name("check-plain-symbolic")
                c_img.set_valign(Gtk.Align.CENTER)
                s_inst.append(c_img)

            del_btn_inst = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
            del_btn_inst.set_tooltip_text("Elimina modello")
            del_btn_inst.add_css_class("flat")
            del_btn_inst.connect("clicked", lambda _b, mid=m_id: self._on_delete_clicked(mid))
            s_inst.append(del_btn_inst)

        else:
            if item["row_installed"]:
                if self.group_installed:
                    try:
                        self.group_installed.remove(item["row_installed"])
                    except Exception:
                        pass
                if item["row_installed"] in self._installed_rows:
                    self._installed_rows.remove(item["row_installed"])
                item["row_installed"] = None
                item["suffix_installed"] = None

        # --- 3. Scheda "IN DOWNLOAD" (models_group_downloading) ---
        if state == "downloading":
            if not item["row_downloading"]:
                row_dl = Adw.ActionRow(title=item["name"], subtitle=item["subtitle"])
                row_dl.set_activatable(False)
                row_dl._model_id = m_id
                row_dl._search_key = item["search_key"]

                suffix_dl = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, valign=Gtk.Align.CENTER)
                row_dl.add_suffix(suffix_dl)

                if self.group_downloading:
                    self.group_downloading.add(row_dl)
                    self._downloading_rows.append(row_dl)

                item["row_downloading"] = row_dl
                item["suffix_downloading"] = suffix_dl

            # Aggiorna suffisso in scheda in download
            s_dl = item["suffix_downloading"]
            while child := s_dl.get_first_child():
                s_dl.remove(child)

            pbar_dl = Gtk.ProgressBar(valign=Gtk.Align.CENTER, hexpand=False)
            pbar_dl.set_size_request(80, -1)
            pbar_dl.set_fraction(max(0.0, min(1.0, pct / 100.0)))
            lbl_dl = Gtk.Label(label=f"{pct}%", valign=Gtk.Align.CENTER)
            lbl_dl.add_css_class("dim-label")
            cancel_btn_dl = Gtk.Button(icon_name="process-stop-symbolic", valign=Gtk.Align.CENTER)
            cancel_btn_dl.set_tooltip_text("Annulla scaricamento")
            cancel_btn_dl.add_css_class("flat")
            cancel_btn_dl.connect("clicked", lambda _b, mid=m_id: self._on_cancel_clicked(mid))

            s_dl.append(pbar_dl)
            s_dl.append(lbl_dl)
            s_dl.append(cancel_btn_dl)

        else:
            if item["row_downloading"]:
                if self.group_downloading:
                    try:
                        self.group_downloading.remove(item["row_downloading"])
                    except Exception:
                        pass
                if item["row_downloading"] in self._downloading_rows:
                    self._downloading_rows.remove(item["row_downloading"])
                item["row_downloading"] = None
                item["suffix_downloading"] = None

        self._update_placeholders()

    @staticmethod
    def _same_provider_family(a: str, b: str) -> bool:
        """Confronta due identificatori di provider tollerando gli alias equivalenti.

        Il demone indicizza i download con il nome ricevuto da chi li avvia ("llm" per un
        GGUF scaricato dalla pipeline), mentre il selettore usa il provider della pagina
        corrente ("gguf"): senza questa equivalenza l'avanzamento non verrebbe agganciato
        alla riga del modello.
        """
        families = (
            {"llm", "gguf", "llama", "local"},
            {"piper", "tts"},
            {"sherpa", "sherpa-onnx", "sherpa_onnx"},
        )
        a_low = (a or "").lower()
        b_low = (b or "").lower()
        if a_low == b_low:
            return True
        return any(a_low in family and b_low in family for family in families)

    def _set_model_state(self, model_id: str, state: str, percent: int = 0, is_active: Optional[bool] = None) -> None:
        item = self._items.get(model_id)
        if not item:
            for mid, itm in self._items.items():
                if mid.lower() == model_id.lower():
                    item = itm
                    break
        if not item:
            for mid, itm in self._items.items():
                if self._models_match(mid, model_id, provider=self.current_provider):
                    item = itm
                    break
        if not item:
            return

        item["state"] = state
        item["percent"] = percent
        if is_active is not None:
            item["is_active"] = is_active
        self._render_model_suffixes(item)

    def _handle_download_progress(self, provider: str, model_name: str, percent: int) -> bool:
        p_curr = self.current_provider.lower()
        p_ev = provider.lower()
        if not self._same_provider_family(p_curr, p_ev):
            return False

        m_target = None
        # 1. Match esatto case-insensitive
        for mid in self._items.keys():
            if mid.lower() == model_name.lower():
                m_target = mid
                break

        # 2. Match conforme alle convenzioni del provider
        if not m_target:
            for mid in self._items.keys():
                if self._models_match(mid, model_name, provider=p_curr):
                    m_target = mid
                    break

        if not m_target:
            return False

        if 0 <= percent < 100:
            self._set_model_state(m_target, "downloading", percent=percent)
        elif percent == 100:
            self._set_model_state(m_target, "installed", percent=100)
            if self.parent_window and hasattr(self.parent_window, "models_manager"):
                try:
                    self.parent_window.models_manager.refresh()
                except Exception:
                    pass
        elif percent == -1:
            self._set_model_state(m_target, "available")

        return False

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        query = entry.get_text().strip().lower()
        for row in self._all_rows + self._installed_rows + self._downloading_rows:
            if not query:
                row.set_visible(True)
            else:
                key = getattr(row, "_search_key", "")
                row.set_visible(query in key)

        if self._installed_placeholder:
            self._installed_placeholder.set_visible(not bool(query))
        if self._downloading_placeholder:
            self._downloading_placeholder.set_visible(not bool(query))

    def _on_download_clicked(self, model_id: str) -> None:
        """Avvia il download e trasforma la riga mostrando la progress bar e il tasto annulla."""
        self._set_model_state(model_id, "downloading", percent=0)
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.call_sync(
                _DBUS_NAME,
                _DBUS_PATH,
                _DBUS_IFACE,
                "DownloadModel",
                GLib.Variant("(ss)", (self.current_provider, model_id)),
                None,
                Gio.DBusCallFlags.NONE,
                3000,
                None,
            )
        except Exception as e:
            _log.warning("Errore richiesta download D-Bus: %s", e)

    def _on_cancel_clicked(self, model_id: str) -> None:
        """Annulla il download in corso su richiesta dell'utente."""
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.call_sync(
                _DBUS_NAME,
                _DBUS_PATH,
                _DBUS_IFACE,
                "CancelDownload",
                GLib.Variant("(ss)", (self.current_provider, model_id)),
                None,
                Gio.DBusCallFlags.NONE,
                3000,
                None,
            )
        except Exception as e:
            _log.warning("Errore richiesta annullamento download D-Bus: %s", e)

        self._set_model_state(model_id, "available")

    def _on_delete_clicked(self, model_id: str) -> None:
        """Mostra finestra di conferma per l'eliminazione del modello e ne rimuove i file."""
        item = self._items.get(model_id)
        name = item["name"] if item else model_id
        is_active = item["is_active"] if item else False

        body = (
            f"Questo modello ('{name}') è attualmente in uso dall'assistente.\n"
            "Se eliminato, verrà ripristinato il modello predefinito.\n"
            "Vuoi procedere con la cancellazione permanente dal disco?"
            if is_active else
            f"Il modello '{name}' verrà rimosso permanentemente dal disco."
        )

        dlg = Adw.AlertDialog(
            heading=f"Eliminare '{name}'?",
            body=body,
        )
        dlg.add_response("cancel", "Annulla")
        dlg.add_response("delete", "Elimina")
        dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")
        dlg.set_close_response("cancel")

        def _do_delete(_dialog, response: str) -> None:
            if response != "delete":
                return

            # 1. Prova eliminazione via D-Bus
            try:
                bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                bus.call_sync(
                    _DBUS_NAME,
                    _DBUS_PATH,
                    _DBUS_IFACE,
                    "DeleteModel",
                    GLib.Variant("(ss)", (self.current_provider, model_id)),
                    None,
                    Gio.DBusCallFlags.NONE,
                    3000,
                    None,
                )
            except Exception as e:
                _log.info("D-Bus DeleteModel non disponibile (%s), fallback locale", e)

            # 2. Rimozione file dal filesystem
            self._delete_model_files(self.current_provider, model_id)

            # 3. Reset GSettings se il modello era attivo
            if self.settings and is_active:
                try:
                    if self.current_service in ("wakeword", "ww"):
                        if self.current_provider == "vosk":
                            self.settings.reset("vosk-ww-model")
                        elif self.current_provider in ("sherpa", "sherpa-onnx", "sherpa_onnx"):
                            self.settings.reset("sherpa-model")
                    elif self.current_service == "stt":
                        self.settings.reset("stt-model")
                    elif self.current_service == "llm":
                        self.settings.reset("llm-model")
                    elif self.current_service == "tts":
                        self.settings.reset("tts-voice")
                except Exception as e:
                    _log.warning("Errore reset impostazione GSettings: %s", e)

            # 4. Aggiorna lo stato della UI (riporta il modello a disponibile)
            self._set_model_state(model_id, "available", is_active=False)

            # 5. Sincronizza lo storage manager se presente
            if self.parent_window and hasattr(self.parent_window, "models_manager"):
                try:
                    self.parent_window.models_manager.refresh()
                except Exception:
                    pass

        dlg.connect("response", _do_delete)
        win = self.parent_window or (self.page.get_root() if self.page and hasattr(self.page, "get_root") else None)
        if win:
            dlg.present(win)
        else:
            # Fallback se la finestra principale non è disponibile (es. test senza GUI)
            _do_delete(dlg, "delete")

    def _on_row_activated(self, _row: Adw.ActionRow, model_id: str) -> None:
        item = self._items.get(model_id)
        if not item:
            return

        if item["state"] == "installed":
            # Seleziona il modello e chiudi la subpage
            if self.settings:
                try:
                    if self.current_service in ("wakeword", "ww"):
                        if self.current_provider == "vosk":
                            self.settings.set_string("vosk-ww-model", model_id)
                        elif self.current_provider in ("sherpa", "sherpa-onnx", "sherpa_onnx"):
                            self.settings.set_string("sherpa-model", model_id)
                    elif self.current_service == "stt":
                        self.settings.set_string("stt-model", model_id)
                    elif self.current_service == "llm":
                        clean_id = model_id.split(":")[-1] if (self.current_provider.lower() in ("gguf", "llama", "local") or model_id.endswith(".gguf")) else model_id
                        self.settings.set_string("llm-model", clean_id)
                    elif self.current_service == "tts":
                        self.settings.set_string("tts-voice", model_id)
                except Exception as e:
                    _log.warning("Errore salvataggio modello in GSettings: %s", e)

            # Aggiorna flag attivo su tutti gli item
            for mid, itm in self._items.items():
                itm["is_active"] = (mid == model_id)
                self._render_model_suffixes(itm)

            if self.on_selected_callback:
                try:
                    self.on_selected_callback(model_id)
                except Exception as e:
                    _log.warning("Errore in on_selected_callback: %s", e)

            win = self.parent_window or (self.page.get_root() if (self.page and hasattr(self.page, "get_root")) else None)
            if win and hasattr(win, "pop_subpage"):
                win.pop_subpage()
            elif self.nav_view:
                self.nav_view.pop()

        elif item["state"] == "available":
            # Avvia il download rimanendo nella pagina per mostrare la progress bar
            self._on_download_clicked(model_id)
        elif item["state"] == "downloading":
            # Già in download, non fare nulla
            pass

    def _on_cloud_row_activated(self, _row: Optional[Adw.ActionRow], model_id: str) -> None:
        """Seleziona immediatamente il modello cloud e chiude la pagina del selettore."""
        if self.current_service == "llm":
            try:
                self.cloud_config.set_provider_config("llm", self.current_provider, {"model": model_id})
            except Exception as e:
                _log.warning("Errore salvataggio modello in cloud_config: %s", e)

        if self.settings:
            try:
                self.settings.set_string("llm-model", model_id)
            except Exception as e:
                _log.warning("Errore salvataggio llm-model in GSettings: %s", e)

        if self.on_selected_callback:
            try:
                self.on_selected_callback(model_id)
            except Exception as e:
                _log.warning("Errore in on_selected_callback: %s", e)

        win = self.parent_window or (self.page.get_root() if (self.page and hasattr(self.page, "get_root")) else None)
        if win and hasattr(win, "pop_subpage"):
            win.pop_subpage()
        elif self.nav_view:
            self.nav_view.pop()

    def _show_custom_model_dialog(self, *_args) -> None:
        """Mostra un dialog per inserire manualmente l'ID di un modello personalizzato."""
        target_win = self.parent_window or (self.page.get_root() if self.page and hasattr(self.page, "get_root") else None)

        dlg = Adw.AlertDialog(
            heading="Modello Personalizzato",
            body="Inserisci il nome o l'ID del modello che desideri utilizzare:",
        )

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        listbox = Gtk.ListBox()
        listbox.add_css_class("boxed-list")
        entry_row = Adw.EntryRow(title="ID Modello")
        curr_m = (self.settings.get_string("llm-model") if self.settings else "") or ""
        entry_row.set_text(curr_m)
        listbox.append(entry_row)
        box.append(listbox)
        dlg.set_extra_child(box)

        dlg.add_response("cancel", "Annulla")
        dlg.add_response("apply", "Applica")
        dlg.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("apply")
        dlg.set_close_response("cancel")

        def _on_response(_d, response_id: str):
            if response_id != "apply":
                return
            custom_id = entry_row.get_text().strip()
            if not custom_id:
                return
            self._on_cloud_row_activated(None, custom_id)

        dlg.connect("response", _on_response)
        entry_row.connect("entry-activated", lambda *_: dlg.response("apply") if hasattr(dlg, "response") else None)

        if target_win:
            dlg.present(target_win)
        else:
            _on_response(dlg, "apply")

    def _show_custom_ollama_dialog(self, *_args) -> None:
        """Mostra un dialog per inserire manualmente il tag di un modello Ollama da scaricare o attivare."""
        target_win = self.parent_window or (self.page.get_root() if self.page and hasattr(self.page, "get_root") else None)

        dlg = Adw.AlertDialog(
            heading="Scarica Modello Ollama",
            body="Inserisci il tag del modello da scaricare dalla libreria Ollama (es. smollm:135m, llama3.1:8b):",
        )

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        listbox = Gtk.ListBox()
        listbox.add_css_class("boxed-list")
        entry_row = Adw.EntryRow(title="Tag Modello Ollama")
        entry_row.set_text("")
        listbox.append(entry_row)
        box.append(listbox)
        dlg.set_extra_child(box)

        dlg.add_response("cancel", "Annulla")
        dlg.add_response("download", "Scarica")
        dlg.set_response_appearance("download", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("download")
        dlg.set_close_response("cancel")

        def _on_response(_d, response_id: str):
            if response_id != "download":
                return
            custom_id = entry_row.get_text().strip()
            if not custom_id:
                return
            item = self._items.get(custom_id)
            if item and item.get("state") == "installed":
                self._on_row_activated(item.get("row_all"), custom_id)
            else:
                self._on_download_clicked(custom_id)

        dlg.connect("response", _on_response)
        entry_row.connect("entry-activated", lambda *_: dlg.response("download") if hasattr(dlg, "response") else None)

        if target_win:
            dlg.present(target_win)
        else:
            _on_response(dlg, "download")
