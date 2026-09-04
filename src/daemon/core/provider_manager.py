"""Provider bootstrap, model download and lifecycle helpers for the daemon."""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading

import notify2
from gi.repository import GLib

from core.daemon_protocol import DaemonOwner

logger = logging.getLogger("VoiceAssistant.ProviderManager")


class ProviderManager:
    """Encapsulates STT provider lifecycle and model download management."""

    def __init__(self, owner: DaemonOwner):
        self.owner = owner

    def _show_notification(self, notif):
        if notif:
            notif.show()
        return False

    def has_installed_models(self) -> bool:
        target_dir = getattr(self.owner, 'models_dir', '')
        if not target_dir or target_dir.strip() == "":
            target_dir = os.path.expanduser("~/.local/share/voice-assistant/models")
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
            local_extra["api_key"] = settings_obs.get("llm-api-key", "")
            local_extra["language"] = settings_obs.get("language", "it")

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
                notif = notify2.Notification("Voice Assistant", f"Inizializzazione {local_provider_name} ({local_model_name})...", "system-run-symbolic")
                notif._is_closed = False

                def on_closed(n):
                    n._is_closed = True

                notif.connect('closed', on_closed)
                self.owner._active_notifs[model_key] = notif
            except Exception as notif_err:
                logger.warning(f"Impossibile creare notifica per inizializzazione: {notif_err}")
                notif = None

        download_started = [False]

        def progress_cb(percent: int):
            key = f"{local_provider_name}:{local_model_name}"
            if hasattr(self.owner, '_cancel_requests') and key in self.owner._cancel_requests:
                raise InterruptedError("Scaricamento annullato dall'utente")

            if percent >= 0 and percent < 100:
                self.owner._downloading_models[key] = percent
            else:
                self.owner._downloading_models.pop(key, None)

            self.owner.emit_download_progress(local_provider_name, local_model_name, percent)

            if notif:
                download_started[0] = True
                if getattr(notif, '_is_closed', False):
                    notif.id = 0
                    notif._is_closed = False

                notif.update("Voice Assistant", f"Scaricamento {local_provider_name} ({local_model_name}): {percent}%", "folder-download-symbolic")
                try:
                    notif.show()
                except Exception:
                    pass

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

    def get_available_models(self, provider: str) -> str:
        if provider.lower().startswith("llm") or provider.lower() in ("gguf", "llama"):
            from services.llm_service import fetch_huggingface_models
            query = ""
            if ":" in provider:
                query = provider.split(":", 1)[1]
            models = fetch_huggingface_models(query=query)
            return json.dumps(models)

        from providers import get_available_models
        models = get_available_models(provider)
        return json.dumps(models)

    def cleanup_partial_download(self, provider: str, model_name: str):
        try:
            target_dir = getattr(self.owner, 'models_dir', '') or os.path.expanduser("~/.local/share/voice-assistant/models")
            possible_folders = [
                os.path.join(target_dir, model_name),
                os.path.join(target_dir, f"vosk-model-{model_name}"),
                os.path.join(target_dir, f"whisper-{model_name}"),
            ]
            possible_zips = [
                os.path.join(target_dir, f"{model_name}.zip"),
                os.path.join(target_dir, f"vosk-model-{model_name}.zip"),
            ]
            for folder in possible_folders:
                if os.path.exists(folder):
                    logger.info(f"[Cleanup] Rimozione cartella incompleta per annullamento: {folder}")
                    shutil.rmtree(folder, ignore_errors=True)
            for zip_file in possible_zips:
                if os.path.exists(zip_file):
                    logger.info(f"[Cleanup] Rimozione file zip incompleto per annullamento: {zip_file}")
                    try:
                        os.remove(zip_file)
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
                notif = notify2.Notification("Voice Assistant", f"Inizio scaricamento {provider} ({model_name})...", "folder-download-symbolic")
                notif.set_timeout(notify2.EXPIRES_NEVER)
                notif._is_closed = False

                def on_closed(n):
                    n._is_closed = True

                notif.connect('closed', on_closed)
                GLib.idle_add(self._show_notification, notif)
            except Exception as e:
                logger.warning(f"Impossibile creare notifica per download: {e}")

            def progress_cb(percent: int):
                if hasattr(self.owner, '_cancel_requests') and key in self.owner._cancel_requests:
                    raise InterruptedError("Scaricamento annullato dall'utente")

                self.owner._downloading_models[key] = percent
                self.owner.emit_download_progress(provider, model_name, percent)
                if notif:
                    if getattr(notif, '_is_closed', False):
                        notif.id = 0
                        notif._is_closed = False
                    notif.set_timeout(notify2.EXPIRES_NEVER)
                    notif.update("Voice Assistant", f"Scaricamento {provider} ({model_name}): {percent}%", "folder-download-symbolic")
                    GLib.idle_add(self._show_notification, notif)

            try:
                if provider.lower() in ("llm", "gguf", "llama"):
                    from services.llm_service import download_llm_model
                    llm_dir = os.path.join(getattr(self.owner, 'models_dir', '') or os.path.expanduser("~/.local/share/voice-assistant/models"), "llm")
                    download_llm_model(model_name, progress_callback=progress_cb, models_dir=llm_dir)
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
                logger.info(f"[D-Bus] Scaricamento completato: {provider} ({model_name})")
                self.owner._downloading_models.pop(key, None)
                self.owner.emit_download_progress(provider, model_name, 100)
                if notif:
                    notif.set_timeout(notify2.EXPIRES_NEVER)
                    notif.update("Voice Assistant", f"Modello {model_name} scaricato con successo!", "emblem-ok-symbolic")
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
            notif = notify2.Notification("Voice Assistant", f"Scaricamento di {model_name} annullato", "dialog-warning-symbolic")
            notif.set_timeout(4000)
            GLib.idle_add(self._show_notification, notif)
        except Exception as e:
            logger.warning(f"Errore notifica annullamento: {e}")

        return True
