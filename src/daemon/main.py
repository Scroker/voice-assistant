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
import json
import threading
import queue

from dasbus.connection import SessionMessageBus
from dasbus.loop import EventLoop
from dasbus.server.interface import dbus_interface, dbus_signal
import sounddevice as sd
from providers import get_provider
import notify2

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
        
        self.settings = Gio.Settings.new("org.gnome.shell.extensions.voice-assistant")
        self.wakeword = self.settings.get_string("wakeword")
        self.provider_name = self.settings.get_string("stt-provider")
        self.model_name = self.settings.get_string("stt-model")
        self.hardware = self.settings.get_string("stt-hardware")
        self.models_dir = self.settings.get_string("models-dir")
        
        try:
            extra_str = self.settings.get_string("stt-extra")
            self.extra_config = json.loads(extra_str) if extra_str else {}
        except json.JSONDecodeError:
            self.extra_config = {}
            
        is_enabled = self.settings.get_boolean("enabled")
        
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
                self.ww_provider = VoskProvider("vosk-model-small-it-0.22", "cpu", {}, progress_callback=progress_cb, models_dir=self.models_dir)
                self.ww_model = self.ww_provider.model
                self.ww_recognizer = KaldiRecognizer(self.ww_model, 16000)
                print("Motore Wake Word (Vosk) inizializzato con successo.")
            except Exception as e:
                print(f"Errore inizializzazione Wake Word: {e}")
                self.ww_recognizer = None
        
        threading.Thread(target=_load_ww, daemon=True).start()
            
        # Avviamo il caricamento in un thread per evitare di bloccare 
        # la registrazione D-Bus e causare un timeout di systemd
        self._load_id = 1
        import threading
        threading.Thread(target=self.load_provider, args=(self._load_id,), daemon=True).start()
        
        # Avvia il thread dell'audio in background
        self._audio_thread = threading.Thread(target=self._audio_loop, daemon=True)
        self._audio_thread.start()
        
        self.set_state("idle" if is_enabled else "disabled")

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

    def load_provider(self, load_id):
        # Usiamo variabili locali per il thread per evitare conflitti se l'utente cambia modello durante il download
        local_provider_name = self.provider_name
        local_model_name = self.model_name
        local_hardware = self.hardware
        local_extra = self.extra_config
        local_models_dir = getattr(self, 'models_dir', '')
        model_key = (local_provider_name, local_model_name)
        
        print(f"Caricamento del provider STT '{local_provider_name}'...")
        
        if load_id == getattr(self, '_load_id', 0):
            GLib.idle_add(self.set_state, "downloading")
            
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
            is_active = (local_provider_name == getattr(self, 'provider_name', '')) and (local_model_name == getattr(self, 'model_name', ''))
            key = f"{local_provider_name}:{local_model_name}"
            if percent >= 0 and percent < 100:
                self._downloading_models[key] = percent
            else:
                self._downloading_models.pop(key, None)
                
            try:
                self.DownloadProgress(local_provider_name, local_model_name, percent)
            except Exception:
                pass
                
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
                
            # Cleanup della notifica dalla cache
            if model_key in getattr(self, '_active_notifs', {}):
                self._active_notifs.pop(model_key, None)
                
            # Se l'ID corrisponde a quello attuale, applichiamo il provider
            if load_id == getattr(self, '_load_id', 0):
                self.provider = new_provider
                is_enabled = self.settings.get_boolean("enabled")
                GLib.idle_add(self.set_state, "idle" if is_enabled else "disabled")
            else:
                print(f"Download di {local_provider_name} ({local_model_name}) completato in background, ma l'utente ha selezionato un altro modello nel frattempo.")
        except Exception as e:
            print(f"Errore critico caricamento provider STT: {e}")
            if notif:
                notif.update("Voice Assistant", f"Errore caricamento: {e}", "dialog-error-symbolic")
                GLib.idle_add(self._show_notification, notif)
            if load_id == getattr(self, '_load_id', 0):
                GLib.idle_add(self.set_state, "disabled")

    @dbus_signal
    def StateChanged(self, new_state: str):
        pass

    @dbus_signal
    def DownloadProgress(self, provider: str, model_name: str, percent: int):
        pass

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

    def DownloadModel(self, provider: str, model_name: str) -> bool:
        """Avvia lo scaricamento di un modello in background via D-Bus senza cambiare il modello in uso."""
        def _download_thread():
            key = f"{provider}:{model_name}"
            self._downloading_models[key] = 0
            try:
                self.DownloadProgress(provider, model_name, 0)
            except Exception:
                pass

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
                self._downloading_models[key] = percent
                try:
                    self.DownloadProgress(provider, model_name, percent)
                except Exception:
                    pass
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
                    models_dir=self.models_dir
                )
                print(f"[D-Bus] Scaricamento completato: {provider} ({model_name})")
                self._downloading_models.pop(key, None)
                try:
                    self.DownloadProgress(provider, model_name, 100)
                except Exception:
                    pass
                if notif:
                    notif.set_timeout(notify2.EXPIRES_NEVER)
                    notif.update("Voice Assistant", f"Modello {model_name} scaricato con successo!", "emblem-ok-symbolic")
                    notif._is_closed = False
                    GLib.idle_add(self._show_notification, notif)
            except Exception as e:
                print(f"[D-Bus] Errore scaricamento modello {model_name}: {e}")
                self._downloading_models.pop(key, None)
                try:
                    self.DownloadProgress(provider, model_name, -1)
                except Exception:
                    pass
                if notif:
                    notif.set_timeout(notify2.EXPIRES_NEVER)
                    notif.update("Voice Assistant", f"Errore scaricamento {model_name}: {e}", "dialog-error-symbolic")
                    GLib.idle_add(self._show_notification, notif)
            finally:
                self._inhibitor.uninhibit()

        threading.Thread(target=_download_thread, daemon=True).start()
        return True

    def trigger_assistant(self):
        if self._state != "idle":
            return
            
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
        
        silence_timeout = 2.0  # Secondi di silenzio per far scattare Whisper
        last_speech_time = None

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
                                last_speech_time = time.time()
                        else:
                            partial_json = self.ww_recognizer.PartialResult()
                            partial = json.loads(partial_json)
                            partial_text = partial.get("partial", "").strip()
                            if wakeword_lower in partial_text:
                                print(f"\n--- Wakeword '{self.wakeword}' rilevata! ---")
                                self.trigger_assistant()
                                last_speech_time = time.time()
                elif self._state == "listening":
                    if not hasattr(self, 'provider') or not self.provider:
                        continue

                    text, partial_text = self.provider.process_chunk(data)
                    
                    # Vosk ritorna il testo completato in tempo reale
                    if text:
                        self._process_text(text)
                        last_speech_time = None
                        GLib.idle_add(self.set_state, "idle")
                    else:
                        # Logica di silenzio manuale per Whisper (che non supporta streaming reale)
                        audio_np = np.frombuffer(data, np.int16)
                        # Calcolo approssimativo del volume
                        volume = np.abs(audio_np.astype(float)).mean()
                        
                        if volume > 500: # Soglia RMS approssimativa per il parlato
                            last_speech_time = time.time()
                            
                        if last_speech_time and (time.time() - last_speech_time) > silence_timeout:
                            print("Rilevato silenzio, procedo con la trascrizione (se Whisper)...")
                            batch_text = self.provider.flush_and_transcribe()
                            if batch_text:
                                self._process_text(batch_text)
                            last_speech_time = None
                            GLib.idle_add(self.set_state, "idle")

        except Exception as e:
            print(f"Errore nel thread audio: {e}", file=sys.stderr)

    def _process_text(self, text):
        if self._state == "listening":
            print(f"\n[Testo Riconosciuto]: {text}\n")
            self.set_state("processing")
            
            # Qui si potrebbe integrare la chiamata a un LLM (es. Ollama) o l'esecuzione di comandi.
            # Per questa prima fase, stampiamo semplicemente a schermo e torniamo in idle.
            print("Elaborazione completata (Fase 1: solo STT). Ritorno in attesa.")
            
            self.set_state("idle")
        elif self._state == "idle":
            # Controllo di sicurezza se la wakeword viene identificata solo nel risultato finale
            if self.wakeword in text:
                print(f"\n--- Wakeword '{self.wakeword}' rilevata (a fine frase)! ---")
                self.trigger_assistant()

if __name__ == '__main__':
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
