"""Provider bootstrap, model download and lifecycle helpers for the daemon."""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time

import notify2
from gi.repository import GLib

from core.daemon_protocol import DaemonOwner

# Intervallo minimo tra due emissioni di DownloadProgress a percentuale invariata: serve
# a far riallineare una GUI aperta mentre un download è già in corso.
PROGRESS_HEARTBEAT_SEC = 1.0

logger = logging.getLogger("VoiceAssistant.ProviderManager")


class ProviderManager:
    """Encapsulates STT provider lifecycle and model download management."""

    def __init__(self, owner: DaemonOwner):
        self.owner = owner

    @property
    def model_registry(self):
        from core.model_registry import get_model_registry
        models_dir = getattr(self.owner, "models_dir", None)
        return get_model_registry(models_dir=models_dir)

    def _show_notification(self, notif):
        if notif:
            try:
                notif.set_hint_string("desktop-entry", "org.local.VoiceAssistant.GUI")
            except Exception:
                pass
            notif.show()
        return False

    def has_installed_models(self) -> bool:
        from core.path_utils import get_models_dir
        target_dir = str(get_models_dir(getattr(self.owner, 'models_dir', '')))
        if not os.path.exists(target_dir):
            return False
        try:
            entries = [e for e in os.listdir(target_dir) if not e.startswith('.')]
            return len(entries) > 0
        except Exception:
            return False

    def load_provider(self, load_id):
        local_provider_name = self.owner.provider_name
        local_model_name = self.owner.model_name
        local_hardware = self.owner.hardware
        local_extra = dict(self.owner.extra_config or {})
        settings_obs = getattr(self.owner, '_settings_observer', None)
        if settings_obs:
            from core.locale_utils import get_system_language
            local_extra["api_key"] = settings_obs.get("llm-api-key", "")
            obs_lang = settings_obs.get("language", "")
            local_extra["language"] = obs_lang if (obs_lang and obs_lang.strip()) else (getattr(self.owner, "language", "") or get_system_language())
        elif not local_extra.get("language"):
            from core.locale_utils import get_system_language
            local_extra["language"] = getattr(self.owner, "language", "") or get_system_language()

        if local_provider_name in ("openai_cloud", "groq_cloud"):
            local_extra["provider"] = local_provider_name
            try:
                from core.cloud_config import get_cloud_config
                c_cfg = get_cloud_config().get_provider_config("stt", local_provider_name)
                if c_cfg.get("api_key"):
                    local_extra["api_key"] = c_cfg["api_key"]
                if c_cfg.get("endpoint"):
                    local_extra["endpoint"] = c_cfg["endpoint"]
            except Exception:
                pass

        local_models_dir = getattr(self.owner, 'models_dir', '')
        key_str = f"{local_provider_name}:{local_model_name}"
        model_key = (local_provider_name, local_model_name)
        if hasattr(self.owner, '_cancel_requests'):
            self.owner._cancel_requests.discard(key_str)

        logger.info(f"Caricamento del provider STT '{local_provider_name}'...")

        if load_id == getattr(self.owner, '_load_id', 0):
            if not self.has_installed_models():
                GLib.idle_add(self.owner.set_state, "downloading")
            if key_str not in self.owner._downloading_models:
                self.owner._downloading_models[key_str] = 0

        if not hasattr(self.owner, '_active_notifs'):
            self.owner._active_notifs = {}

        if model_key in self.owner._active_notifs:
            notif = self.owner._active_notifs[model_key]
        else:
            try:
                notif = notify2.Notification("Voice Assistant", f"Inizializzazione {local_provider_name} ({local_model_name})...", "vocal-assistant-icon")
                try:
                    notif.set_hint_string("desktop-entry", "org.local.VoiceAssistant.GUI")
                except Exception:
                    pass
                notif._is_closed = False

                def on_closed(n):
                    n._is_closed = True

                notif.connect('closed', on_closed)
                self.owner._active_notifs[model_key] = notif
            except Exception as notif_err:
                logger.warning(f"Impossibile creare notifica per inizializzazione: {notif_err}")
                notif = None

        download_started = [False]
        last_percent = [None]
        last_emit = [0.0]

        def progress_cb(percent: int):
            key = f"{local_provider_name}:{local_model_name}"
            if hasattr(self.owner, '_cancel_requests') and key in self.owner._cancel_requests:
                raise InterruptedError("Scaricamento annullato dall'utente")

            try:
                percent = int(percent)
            except (TypeError, ValueError):
                return

            # I downloader invocano questo callback a ogni blocco letto, quindi molte volte
            # per lo stesso valore percentuale. La notifica viene riscritta solo quando il
            # numero mostrato cambia davvero; il segnale D-Bus mantiene invece un battito
            # minimo, perché è così che la GUI aperta a download già avviato si riallinea
            # (su file grandi e connessioni lente un punto percentuale può richiedere minuti).
            percent_changed = percent != last_percent[0]
            now = time.monotonic()
            if not percent_changed and (now - last_emit[0]) < PROGRESS_HEARTBEAT_SEC:
                return
            last_percent[0] = percent
            last_emit[0] = now

            if percent >= 0 and percent < 100:
                self.owner._downloading_models[key] = percent
            else:
                self.owner._downloading_models.pop(key, None)

            self.owner.emit_download_progress(local_provider_name, local_model_name, percent)

            if notif and percent_changed:
                download_started[0] = True
                if getattr(notif, '_is_closed', False):
                    notif.id = 0
                    notif._is_closed = False

                # Senza EXPIRES_NEVER la notifica di avanzamento si affidava al fatto di
                # essere rimostrata di continuo per restare visibile.
                notif.set_timeout(notify2.EXPIRES_NEVER)
                notif.update("Voice Assistant", f"Scaricamento {local_provider_name} ({local_model_name}): {percent}%", "vocal-assistant-icon")
                GLib.idle_add(self._show_notification, notif)

        try:
            from providers import get_provider
            new_provider = get_provider(
                local_provider_name,
                local_model_name,
                local_hardware,
                local_extra,
                progress_cb,
                models_dir=local_models_dir,
            )
            logger.info(f"Provider {local_provider_name} inizializzato.")

            if notif and download_started[0]:
                notif.set_timeout(notify2.EXPIRES_NEVER)
                notif.update("Voice Assistant", f"{local_provider_name} ({local_model_name}) pronto!", "emblem-ok-symbolic")
                notif._is_closed = False
                GLib.idle_add(self._show_notification, notif)

            self.owner._downloading_models.pop(key_str, None)

            if (local_provider_name, local_model_name) in getattr(self.owner, '_active_notifs', {}):
                self.owner._active_notifs.pop((local_provider_name, local_model_name), None)

            if load_id == getattr(self.owner, '_load_id', 0):
                self.owner.provider = new_provider
                model_manager = getattr(self.owner, "model_manager", None)
                if model_manager:
                    model_manager.register_instance(
                        "stt",
                        new_provider,
                        lambda: setattr(self.owner, "provider", None),
                    )
                pending_state = getattr(self.owner, "_pending_state_after_provider_load", None)
                self.owner._pending_state_after_provider_load = None
                self.owner._stt_load_pending = False
                is_enabled = self.owner.settings.get_boolean("enabled")
                next_state = pending_state if is_enabled and pending_state else ("idle" if is_enabled else "disabled")
                GLib.idle_add(self.owner.set_state, next_state)
                return new_provider
            else:
                logger.info(f"Download di {local_provider_name} ({local_model_name}) completato in background, ma l'utente ha selezionato un altro modello nel frattempo.")
                return new_provider
        except Exception as e:
            is_cancelled = hasattr(self.owner, '_cancel_requests') and key_str in self.owner._cancel_requests
            if hasattr(self.owner, '_cancel_requests'):
                self.owner._cancel_requests.discard(key_str)
            self.owner._downloading_models.pop(key_str, None)
            self.owner.emit_download_progress(local_provider_name, local_model_name, -1)

            if is_cancelled:
                self.cleanup_partial_download(local_provider_name, local_model_name)

            logger.error(f"Errore caricamento provider STT: {e}", exc_info=True)
            if notif:
                msg = f"Scaricamento di {local_model_name} annullato" if is_cancelled else f"Errore caricamento: {e}"
                icon = "dialog-warning-symbolic" if is_cancelled else "dialog-error-symbolic"
                notif.update("Voice Assistant", msg, icon)
                GLib.idle_add(self._show_notification, notif)
            if load_id == getattr(self.owner, '_load_id', 0):
                self.owner._stt_load_pending = False
                is_enabled = self.owner.settings.get_boolean("enabled")
                GLib.idle_add(self.owner.set_state, "idle" if is_enabled else "disabled")

    def get_installed_models(self, provider: str = "") -> str:
        """Ritorna la lista JSON dei modelli installati memorizzati nel manifest."""
        models = self.model_registry.get_installed_models(provider or None)
        return json.dumps(models)

    def get_available_models(self, provider: str) -> str:
        p = provider.lower()
        if p.startswith("llm") or p in ("gguf", "llama", "local"):
            installed_models = self.model_registry.get_installed_models("gguf")
            installed_files = {}
            for im in installed_models:
                m_fn = im.get("filename") or im.get("id") or ""
                clean_name = im.get("name") or m_fn.replace(".gguf", "").replace("-GGUF", "").replace("-", " ")
                installed_files[m_fn.lower()] = {
                    "name": m_fn,
                    "id": im.get("id", m_fn),
                    "clean_name": clean_name,
                    "size_text": im.get("size_text", ""),
                }

            from services.catalog_manager import CURATED_GGUF_MODELS
            curated_gguf = CURATED_GGUF_MODELS

            models = []
            seen_files = set()
            seen_ids = set()

            for cm in curated_gguf:
                c_file = cm["file"].lower()
                c_stem = c_file[:-5] if c_file.endswith(".gguf") else c_file
                is_inst = c_file in installed_files or c_stem in installed_files
                item = dict(cm)
                if is_inst:
                    inst_info = installed_files.get(c_file) or installed_files.get(c_stem)
                    if inst_info:
                        item["size_text"] = inst_info["size_text"]
                    item["installed"] = True
                    models.append(item)
                    seen_files.add(c_file)
                    seen_files.add(c_stem)
                    seen_ids.add(item["id"].lower())

            for f_low, f_info in installed_files.items():
                f_stem = f_low[:-5] if f_low.endswith(".gguf") else f_low
                if f_low not in seen_files and f_stem not in seen_files:
                    models.append({
                        "id": f_info["name"],
                        "provider": "llm",
                        "name": f_info["clean_name"],
                        "subtitle": f"Locale • {f_info['size_text']}",
                        "file": f_info["name"],
                        "size_text": f_info["size_text"],
                        "installed": True,
                    })
                    seen_files.add(f_low)
                    seen_files.add(f_stem)
                    seen_ids.add(f_info["name"].lower())

            for cm in curated_gguf:
                if cm["id"].lower() not in seen_ids:
                    item = dict(cm)
                    item["installed"] = False
                    models.append(item)
                    seen_ids.add(item["id"].lower())

            try:
                from services.llm_service import fetch_huggingface_models
                query = ""
                if ":" in provider:
                    query = provider.split(":", 1)[1]
                hf_models = fetch_huggingface_models(query=query)
                for hm in hf_models:
                    hid = hm.get("id", "").lower()
                    hfile = hm.get("file", "").lower()
                    hstem = hfile[:-5] if hfile.endswith(".gguf") else hfile
                    if hid not in seen_ids and hfile not in seen_files:
                        is_inst = hfile in installed_files or hstem in installed_files
                        item = dict(hm)
                        item["installed"] = is_inst
                        if is_inst:
                            inst_info = installed_files.get(hfile) or installed_files.get(hstem)
                            if inst_info and inst_info.get("size_text"):
                                item["size_text"] = inst_info["size_text"]
                        models.append(item)
                        seen_ids.add(hid)
            except Exception as e:
                logger.info(f"[GGUF] Errore fetch Hugging Face models ({e}), uso modelli curati e locali")

            return json.dumps(models)

        if p == "ollama":
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
            if hasattr(self.owner, "settings") and self.owner.settings:
                try:
                    endpoint = self.owner.settings.get_string("llm-endpoint") or endpoint
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
                logger.info(f"[Ollama] Impossibile contattare {endpoint}/api/tags ({e}), uso catalogo standard")

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
                is_inst = mid in installed_tags
                if mid not in seen_ids:
                    item = dict(cm)
                    item["installed"] = is_inst
                    models.append(item)
                    seen_ids.add(mid)

            return json.dumps(models)

        user_lang = None
        if hasattr(self.owner, 'settings') and self.owner.settings:
            try:
                user_lang = self.owner.settings.get_string("language")
            except Exception:
                pass
        if not user_lang:
            try:
                from core.locale_utils import get_system_language
                user_lang = get_system_language()
            except Exception:
                pass

        if p in ("tts", "piper"):
            from services.tts_service import PiperTTSProvider
            voices = PiperTTSProvider.get_available_voices(user_lang=user_lang)
            return json.dumps(voices)

        if provider.lower() in ("sherpa", "sherpa-onnx", "sherpa_onnx"):
            from services.catalog_manager import catalog_service
            base_dir = getattr(self.owner, 'models_dir', '')
            from core.path_utils import get_models_dir
            models_dir = str(get_models_dir(base_dir))
            models = catalog_service.get_sherpa_models(user_lang=user_lang, models_dir=models_dir)
            return json.dumps(models)

        from providers import get_available_models
        models = get_available_models(provider, user_lang=user_lang)
        return json.dumps(models)

    def cleanup_partial_download(self, provider: str, model_name: str):
        try:
            from core.path_utils import get_models_dir, get_stt_models_dir, get_tts_models_dir, get_llm_models_dir
            base_dir = getattr(self.owner, 'models_dir', '')
            stt_dir = str(get_stt_models_dir(base_dir))
            tts_dir = str(get_tts_models_dir(base_dir))
            llm_dir = str(get_llm_models_dir(base_dir))
            target_dir = str(get_models_dir(base_dir))

            possible_folders = [
                os.path.join(target_dir, model_name),
                os.path.join(target_dir, f"vosk-model-{model_name}"),
                os.path.join(target_dir, f"whisper-{model_name}"),
                os.path.join(stt_dir, model_name),
                os.path.join(stt_dir, f"vosk-model-{model_name}"),
                os.path.join(stt_dir, f"whisper-{model_name}"),
                os.path.join(target_dir, "wakeword", model_name),
                os.path.join(target_dir, "sherpa", model_name),
                os.path.expanduser(f"~/.cache/vosk/{model_name}"),
            ]
            possible_files = [
                os.path.join(target_dir, f"{model_name}.zip"),
                os.path.join(target_dir, f"{model_name}.zip.part"),
                os.path.join(target_dir, f"vosk-model-{model_name}.zip"),
                os.path.join(target_dir, f"vosk-model-{model_name}.zip.part"),
                os.path.join(stt_dir, f"{model_name}.zip"),
                os.path.join(stt_dir, f"{model_name}.zip.part"),
                os.path.join(stt_dir, f"vosk-model-{model_name}.zip"),
                os.path.join(stt_dir, f"vosk-model-{model_name}.zip.part"),
                os.path.join(tts_dir, f"{model_name}.onnx"),
                os.path.join(tts_dir, f"{model_name}.onnx.tmp"),
                os.path.join(tts_dir, f"{model_name}.onnx.json"),
                os.path.join(tts_dir, f"{model_name}.onnx.json.tmp"),
                os.path.join(llm_dir, f"{model_name}.tmp"),
                os.path.join(llm_dir, f"{model_name}.part"),
            ]
            for folder in possible_folders:
                if os.path.exists(folder) and os.path.isdir(folder):
                    logger.info(f"[Cleanup] Rimozione cartella incompleta per annullamento: {folder}")
                    shutil.rmtree(folder, ignore_errors=True)
            for f in possible_files:
                if os.path.exists(f) and os.path.isfile(f):
                    logger.info(f"[Cleanup] Rimozione file incompleto per annullamento: {f}")
                    try:
                        os.remove(f)
                    except Exception:
                        pass
        except Exception as clean_err:
            logger.error(f"Errore pulizia download annullato: {clean_err}")

    def download_model(self, provider: str, model_name: str) -> bool:
        def _download_thread():
            key = f"{provider}:{model_name}"
            if hasattr(self.owner, '_cancel_requests'):
                self.owner._cancel_requests.discard(key)
            self.owner._downloading_models[key] = 0
            self.owner.emit_download_progress(provider, model_name, 0)

            logger.info(f"[D-Bus] Avvio scaricamento modello: provider={provider}, model={model_name}")
            self.owner._inhibitor.inhibit(f"Scaricamento modello {model_name} in corso")

            notif = None
            try:
                notif = notify2.Notification("Voice Assistant", f"Inizio scaricamento {provider} ({model_name})...", "vocal-assistant-icon")
                try:
                    notif.set_hint_string("desktop-entry", "org.local.VoiceAssistant.GUI")
                except Exception:
                    pass
                notif.set_timeout(notify2.EXPIRES_NEVER)
                notif._is_closed = False

                def on_closed(n):
                    n._is_closed = True

                notif.connect('closed', on_closed)
                GLib.idle_add(self._show_notification, notif)
            except Exception as e:
                logger.warning(f"Impossibile creare notifica per download: {e}")

            last_percent = [0]
            last_emit = [0.0]

            def progress_cb(percent: int, *args):
                if args:
                    if isinstance(percent, str) and isinstance(args[0], (int, float)):
                        percent = int(args[0])
                    elif isinstance(args[0], (int, float)):
                        percent = int(args[0])

                if hasattr(self.owner, '_cancel_requests') and key in self.owner._cancel_requests:
                    raise InterruptedError("Scaricamento annullato dall'utente")

                try:
                    percent = int(percent)
                except (TypeError, ValueError):
                    return

                # Vedi progress_cb in load_provider: notifica solo a percentuale cambiata,
                # segnale D-Bus con un battito minimo per tenere allineata la GUI.
                percent_changed = percent != last_percent[0]
                now = time.monotonic()
                if not percent_changed and (now - last_emit[0]) < PROGRESS_HEARTBEAT_SEC:
                    return
                last_percent[0] = percent
                last_emit[0] = now

                self.owner._downloading_models[key] = percent
                self.owner.emit_download_progress(provider, model_name, percent)
                if notif and percent_changed:
                    if getattr(notif, '_is_closed', False):
                        notif.id = 0
                        notif._is_closed = False
                    notif.set_timeout(notify2.EXPIRES_NEVER)
                    notif.update("Voice Assistant", f"Scaricamento {provider} ({model_name}): {percent}%", "vocal-assistant-icon")
                    GLib.idle_add(self._show_notification, notif)

            try:
                if provider.lower() in ("llm", "gguf", "llama"):
                    from services.llm_service import download_llm_model
                    from core.path_utils import get_llm_models_dir
                    llm_dir = str(get_llm_models_dir(getattr(self.owner, 'models_dir', '')))
                    target_path = download_llm_model(model_name, progress_callback=progress_cb, models_dir=llm_dir)
                    fname = os.path.basename(target_path) if target_path else model_name.split(":")[-1]
                    clean_name = fname.replace(".gguf", "").replace("-GGUF", "").replace("-", " ")
                    self.model_registry.register_model(
                        provider="gguf",
                        model_id=fname,
                        name=clean_name,
                        service="llm",
                        path=target_path,
                        files=[target_path] if target_path else [],
                        extra={"catalog_id": model_name, "filename": fname}
                    )
                elif provider.lower() == "ollama":
                    self.download_ollama_model(model_name, progress_cb)
                    self.model_registry.register_model(
                        provider="ollama",
                        model_id=model_name,
                        name=model_name,
                        service="llm",
                        path="",
                        files=[],
                    )
                elif provider.lower() in ("tts", "piper"):
                    from services.tts_service import PiperTTSProvider
                    from core.path_utils import get_tts_models_dir
                    tts_dir = str(get_tts_models_dir(getattr(self.owner, 'models_dir', '')))
                    provider_inst = PiperTTSProvider(models_dir=tts_dir)
                    onnx_path, json_path = provider_inst.ensure_voice_downloaded(model_name, progress_callback=progress_cb)
                    self.model_registry.register_model(
                        provider="piper",
                        model_id=model_name,
                        name=model_name,
                        service="tts",
                        path=str(onnx_path),
                        files=[str(onnx_path), str(json_path)],
                    )
                elif provider.lower() in ("sherpa", "sherpa-onnx", "sherpa_onnx"):
                    self.download_sherpa_model(model_name, progress_cb)
                    from core.path_utils import get_models_dir
                    models_base = str(get_models_dir(getattr(self.owner, 'models_dir', '')))
                    sherpa_dir = os.path.join(models_base, model_name)
                    self.model_registry.register_model(
                        provider="sherpa-onnx",
                        model_id=model_name,
                        name=model_name,
                        service="wakeword",
                        path=sherpa_dir,
                        files=[sherpa_dir],
                    )
                else:
                    from providers import get_provider
                    get_provider(
                        provider,
                        model_name,
                        self.owner.hardware,
                        self.owner.extra_config,
                        progress_cb,
                        models_dir=self.owner.models_dir,
                        download_only=True,
                    )
                    from core.path_utils import get_stt_models_dir, get_models_dir
                    base_dir = getattr(self.owner, 'models_dir', '')
                    stt_dir = str(get_stt_models_dir(base_dir))
                    m_dir = str(get_models_dir(base_dir))
                    target_path = ""
                    for cand in [
                        os.path.join(stt_dir, model_name),
                        os.path.join(stt_dir, f"whisper-{model_name}"),
                        os.path.join(stt_dir, f"models--Systran--faster-whisper-{model_name}"),
                        os.path.join(m_dir, model_name),
                    ]:
                        if os.path.exists(cand):
                            target_path = cand
                            break
                    self.model_registry.register_model(
                        provider=provider,
                        model_id=model_name,
                        name=model_name,
                        service="stt",
                        path=target_path,
                        files=[target_path] if target_path else [],
                    )
                logger.info(f"[D-Bus] Scaricamento completato e registrato in ModelRegistry: {provider} ({model_name})")
                self.owner._downloading_models.pop(key, None)
                self.owner.emit_download_progress(provider, model_name, 100)
                if notif:
                    notif.set_timeout(notify2.EXPIRES_NEVER)
                    notif.update("Voice Assistant", f"Modello {model_name} scaricato con successo!", "vocal-assistant-icon")
                    notif._is_closed = False
                    GLib.idle_add(self._show_notification, notif)
            except Exception as e:
                is_cancelled = hasattr(self.owner, '_cancel_requests') and key in self.owner._cancel_requests
                if hasattr(self.owner, '_cancel_requests'):
                    self.owner._cancel_requests.discard(key)
                self.owner._downloading_models.pop(key, None)
                self.owner.emit_download_progress(provider, model_name, -1)

                if is_cancelled:
                    self.cleanup_partial_download(provider, model_name)

                logger.error(f"[D-Bus] Scaricamento modello {model_name} terminato: {e}")
                if notif:
                    msg = f"Scaricamento di {model_name} annullato" if is_cancelled else f"Errore scaricamento {model_name}: {e}"
                    icon = "dialog-warning-symbolic" if is_cancelled else "dialog-error-symbolic"
                    notif.set_timeout(5000)
                    notif.update("Voice Assistant", msg, icon)
                    GLib.idle_add(self._show_notification, notif)
            finally:
                self.owner._inhibitor.uninhibit()

        threading.Thread(target=_download_thread, daemon=True).start()
        return True

    def download_ollama_model(self, model_name: str, progress_cb=None):
        import urllib.request
        logger.info(f"[OllamaDownload] Avvio pull {model_name}...")
        endpoint = "http://localhost:11434"
        if hasattr(self.owner, "settings") and self.owner.settings:
            try:
                endpoint = self.owner.settings.get_string("llm-endpoint") or endpoint
            except Exception:
                pass
        endpoint = endpoint.rstrip("/")
        if endpoint.endswith("/v1"):
            endpoint = endpoint[:-3]
        elif endpoint.endswith("/v1/chat/completions"):
            endpoint = endpoint[:-20]

        url = f"{endpoint}/api/pull"
        data = json.dumps({"name": model_name}).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")

        layer_progress: dict[str, tuple[int, int]] = {}
        last_pct = -1
        success_seen = False

        with urllib.request.urlopen(req, timeout=3600) as resp:
            for line in resp:
                if not line:
                    continue
                line_str = line.decode("utf-8", errors="replace").strip()
                if not line_str:
                    continue
                try:
                    ev = json.loads(line_str)
                except Exception:
                    continue

                if "error" in ev:
                    raise RuntimeError(f"Ollama pull error: {ev['error']}")

                if ev.get("status") == "success":
                    success_seen = True

                digest = ev.get("digest")
                total = ev.get("total", 0)
                completed = ev.get("completed", 0)
                if digest and total > 0:
                    layer_progress[digest] = (completed, total)
                    tot_completed = sum(c for c, _ in layer_progress.values())
                    tot_bytes = sum(t for _, t in layer_progress.values())
                    if tot_bytes > 0 and progress_cb:
                        pct = min(99, int((tot_completed / tot_bytes) * 100))
                        if pct != last_pct:
                            last_pct = pct
                            progress_cb(pct)

        if not success_seen:
            # Verifica su /api/tags se il modello è effettivamente presente
            tags_found = False
            try:
                req_tags = urllib.request.Request(f"{endpoint}/api/tags", headers={"User-Agent": "VoiceAssistant/1.0"})
                with urllib.request.urlopen(req_tags, timeout=2.0) as tag_resp:
                    tags_data = json.loads(tag_resp.read().decode("utf-8"))
                    for m in tags_data.get("models", []):
                        t_name = m.get("name", "").lower()
                        if t_name == model_name.lower() or t_name == f"{model_name.lower()}:latest":
                            tags_found = True
                            break
            except Exception:
                pass
            if not tags_found:
                raise RuntimeError(f"Ollama pull per '{model_name}' non completato o interrotto.")

        if progress_cb:
            progress_cb(100)
        logger.info(f"[OllamaDownload] Pull {model_name} completato con successo.")

    def download_sherpa_model(self, model_name: str, progress_cb=None):
        import urllib.request
        import tarfile
        import tempfile
        from core.path_utils import get_models_dir

        models_base = str(get_models_dir(getattr(self.owner, 'models_dir', '')))
        os.makedirs(models_base, exist_ok=True)
        url = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/{model_name}.tar.bz2"
        logger.info(f"[SherpaDownload] Avvio download {url}")
        tmp_archive = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".tar.bz2", delete=False) as tmp:
                tmp_archive = tmp.name

            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko)"}
            )
            with urllib.request.urlopen(req) as resp, open(tmp_archive, "wb") as out_f:
                total_size = resp.headers.get("Content-Length")
                total_bytes = int(total_size) if total_size and total_size.isdigit() else 0
                downloaded = 0
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    out_f.write(chunk)
                    downloaded += len(chunk)
                    if total_bytes > 0:
                        pct = min(95, int(downloaded * 95 / total_bytes))
                        if progress_cb:
                            progress_cb(pct)

            logger.info(f"[SherpaDownload] Estrazione archivio {tmp_archive} in {models_base}...")
            with tarfile.open(tmp_archive, "r:bz2") as tar:
                if hasattr(tarfile, 'data_filter'):
                    tar.extractall(path=models_base, filter='data')
                else:
                    tar.extractall(path=models_base)
            if progress_cb:
                progress_cb(100)
            logger.info(f"[SherpaDownload] Modello {model_name} estratto con successo.")
        finally:
            if tmp_archive and os.path.isfile(tmp_archive):
                try:
                    os.unlink(tmp_archive)
                except OSError:
                    pass

    def cancel_download(self, provider: str, model_name: str) -> bool:
        key = f"{provider}:{model_name}"
        logger.info(f"[D-Bus] Richiesta annullamento scaricamento per {key}")
        if not hasattr(self.owner, '_cancel_requests'):
            self.owner._cancel_requests = set()
        self.owner._cancel_requests.add(key)
        self.owner._downloading_models.pop(key, None)
        self.owner.emit_download_progress(provider, model_name, -1)

        self.cleanup_partial_download(provider, model_name)

        try:
            notif = notify2.Notification("Voice Assistant", f"Scaricamento di {model_name} annullato", "vocal-assistant-icon")
            try:
                notif.set_hint_string("desktop-entry", "org.local.VoiceAssistant.GUI")
            except Exception:
                pass
            notif.set_timeout(4000)
            GLib.idle_add(self._show_notification, notif)
        except Exception as e:
            logger.warning(f"Errore notifica annullamento: {e}")

        return True

    def delete_model(self, provider: str, model_name: str) -> bool:
        """Elimina un modello scaricato dal disco e scarica la risorsa se attiva."""
        p_lower = provider.lower()
        m_lower = model_name.lower()
        m_base = model_name.split("/")[-1].lower()
        m_file = model_name.split(":")[-1].lower() if ":" in model_name else m_base
        m_file_stem = m_file[:-5] if m_file.endswith(".gguf") else (m_file[:-4] if m_file.endswith(".bin") else m_file)
        logger.info(f"[ProviderManager] Richiesta eliminazione modello: provider={provider}, model={model_name}")

        if p_lower == "ollama":
            deleted = False
            try:
                endpoint = "http://localhost:11434"
                if hasattr(self.owner, "settings") and self.owner.settings:
                    try:
                        endpoint = self.owner.settings.get_string("llm-endpoint") or endpoint
                    except Exception:
                        pass
                endpoint = endpoint.rstrip("/")
                if endpoint.endswith("/v1"):
                    endpoint = endpoint[:-3]
                elif endpoint.endswith("/v1/chat/completions"):
                    endpoint = endpoint[:-20]

                import urllib.request
                url = f"{endpoint}/api/delete"
                data = json.dumps({"name": model_name}).encode("utf-8")
                req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="DELETE")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    logger.info(f"[DeleteModel] Modello Ollama {model_name} eliminato con successo: {resp.status}")
                    deleted = True
            except Exception as e:
                logger.warning(f"[DeleteModel] Errore eliminazione modello Ollama {model_name}: {e}")

            try:
                self.model_registry.delete_model(provider, model_name)
            except Exception:
                pass
            return deleted

        try:
            # Scarica dalle risorse attive se in uso
            if p_lower in ("tts", "piper") and hasattr(self.owner, "tts_manager"):
                try:
                    self.owner.tts_manager.unload_voice()
                except Exception:
                    pass

            if p_lower in ("stt", "vosk", "whisper") and hasattr(self.owner, "stt_provider"):
                try:
                    current_model = getattr(self.owner.stt_provider, "model_name", "")
                    if m_lower in current_model.lower() or current_model.lower() in m_lower:
                        self.owner.stt_provider = None
                except Exception:
                    pass

            if p_lower in ("llm", "gguf") and hasattr(self.owner, "llm_service"):
                try:
                    if hasattr(self.owner.llm_service, "unload_model"):
                        self.owner.llm_service.unload_model()
                except Exception:
                    pass

            if (p_lower in ("wakeword", "ww", "vosk") or "vosk" in m_lower) and hasattr(self.owner, "ww_provider") and self.owner.ww_provider:
                try:
                    current_ww = getattr(self.owner.ww_provider, "model_name", "") or getattr(self.owner, "vosk_ww_model", "")
                    if m_lower in current_ww.lower() or current_ww.lower() in m_lower:
                        self.owner.ww_provider = None
                except Exception:
                    pass

            if p_lower in ("sherpa", "sherpa-onnx", "sherpa_onnx", "wakeword") and hasattr(self.owner, "sherpa_spotter"):
                try:
                    self.owner.sherpa_spotter = None
                    self.owner.sherpa_stream = None
                except Exception:
                    pass

            # Rimuovi file e cartelle via ModelRegistry
            deleted = self.model_registry.delete_model(provider, model_name)
            logger.info(f"[DeleteModel] Eliminazione completata da ModelRegistry per {provider}:{model_name} (successo: {deleted})")
            return True
        except Exception as e:
            logger.error(f"[DeleteModel] Errore durante l'eliminazione di {model_name}: {e}", exc_info=True)
            return False
