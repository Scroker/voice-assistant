# Voice Assistant GNOME Extension
# Copyright (C) 2026 Giorgio Dramis
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import sys
import os
import json
import threading
import queue

from dasbus.connection import SessionMessageBus
from dasbus.loop import EventLoop
from dasbus.server.interface import dbus_interface, dbus_signal
import sounddevice as sd
from providers import get_provider
import notify2

from audio.player import AudioPlayer
from services.tts_service import TTSServiceManager
from services.llm_service import LLMServiceManager
from core.pipeline import PipelineController
from core.state import StateMachine
from core.logger import setup_logger, install_global_exception_hooks, ErrorCollector

import gi
gi.require_version('Gio', '2.0')
from gi.repository import Gio, GLib

q = queue.Queue()

def audio_callback(indata, frames, time, status):
    """Questa callback viene chiamata per ogni blocco di audio in ingresso dal microfono."""
    if status:
        print(status, file=sys.stderr)
    q.put(bytes(indata))

class PowerInhibitor:
    def __init__(self):
        self._gnome_cookie = None
        self._logind_fd = None

    def inhibit(self, reason="Scaricamento modello in corso"):
        # 1. Systemd Logind Inhibitor (System Bus FD)
        if self._logind_fd is None:
            try:
                sys_bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
                res, fd_list = sys_bus.call_with_unix_fd_list_sync(
                    'org.freedesktop.login1',
                    '/org/freedesktop/login1',
                    'org.freedesktop.login1.Manager',
                    'Inhibit',
                    GLib.Variant('(ssss)', ('sleep:idle:handle-suspend-key:handle-hibernate-key:handle-lid-switch', 'Voice Assistant', reason, 'block')),
                    GLib.VariantType.new('(h)'),
                    Gio.DBusCallFlags.NONE,
                    -1,
                    None,
                    None
                )
                if res and fd_list:
                    fd_idx = res.unpack()[0]
                    self._logind_fd = fd_list.get(fd_idx)
                    print(f"PowerInhibitor: Systemd logind lock attivato (FD: {self._logind_fd}).")
            except Exception as e:
                print(f"PowerInhibitor: impossibile attivare logind lock: {e}")

        # 2. GNOME SessionManager Inhibitor (Session Bus Cookie)
        if self._gnome_cookie is None:
            try:
                bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                res = bus.call_sync(
                    "org.gnome.SessionManager",
                    "/org/gnome/SessionManager",
                    "org.gnome.SessionManager",
                    "Inhibit",
                    GLib.Variant("(susu)", ("org.local.VoiceAssistant", 0, reason, 12)),
                    GLib.VariantType.new("(u)"),
                    Gio.DBusCallFlags.NONE,
                    -1,
                    None
                )
                if res:
                    self._gnome_cookie = res.unpack()[0]
                    print(f"PowerInhibitor: GNOME SessionManager lock attivato (cookie: {self._gnome_cookie}).")
            except Exception as e:
                print(f"PowerInhibitor: impossibile attivare GNOME lock: {e}")

    def uninhibit(self):
        if self._logind_fd is not None:
            try:
                import os
                os.close(self._logind_fd)
                print("PowerInhibitor: Systemd logind lock rilasciato.")
            except Exception as e:
                print(f"PowerInhibitor: errore rilascio logind lock: {e}")
            self._logind_fd = None

        if self._gnome_cookie is not None:
            try:
                bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                bus.call_sync(
                    "org.gnome.SessionManager",
                    "/org/gnome/SessionManager",
                    "org.gnome.SessionManager",
                    "Uninhibit",
                    GLib.Variant("(u)", (self._gnome_cookie,)),
                    None,
                    Gio.DBusCallFlags.NONE,
                    -1,
                    None
                )
                print("PowerInhibitor: GNOME SessionManager lock rilasciato.")
            except Exception as e:
                print(f"PowerInhibitor: errore rilascio GNOME lock: {e}")
            self._gnome_cookie = None

@dbus_interface("org.local.VoiceAssistant")
class VoiceAssistant(object):
    def __init__(self):
        self._inhibitor = PowerInhibitor()
        self._state = "disabled" # Parte disabilitato o idle? Mettiamo disabled per sicurezza
        self._listening = False
        self._stream = None
        self._downloading_models = {}
        self._cancel_requests = set()
        
        self.settings = Gio.Settings.new("org.gnome.shell.extensions.voice-assistant")
        self.wakeword = self.settings.get_string("wakeword")
        self.provider_name = self.settings.get_string("stt-provider")
        self.model_name = self.settings.get_string("stt-model")
        self.hardware = self.settings.get_string("stt-hardware")
        self.models_dir = self.settings.get_string("models-dir")
        
        self.language = self.settings.get_string("language")
        if not self.language or self.language.strip() == "":
            import locale
            env_lang = os.environ.get("LANG", "") or os.environ.get("LC_MESSAGES", "")
            sys_loc = (locale.getdefaultlocale()[0] or "").lower()
            full_lang = (env_lang or sys_loc).lower()
            if full_lang.startswith("it"):
                self.language = "it"
            elif full_lang.startswith("en"):
                self.language = "en"
            else:
                self.language = "it"
            try:
                self.settings.set_string("language", self.language)
            except Exception:
                pass

        self.vosk_ww_model = "vosk-model-small-it-0.22" if self.language == "it" else "vosk-model-small-en-us-0.15"

        try:
            extra_str = self.settings.get_string("stt-extra")
            self.extra_config = json.loads(extra_str) if extra_str else {}
        except json.JSONDecodeError:
            self.extra_config = {}
            
        is_enabled = self.settings.get_boolean("enabled")
        
        self.settings.connect("changed::language", self.on_settings_changed)
        self.settings.connect("changed::wakeword", self.on_settings_changed)
        self.settings.connect("changed::stt-provider", self.on_settings_changed)
        self.settings.connect("changed::stt-model", self.on_settings_changed)
        self.settings.connect("changed::stt-hardware", self.on_settings_changed)
        self.settings.connect("changed::stt-extra", self.on_settings_changed)
        self.settings.connect("changed::models-dir", self.on_settings_changed)
        self.settings.connect("changed::enabled", self.on_settings_changed)
        
        try:
            notify2.init("Voice Assistant")
        except Exception as e:
            print(f"Avviso: impossibile inizializzare notify2: {e}")
            
        # Inizializza il motore Wake Word in background (fisso su Vosk per basso consumo CPU)
        import threading
        self.ww_recognizer = None
        def _load_ww():
            from providers.vosk_provider import VoskProvider
            from vosk import KaldiRecognizer
            try:
                def progress_cb(pct):
                    try:
                        self.DownloadProgress(pct)
                    except Exception:
                        pass
                self.ww_provider = VoskProvider(self.vosk_ww_model, "cpu", {}, progress_callback=progress_cb, models_dir=self.models_dir)
                self.ww_model = self.ww_provider.model
                self.ww_recognizer = KaldiRecognizer(self.ww_model, 16000)
                print(f"Motore Wake Word (Vosk: {self.vosk_ww_model}) inizializzato con successo.")
            except Exception as e:
                print(f"Errore inizializzazione Wake Word: {e}")
                self.ww_recognizer = None
        
        threading.Thread(target=_load_ww, daemon=True).start()
            
        # Inizializza Audio Player, TTS, LLM Service e Pipeline Controller
        self.audio_player = AudioPlayer()
        self.audio_player.start()

        self.tts_manager = TTSServiceManager(
            audio_player=self.audio_player
        )

        self.llm_service = LLMServiceManager(settings_observer=self)

        self.state_machine = StateMachine()
        self.state_machine.add_callback(self.set_state)

        self.pipeline_controller = PipelineController(
            state_machine=self.state_machine,
            audio_player=self.audio_player,
            llm_streamer=lambda prompt: self.llm_service.stream_tokens(prompt),
            tts_engine=lambda text: self.tts_manager.speak(text)
        )

        # Avviamo il caricamento in un thread per evitare di bloccare 
        # la registrazione D-Bus e causare un timeout di systemd
        self._load_id = 1
        import threading
        threading.Thread(target=self.load_provider, args=(self._load_id,), daemon=True).start()
        
        # Avvia il thread dell'audio in background
        self._audio_thread = threading.Thread(target=self._audio_loop, daemon=True)
        self._audio_thread.start()
        
        self.set_state("idle" if is_enabled else "disabled")

    def get(self, key: str, default: Any = None) -> Any:
        try:
            val = self.settings.get_value(key)
            return val.unpack() if val is not None else default
        except Exception:
            return default

    def _schedule_reload(self):
        if getattr(self, '_reload_timer', None):
            self._reload_timer.cancel()
            
        self._load_id = getattr(self, '_load_id', 0) + 1
        current_id = self._load_id
        
        import threading
        self._reload_timer = threading.Timer(0.5, lambda: threading.Thread(target=self.load_provider, args=(current_id,), daemon=True).start())
        self._reload_timer.start()

    def on_settings_changed(self, settings, key):
        if key == "wakeword":
            self.wakeword = settings.get_string(key)
            print(f"Wakeword aggiornata a: '{self.wakeword}'")
        elif key == "stt-provider":
            new_val = settings.get_string(key)
            if new_val != getattr(self, 'provider_name', ''):
                self.provider_name = new_val
                self._schedule_reload()
        elif key == "stt-model":
            new_val = settings.get_string(key)
            if new_val != getattr(self, 'model_name', ''):
                self.model_name = new_val
                self._schedule_reload()
        elif key == "stt-hardware":
            new_val = settings.get_string(key)
            if new_val != getattr(self, 'hardware', ''):
                self.hardware = new_val
                self._schedule_reload()
        elif key == "models-dir":
            new_val = settings.get_string(key)
            if new_val != getattr(self, 'models_dir', ''):
                self.models_dir = new_val
                self._schedule_reload()
        elif key == "stt-extra":
            try:
                new_extra = json.loads(settings.get_string(key))
            except json.JSONDecodeError:
                new_extra = {}
            if new_extra != getattr(self, 'extra_config', {}):
                self.extra_config = new_extra
                self._schedule_reload()
        elif key == "enabled":
            new_enabled = settings.get_boolean(key)
            if new_enabled and self._state == "disabled":
                GLib.idle_add(self.set_state, "idle")
            elif not new_enabled and self._state != "disabled":
                GLib.idle_add(self.set_state, "disabled")

    def _show_notification(self, notif):
        if notif:
            notif.show()
        return False

    def _has_installed_models(self) -> bool:
        target_dir = getattr(self, 'models_dir', '')
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
        # Usiamo variabili locali per il thread per evitare conflitti se l'utente cambia modello durante il download
        local_provider_name = self.provider_name
        local_model_name = self.model_name
        local_hardware = self.hardware
        local_extra = self.extra_config
        local_models_dir = getattr(self, 'models_dir', '')
        key_str = f"{local_provider_name}:{local_model_name}"
        model_key = (local_provider_name, local_model_name)
        if hasattr(self, '_cancel_requests'):
            self._cancel_requests.discard(key_str)
        
        print(f"Caricamento del provider STT '{local_provider_name}'...")
        
        if load_id == getattr(self, '_load_id', 0):
            # Mostra l'icona di download sul pannello principale dell'estensione SOLO al primo avvio quando non c'è alcun modello installato
            if not self._has_installed_models():
                GLib.idle_add(self.set_state, "downloading")
            if key_str not in self._downloading_models:
                self._downloading_models[key_str] = 0
            
        if not hasattr(self, '_active_notifs'):
            self._active_notifs = {}
        
        # Se esiste già una notifica per questo modello (es. download in background), la riutilizziamo
        if model_key in self._active_notifs:
            notif = self._active_notifs[model_key]
        else:
            try:
                notif = notify2.Notification("Voice Assistant", f"Inizializzazione {local_provider_name} ({local_model_name})...", "system-run-symbolic")
                notif._is_closed = False
                
                def on_closed(n):
                    n._is_closed = True
                    
                notif.connect('closed', on_closed)
                self._active_notifs[model_key] = notif
            except:
                notif = None

        if load_id == getattr(self, '_load_id', 0) and notif:
            GLib.idle_add(self._show_notification, notif)

        def progress_cb(percent: int):
            key = f"{local_provider_name}:{local_model_name}"
            if hasattr(self, '_cancel_requests') and key in self._cancel_requests:
                raise InterruptedError("Scaricamento annullato dall'utente")

            is_active = (local_provider_name == getattr(self, 'provider_name', '')) and (local_model_name == getattr(self, 'model_name', ''))
            if percent >= 0 and percent < 100:
                self._downloading_models[key] = percent
            else:
                self._downloading_models.pop(key, None)
                
            self.emit_download_progress(local_provider_name, local_model_name, percent)
                
            if notif:
                if getattr(notif, '_is_closed', False):
                    notif.id = 0
                    notif._is_closed = False
                
                notif.update("Voice Assistant", f"Scaricamento {local_provider_name} ({local_model_name}): {percent}%", "folder-download-symbolic")
                try:
                    notif.show()
                except Exception:
                    pass
                
        try:
            new_provider = get_provider(
                local_provider_name, 
                local_model_name, 
                local_hardware, 
                local_extra,
                progress_cb,
                models_dir=local_models_dir
            )
            print(f"Provider '{local_provider_name}' inizializzato.")
            
            if notif:
                notif.set_timeout(notify2.EXPIRES_NEVER)
                notif.update("Voice Assistant", f"{local_provider_name} ({local_model_name}) pronto!", "emblem-ok-symbolic")
                notif._is_closed = False
                GLib.idle_add(self._show_notification, notif)
                
            self._downloading_models.pop(key_str, None)
            
            # Cleanup della notifica dalla cache
            if (local_provider_name, local_model_name) in getattr(self, '_active_notifs', {}):
                self._active_notifs.pop((local_provider_name, local_model_name), None)
                
            # Se l'ID corrisponde a quello attuale, applichiamo il provider
            if load_id == getattr(self, '_load_id', 0):
                self.provider = new_provider
                is_enabled = self.settings.get_boolean("enabled")
                GLib.idle_add(self.set_state, "idle" if is_enabled else "disabled")
            else:
                print(f"Download di {local_provider_name} ({local_model_name}) completato in background, ma l'utente ha selezionato un altro modello nel frattempo.")
        except Exception as e:
            is_cancelled = hasattr(self, '_cancel_requests') and key_str in self._cancel_requests
            if hasattr(self, '_cancel_requests'):
                self._cancel_requests.discard(key_str)
            self._downloading_models.pop(key_str, None)
            self.emit_download_progress(local_provider_name, local_model_name, -1)

            if is_cancelled:
                self._cleanup_partial_download(local_provider_name, local_model_name)

            print(f"Errore caricamento provider STT: {e}")
            if notif:
                msg = f"Scaricamento di {local_model_name} annullato" if is_cancelled else f"Errore caricamento: {e}"
                icon = "dialog-warning-symbolic" if is_cancelled else "dialog-error-symbolic"
                notif.update("Voice Assistant", msg, icon)
                GLib.idle_add(self._show_notification, notif)
            if load_id == getattr(self, '_load_id', 0):
                is_enabled = self.settings.get_boolean("enabled")
                GLib.idle_add(self.set_state, "idle" if is_enabled else "disabled")

    @dbus_signal
    def StateChanged(self, new_state: str):
        pass

    @dbus_signal
    def DownloadProgress(self, provider: str, model_name: str, percent: int):
        pass

    def emit_download_progress(self, provider: str, model_name: str, percent: int):
        def _emit():
            try:
                self.DownloadProgress(provider, model_name, percent)
            except Exception as e:
                print(f"Error emitting DownloadProgress signal: {e}")
            return False
        GLib.idle_add(_emit)

    def _create_stream(self):
        if self._stream is None:
            self._stream = sd.RawInputStream(samplerate=16000, blocksize=8000, device=None,
                                             dtype='int16', channels=1, callback=audio_callback)

    def _close_stream(self):
        if self._stream is not None:
            if self._stream.active:
                self._stream.stop()
            self._stream.close()
            self._stream = None
            # Svuota la coda per evitare residui
            while not q.empty():
                q.get_nowait()

    def set_state(self, state):
        # Eseguiamo sempre nel main thread per evitare crash di DBus
        def _do_set_state():
            if getattr(self, '_state', None) == state:
                return False
            self._state = state
            self.StateChanged(state)
            print(f"Stato UI cambiato in: {state}")
            
            if state == "downloading":
                self._inhibitor.inhibit("Scaricamento modello Voice Assistant in corso")
            else:
                self._inhibitor.uninhibit()

            if state == "disabled":
                print("Microfono disattivato (hardware chiuso).")
                self._close_stream()
            elif state == "idle" or state == "listening":
                self._create_stream()
                if not self._stream.active:
                    print("Microfono attivato (in ascolto).")
                    self._stream.start()
            return False
        GLib.idle_add(_do_set_state)

    def ToggleListening(self) -> bool:
        """Metodo chiamato dall'estensione GNOME quando l'utente clicca sull'icona."""
        if self._state == "disabled":
            print("Abilitazione dell'assistente.")
            self.settings.set_boolean("enabled", True)
            self.set_state("idle")
            return True
        else:
            print("Disabilitazione dell'assistente.")
            self.settings.set_boolean("enabled", False)
            self.set_state("disabled")
            return False

    def GetAvailableModels(self, provider: str) -> str:
        """Ritorna una stringa JSON contenente la lista dei modelli disponibili per il provider indicato."""
        from providers import get_available_models
        models = get_available_models(provider)
        return json.dumps(models)

    def GetDownloadingModels(self) -> str:
        """Ritorna una stringa JSON con i modelli attualmente in fase di scaricamento e la relativa percentuale."""
        return json.dumps(self._downloading_models)

    def GetErrorReports(self) -> str:
        """Ritorna i report degli errori memorizzati in formato JSON."""
        reports = ErrorCollector.list_reports(limit=20)
        return json.dumps(reports)

    def ClearErrorReports(self) -> bool:
        """Svuota la cronologia dei report di errore."""
        from core.logger import ERROR_REPORTS_DIR
        try:
            if os.path.exists(ERROR_REPORTS_DIR):
                for f in os.listdir(ERROR_REPORTS_DIR):
                    fpath = os.path.join(ERROR_REPORTS_DIR, f)
                    if os.path.isfile(fpath):
                        os.remove(fpath)
            return True
        except Exception as e:
            print(f"Errore pulizia report: {e}")
            return False

    def DownloadModel(self, provider: str, model_name: str) -> bool:
        """Avvia lo scaricamento di un modello in background via D-Bus senza cambiare il modello in uso."""
        def _download_thread():
            key = f"{provider}:{model_name}"
            if hasattr(self, '_cancel_requests'):
                self._cancel_requests.discard(key)
            self._downloading_models[key] = 0
            self.emit_download_progress(provider, model_name, 0)

            print(f"[D-Bus] Avvio scaricamento modello: provider={provider}, model={model_name}")
            self._inhibitor.inhibit(f"Scaricamento modello {model_name} in corso")
            
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
                print(f"Avviso: impossibile creare notifica per download: {e}")

            def progress_cb(percent: int):
                if hasattr(self, '_cancel_requests') and key in self._cancel_requests:
                    raise InterruptedError("Scaricamento annullato dall'utente")

                self._downloading_models[key] = percent
                self.emit_download_progress(provider, model_name, percent)
                if notif:
                    if getattr(notif, '_is_closed', False):
                        notif.id = 0
                        notif._is_closed = False
                    notif.set_timeout(notify2.EXPIRES_NEVER)
                    notif.update("Voice Assistant", f"Scaricamento {provider} ({model_name}): {percent}%", "folder-download-symbolic")
                    GLib.idle_add(self._show_notification, notif)

            try:
                get_provider(
                    provider,
                    model_name,
                    self.hardware,
                    self.extra_config,
                    progress_cb,
                    models_dir=self.models_dir,
                    download_only=True
                )
                print(f"[D-Bus] Scaricamento completato: {provider} ({model_name})")
                self._downloading_models.pop(key, None)
                self.emit_download_progress(provider, model_name, 100)
                if notif:
                    notif.set_timeout(notify2.EXPIRES_NEVER)
                    notif.update("Voice Assistant", f"Modello {model_name} scaricato con successo!", "emblem-ok-symbolic")
                    notif._is_closed = False
                    GLib.idle_add(self._show_notification, notif)
            except Exception as e:
                is_cancelled = hasattr(self, '_cancel_requests') and key in self._cancel_requests
                if hasattr(self, '_cancel_requests'):
                    self._cancel_requests.discard(key)
                self._downloading_models.pop(key, None)
                self.emit_download_progress(provider, model_name, -1)

                if is_cancelled:
                    self._cleanup_partial_download(provider, model_name)

                print(f"[D-Bus] Scaricamento modello {model_name} terminato: {e}")
                if notif:
                    msg = f"Scaricamento di {model_name} annullato" if is_cancelled else f"Errore scaricamento {model_name}: {e}"
                    icon = "dialog-warning-symbolic" if is_cancelled else "dialog-error-symbolic"
                    notif.set_timeout(5000)
                    notif.update("Voice Assistant", msg, icon)
                    GLib.idle_add(self._show_notification, notif)
            finally:
                self._inhibitor.uninhibit()

        threading.Thread(target=_download_thread, daemon=True).start()
        return True

    def _cleanup_partial_download(self, provider: str, model_name: str):
        try:
            target_dir = getattr(self, 'models_dir', '') or os.path.expanduser("~/.local/share/voice-assistant/models")
            possible_folders = [
                os.path.join(target_dir, model_name),
                os.path.join(target_dir, f"vosk-model-{model_name}"),
                os.path.join(target_dir, f"whisper-{model_name}"),
            ]
            possible_zips = [
                os.path.join(target_dir, f"{model_name}.zip"),
                os.path.join(target_dir, f"vosk-model-{model_name}.zip")
            ]
            import shutil
            for folder in possible_folders:
                if os.path.exists(folder):
                    print(f"[Cleanup] Rimozione cartella incompleta per annullamento: {folder}")
                    shutil.rmtree(folder, ignore_errors=True)
            for zip_file in possible_zips:
                if os.path.exists(zip_file):
                    print(f"[Cleanup] Rimozione file zip incompleto per annullamento: {zip_file}")
                    try: os.remove(zip_file)
                    except: pass
        except Exception as clean_err:
            print(f"Errore pulizia download annullato: {clean_err}")

    def CancelDownload(self, provider: str, model_name: str) -> bool:
        """Annulla lo scaricamento di un modello in corso."""
        key = f"{provider}:{model_name}"
        print(f"[D-Bus] Richiesta annullamento scaricamento per {key}")
        if not hasattr(self, '_cancel_requests'):
            self._cancel_requests = set()
        self._cancel_requests.add(key)
        self._downloading_models.pop(key, None)
        self.emit_download_progress(provider, model_name, -1)
        
        self._cleanup_partial_download(provider, model_name)
        
        try:
            notif = notify2.Notification("Voice Assistant", f"Scaricamento di {model_name} annullato", "dialog-warning-symbolic")
            notif.set_timeout(4000)
            GLib.idle_add(self._show_notification, notif)
        except Exception as e:
            print(f"Errore notifica annullamento: {e}")

        return True

    def trigger_assistant(self):
        if self._state != "idle":
            return
            
        import time
        self._listening_start_time = time.time()
        self._last_speech_time = None
        GLib.idle_add(self.set_state, "listening")
        print("Ora ti ascolto... Parla!")
        if hasattr(self, 'provider') and self.provider:
            self.provider.reset()
        if getattr(self, 'ww_recognizer', None):
            self.ww_recognizer.Reset()

    def _audio_loop(self):
        """Loop che legge costantemente l'audio dal microfono."""
        import time
        import numpy as np
        
        silence_timeout = 2.0  # Secondi di silenzio per far scattare la trascrizione

        try:
            while True:
                data = q.get()
                if self._state == "disabled":
                    continue
                    
                if self._state == "idle":
                    if self.ww_recognizer:
                        import json
                        wakeword_lower = self.wakeword.lower()
                        
                        if self.ww_recognizer.AcceptWaveform(data):
                            res_json = self.ww_recognizer.Result()
                            res = json.loads(res_json)
                            text = res.get("text", "").strip()
                            if wakeword_lower in text:
                                print(f"\n--- Wakeword '{self.wakeword}' rilevata (da risultato finale)! ---")
                                self.trigger_assistant()
                        else:
                            partial_json = self.ww_recognizer.PartialResult()
                            partial = json.loads(partial_json)
                            partial_text = partial.get("partial", "").strip()
                            if wakeword_lower in partial_text:
                                print(f"\n--- Wakeword '{self.wakeword}' rilevata! ---")
                                self.trigger_assistant()
                elif self._state == "listening":
                    if not hasattr(self, 'provider') or not self.provider:
                        continue

                    text, partial_text = self.provider.process_chunk(data)
                    now = time.time()

                    if not hasattr(self, '_listening_start_time') or self._listening_start_time is None:
                        self._listening_start_time = now
                    
                    if text:
                        self._process_text(text)
                        self._listening_start_time = None
                        self._last_speech_time = None
                        GLib.idle_add(self.set_state, "idle")
                    else:
                        audio_np = np.frombuffer(data, np.int16)
                        volume = np.abs(audio_np.astype(float)).mean()

                        if volume > 300 or len(partial_text.strip()) > 0:
                            self._last_speech_time = now

                        if getattr(self, '_last_speech_time', None) and (now - self._last_speech_time) > silence_timeout:
                            print("Rilevato silenzio dopo il parlato, procedo con la trascrizione...")
                            batch_text = self.provider.flush_and_transcribe()
                            if batch_text:
                                self._process_text(batch_text)
                            self._listening_start_time = None
                            self._last_speech_time = None
                            GLib.idle_add(self.set_state, "idle")
                        elif (now - self._listening_start_time) > 6.0:
                            print("Timeout ascolto raggiunto, fine acquisizione.")
                            batch_text = self.provider.flush_and_transcribe()
                            if batch_text:
                                self._process_text(batch_text)
                            self._listening_start_time = None
                            self._last_speech_time = None
                            GLib.idle_add(self.set_state, "idle")

        except Exception as e:
            print(f"Errore nel thread audio: {e}", file=sys.stderr)

    def _process_text(self, text):
        if self._state == "listening":
            print(f"\n[Testo Riconosciuto]: {text}\n")
            self.set_state("processing")
            
            # Esecuzione tramite Pipeline Controller (Fast-Path o Fallback TTS)
            res = self.pipeline_controller.process_text_input(text)
            
            # Se non c'è stato match Fast-Path e non c'è ancora un LLM attivo, rispondi con sintesi di conferma del testo
            if not res.get("fast_path") and not res.get("response"):
                print(f"[TTS Fallback] Sintesi vocale per: '{text}'")
                self.tts_manager.speak(f"Ho ascoltato: {text}")
            
            GLib.idle_add(self.set_state, "idle")
        elif self._state == "idle":
            # Controllo di sicurezza se la wakeword viene identificata solo nel risultato finale
            if self.wakeword in text:
                print(f"\n--- Wakeword '{self.wakeword}' rilevata (a fine frase)! ---")
                self.trigger_assistant()

if __name__ == '__main__':
    setup_logger()
    install_global_exception_hooks()
    assistant = VoiceAssistant()
    bus = SessionMessageBus()
    bus.publish_object("/org/local/VoiceAssistant", assistant)
    bus.register_service("org.local.VoiceAssistant")
    loop = EventLoop()
    try:
        loop.run()
    except KeyboardInterrupt:
        print("\nUscita.")
        sys.exit(0)
