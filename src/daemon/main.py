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
import time
import threading
import asyncio
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
from core.logger import setup_logger, install_global_exception_hooks, ErrorCollector, DiagnosticBundler, EnvironmentSnapshot, ERROR_REPORTS_DIR
import logging

logger = logging.getLogger("VoiceAssistant.Daemon")
logger_audio = logging.getLogger("VoiceAssistant.Audio")
logger_power = logging.getLogger("VoiceAssistant.Power")
logger_dbus = logging.getLogger("VoiceAssistant.DBus")

import gi
gi.require_version('Gio', '2.0')
from gi.repository import Gio, GLib

q = queue.Queue()

def audio_callback(indata, frames, time, status):
    """Questa callback viene chiamata per ogni blocco di audio in ingresso dal microfono."""
    if status:
        logger_audio.warning(f"Audio callback status: {status}")
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
                    logger_power.info(f"Systemd logind lock attivato (FD: {self._logind_fd}).")
            except Exception as e:
                logger_power.warning(f"Impossibile attivare logind lock: {e}")

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
                    logger_power.info(f"GNOME SessionManager lock attivato (cookie: {self._gnome_cookie}).")
            except Exception as e:
                logger_power.warning(f"Impossibile attivare GNOME lock: {e}")

    def uninhibit(self):
        if self._logind_fd is not None:
            try:
                import os
                os.close(self._logind_fd)
                logger_power.info("Systemd logind lock rilasciato.")
            except Exception as e:
                logger_power.warning(f"Errore rilascio logind lock: {e}")
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
                logger_power.info("GNOME SessionManager lock rilasciato.")
            except Exception as e:
                logger_power.warning(f"Errore rilascio GNOME lock: {e}")
            self._gnome_cookie = None

@dbus_interface("org.local.VoiceAssistant")
class VoiceAssistant(object):
    def __init__(self):
        self._daemon_start_time = time.time()
        self._inhibitor = PowerInhibitor()
        self._state = "disabled" # Parte disabilitato o idle? Mettiamo disabled per sicurezza
        self._listening = False
        self._stream = None
        self._downloading_models = {}
        self._cancel_requests = set()

        # Registrazione gresource dell'estensione per icone e layout UI Blueprint
        res_file = os.path.expanduser("~/.local/share/gnome-shell/extensions/voice-assistant@scroker.github.io/org.gnome.shell.extensions.voice-assistant.gresource")
        if os.path.exists(res_file):
            try:
                resource = Gio.Resource.load(res_file)
                Gio.resources_register(resource)
                logger.info(f"Registrata risorsa gresource in daemon: {res_file}")
            except Exception as e:
                logger.warning(f"Impossibile registrare gresource in daemon: {e}")
        
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
            logger.warning(f"Impossibile inizializzare notify2: {e}")
            
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
                logger.info(f"Motore Wake Word (Vosk: {self.vosk_ww_model}) inizializzato con successo.")
            except Exception as e:
                logger.error(f"Errore inizializzazione Wake Word: {e}", exc_info=True)
                ErrorCollector.record_error(*sys.exc_info(), component="VoiceAssistant.WakeWord")
                self.ww_recognizer = None
        
        threading.Thread(target=_load_ww, daemon=True).start()
            
        # Inizializza Audio Player, TTS, LLM Service e Pipeline Controller
        self.audio_player = AudioPlayer(on_playback_finished=self._on_playback_finished)
        self.audio_player.start()

        self.tts_manager = TTSServiceManager(
            audio_player=self.audio_player
        )

        try:
            from mcp.manager import MCPManager
            self.mcp_manager = MCPManager()
            asyncio.run(self.mcp_manager.initialize())
        except Exception as e:
            logger.warning(f"Inizializzazione MCPManager in main.py: {e}")
            self.mcp_manager = None

        self.llm_service = LLMServiceManager(settings_observer=self, mcp_manager=self.mcp_manager)

        self.state_machine = StateMachine()
        self.state_machine.add_callback(self.set_state)

        def _on_tts_engine(text):
            try:
                self.ResponseTokenStreamed(text, True)
            except Exception:
                pass
            if self.tts_manager.speak(text):
                self.set_state("speaking")

        self.pipeline_controller = PipelineController(
            state_machine=self.state_machine,
            audio_player=self.audio_player,
            llm_streamer=lambda prompt: self.llm_service.stream_tokens(prompt),
            tts_engine=_on_tts_engine
        )
        self.pipeline_controller.on_token_callback = self._on_llm_token
        self.pipeline_controller.fast_path.intent_handler = self._handle_fast_path_intent

        # Avviamo il caricamento in un thread per evitare di bloccare 
        # la registrazione D-Bus e causare un timeout di systemd
        self._load_id = 1
        import threading
        threading.Thread(target=self.load_provider, args=(self._load_id,), daemon=True).start()
        
        # Avvia il thread dell'audio in background
        self._audio_thread = threading.Thread(target=self._audio_loop, daemon=True)
        self._audio_thread.start()
        
        self.set_state("idle" if is_enabled else "disabled")
        
        # Registra il contesto iniziale per i report di errore
        ErrorCollector.set_context_dict({
            "stt_provider": self.provider_name,
            "stt_model": self.model_name,
            "hardware": self.hardware,
            "wakeword": self.wakeword,
            "language": self.language,
            "state": "idle" if is_enabled else "disabled"
        })

    def _handle_fast_path_intent(self, intent_name: str, params: Dict[str, Any]) -> Tuple[bool, str]:
        if not self.mcp_manager:
            return (False, "")

        try:
            if intent_name == "set_volume":
                vol = params.get("volume", 50)
                res = asyncio.run(self.mcp_manager.execute_tool("system_volume", {"action": "set", "level": vol}))
                return (True, res)
            elif intent_name == "volume_up":
                res = asyncio.run(self.mcp_manager.execute_tool("system_volume", {"action": "increase", "level": 10}))
                return (True, res)
            elif intent_name == "volume_down":
                res = asyncio.run(self.mcp_manager.execute_tool("system_volume", {"action": "decrease", "level": 10}))
                return (True, res)
            elif intent_name == "mute":
                res = asyncio.run(self.mcp_manager.execute_tool("system_volume", {"action": "mute"}))
                return (True, res)
            elif intent_name == "set_theme_dark":
                res = asyncio.run(self.mcp_manager.execute_tool("dark_mode", {"mode": "dark"}))
                return (True, res)
            elif intent_name == "set_theme_light":
                res = asyncio.run(self.mcp_manager.execute_tool("dark_mode", {"mode": "light"}))
                return (True, res)
            elif intent_name == "launch_app":
                app = params.get("app", "firefox")
                res = asyncio.run(self.mcp_manager.execute_tool("app_launcher", {"app_name": app}))
                return (True, res)
            elif intent_name == "get_time":
                res = asyncio.run(self.mcp_manager.execute_tool("date_time", {"action": "time"}))
                return (True, res)
            elif intent_name == "get_date":
                res = asyncio.run(self.mcp_manager.execute_tool("date_time", {"action": "date"}))
                return (True, res)
            elif intent_name == "media_pause":
                res = asyncio.run(self.mcp_manager.execute_tool("system_media", {"action": "pause"}))
                return (True, res)
            elif intent_name == "media_play":
                res = asyncio.run(self.mcp_manager.execute_tool("system_media", {"action": "play"}))
                return (True, res)
        except Exception as e:
            logger.error(f"Errore esecuzione Fast-Path MCP: {e}")

        return (False, "")

    def get(self, key: str, default: Any = None) -> Any:
        try:
            val = self.settings.get_value(key)
            return val.unpack() if val is not None else default
        except Exception as e:
            logger.debug(f"GSettings key '{key}' not found, using default: {e}")
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
            self.reset_wakeword_recognizer()
            while not q.empty():
                try:
                    q.get_nowait()
                except Exception:
                    break
            self._listening_start_time = None
            self._last_speech_time = None
            if hasattr(self, 'provider') and self.provider:
                self.provider.reset()
            if self._state in ("listening", "speaking", "processing"):
                GLib.idle_add(self.set_state, "idle")
            logger.info(f"Wakeword aggiornata a: '{self.wakeword}' - ripristinato stato idle.")
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
        local_extra = dict(self.extra_config or {})
        settings_obs = getattr(self, '_settings_observer', None)
        if settings_obs:
            local_extra["api_key"] = settings_obs.get("llm-api-key", "")
            local_extra["language"] = settings_obs.get("language", "it")

        local_models_dir = getattr(self, 'models_dir', '')
        key_str = f"{local_provider_name}:{local_model_name}"
        model_key = (local_provider_name, local_model_name)
        if hasattr(self, '_cancel_requests'):
            self._cancel_requests.discard(key_str)
        
        logger.info(f"Caricamento del provider STT '{local_provider_name}'...")
        
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
            except Exception as notif_err:
                logger.warning(f"Impossibile creare notifica per inizializzazione: {notif_err}")
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
            logger.info(f"Provider {local_provider_name} inizializzato.")
            
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
                logger.info(f"Download di {local_provider_name} ({local_model_name}) completato in background, ma l'utente ha selezionato un altro modello nel frattempo.")
        except Exception as e:
            is_cancelled = hasattr(self, '_cancel_requests') and key_str in self._cancel_requests
            if hasattr(self, '_cancel_requests'):
                self._cancel_requests.discard(key_str)
            self._downloading_models.pop(key_str, None)
            self.emit_download_progress(local_provider_name, local_model_name, -1)

            if is_cancelled:
                self._cleanup_partial_download(local_provider_name, local_model_name)

            logger.error(f"Errore caricamento provider STT: {e}", exc_info=True)
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
                logger_dbus.error(f"Error emitting DownloadProgress signal: {e}")
            return False
        GLib.idle_add(_emit)

    def _ensure_pipewire_aec(self):
        if getattr(self, '_aec_initialized', False):
            return
        self._aec_initialized = True
        try:
            import subprocess
            res = subprocess.run(["pactl", "list", "modules", "short"], capture_output=True, text=True)
            if "module-echo-cancel" not in res.stdout:
                logger.info("[VoiceAssistant.Audio] Attivazione automatica PipeWire WebRTC AEC / Noise Suppression...")
                subprocess.run(["pactl", "load-module", "module-echo-cancel", "aec_method=webrtc"], capture_output=True)
                subprocess.run(["pactl", "set-default-source", "echo-cancel-source"], capture_output=True)
        except Exception as e:
            logger.warning(f"Impossibile caricare modulo PipeWire echo-cancel: {e}")

    def _get_input_device(self):
        try:
            devices = sd.query_devices()
            for idx, dev in enumerate(devices):
                if "echo-cancel" in dev['name'].lower() and dev['max_input_channels'] > 0:
                    logger.info(f"[VoiceAssistant.Audio] Utilizzo del dispositivo microfono AEC: {dev['name']}")
                    return idx
        except Exception:
            pass
        return None

    def _create_stream(self):
        if self._stream is None:
            self._ensure_pipewire_aec()
            device_idx = self._get_input_device()
            try:
                self._stream = sd.RawInputStream(samplerate=16000, blocksize=8000, device=device_idx,
                                                 dtype='int16', channels=1, callback=audio_callback)
            except Exception as e:
                logger.warning(f"[VoiceAssistant.Audio] Impossibile aprire dispositivo AEC (sample rate): {e}. Fallback su dispositivo predefinito.")
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
        if hasattr(state, 'value'):
            state_str = str(state.value).lower()
        else:
            state_str = str(state).lower().replace("assistantstate.", "")

        if getattr(self, '_state', None) == state_str:
            return
        self._state = state_str
        def _do_set_state():
            self.StateChanged(state_str)
            logger.info(f"Stato UI cambiato in: {state_str}")
            
            if state_str == "downloading":
                self._inhibitor.inhibit("Scaricamento modello Voice Assistant in corso")
            else:
                self._inhibitor.uninhibit()

            if state_str == "disabled":
                logger.info("Microfono disattivato (hardware chiuso).")
                self._close_stream()
            elif state_str in ("idle", "listening", "speaking", "processing"):
                self._create_stream()
                if self._stream and not self._stream.active:
                    logger.info("Microfono attivato (in ascolto).")
                    self._stream.start()
                if state_str == "listening":
                    self._listening_start_time = time.time()
                    self._last_speech_time = None
                    self._ignore_audio_until = time.time() + 0.35
                    if hasattr(self, 'provider') and self.provider:
                        self.provider.reset()
                if state_str == "speaking":
                    self._ignore_audio_until = time.time() + 0.5
                    self._start_speaking_watchdog()
                if state_str in ("idle", "speaking"):
                    while not q.empty():
                        try:
                            q.get_nowait()
                        except Exception:
                            break
                    if getattr(self, 'ww_model', None):
                        self.reset_wakeword_recognizer()
            return False
        GLib.idle_add(_do_set_state)

    def GetState(self) -> str:
        """Ritorna lo stato attuale dell'assistente vocale."""
        return str(self._state)

    def ToggleListening(self) -> bool:
        """Metodo chiamato dall'estensione GNOME quando l'utente clicca sull'icona della barra superiore."""
        if self._state == "disabled":
            logger.info("Abilitazione dell'assistente.")
            self.settings.set_boolean("enabled", True)
            self.trigger_assistant()
            return True
        elif self._state in ("listening", "speaking", "processing"):
            logger.info("Interruzione assistente e ritorno in idle.")
            self.set_state("idle")
            return False
        else:
            self.trigger_assistant()
            return True

    def TriggerListening(self) -> bool:
        """Avvia o forza l'ascolto vocale immediato (Push-to-Talk da GUI/Estensione)."""
        logger.info("[D-Bus] Richiesta ascolto vocale immediato da GUI/Estensione.")
        if self._state == "disabled":
            self.settings.set_boolean("enabled", True)
        self.trigger_assistant()
        return True

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
        try:
            ErrorCollector.clear_reports()
            return True
        except Exception as e:
            logger.error(f"Errore pulizia report: {e}")
            return False

    def GenerateDiagnosticBundle(self) -> str:
        """Genera un archivio diagnostico .tar.gz e restituisce il percorso del file."""
        try:
            bundle_path = DiagnosticBundler.generate(
                settings=self.settings,
                state=self._state,
                daemon_start_time=getattr(self, '_daemon_start_time', None)
            )
            return bundle_path
        except Exception as e:
            logger.error(f"Errore durante la generazione del bundle diagnostico: {e}", exc_info=True)
            ErrorCollector.record_error(*sys.exc_info(), component="VoiceAssistant.DiagnosticBundler")
            return ""

    def DownloadModel(self, provider: str, model_name: str) -> bool:
        """Avvia lo scaricamento di un modello in background via D-Bus senza cambiare il modello in uso."""
        def _download_thread():
            key = f"{provider}:{model_name}"
            if hasattr(self, '_cancel_requests'):
                self._cancel_requests.discard(key)
            self._downloading_models[key] = 0
            self.emit_download_progress(provider, model_name, 0)

            logger.info(f"[D-Bus] Avvio scaricamento modello: provider={provider}, model={model_name}")
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
                logger.warning(f"Impossibile creare notifica per download: {e}")

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
                logger.info(f"[D-Bus] Scaricamento completato: {provider} ({model_name})")
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

                logger.error(f"[D-Bus] Scaricamento modello {model_name} terminato: {e}")
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
                    logger.info(f"[Cleanup] Rimozione cartella incompleta per annullamento: {folder}")
                    shutil.rmtree(folder, ignore_errors=True)
            for zip_file in possible_zips:
                if os.path.exists(zip_file):
                    logger.info(f"[Cleanup] Rimozione file zip incompleto per annullamento: {zip_file}")
                    try: os.remove(zip_file)
                    except: pass
        except Exception as clean_err:
            logger.error(f"Errore pulizia download annullato: {clean_err}")

    def CancelDownload(self, provider: str, model_name: str) -> bool:
        """Annulla lo scaricamento di un modello in corso."""
        key = f"{provider}:{model_name}"
        logger.info(f"[D-Bus] Richiesta annullamento scaricamento per {key}")
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
            logger.warning(f"Errore notifica annullamento: {e}")

        return True

    def ShowWindow(self):
        """Metodo D-Bus per lanciare o portare in primo piano la finestra interattiva dell'assistente."""
        logger.info("[D-Bus] Richiesta apertura finestra interattiva assistente.")
        def _launch():
            try:
                from gui.assistant_window import AssistantWindow
                if not hasattr(self, '_gui_window') or self._gui_window is None:
                    self._gui_window = AssistantWindow(dbus_proxy=self)
                self._gui_window.set_visible(True)
                self._gui_window.present()
            except Exception as e:
                logger.error(f"Errore lancio finestra GUI: {e}")
            return False
        GLib.idle_add(_launch)

    def OpenSettings(self):
        """Metodo D-Bus per aprire il pannello di preferenze dell'assistente vocale."""
        logger.info("[D-Bus] Richiesta apertura finestra impostazioni assistente.")
        try:
            import subprocess
            subprocess.Popen(["gnome-extensions", "prefs", "voice-assistant@scroker.github.io"])
        except Exception as e:
            logger.error(f"Errore avvio impostazioni: {e}")

    def ProcessTextInput(self, text: str):
        """Metodo D-Bus per inviare testo direttamente alla pipeline dell'assistente (senza sintesi audio)."""
        logger.info(f"[D-Bus] Ricevuto testo da input GUI: '{text}'")
        import threading
        threading.Thread(target=self._process_text, args=(text, False), daemon=True).start()

    def _on_llm_token(self, token: str):
        """Callback invocata a ogni token generato dall'LLM per aggiornare la GUI e la D-Bus stream."""
        if hasattr(self, '_gui_window') and self._gui_window is not None:
            GLib.idle_add(self._gui_window.append_assistant_token, token)
        try:
            self.ResponseTokenStreamed(token, False)
        except Exception:
            pass

    def _on_playback_finished(self):
        """Callback invocata dall'AudioPlayer al termine della riproduzione vocale."""
        if str(self._state).lower().endswith("speaking") or self._state == "speaking":
            logger.info("[AudioPlayer] Riproduzione audio completata. Ripristino stato idle.")
            GLib.idle_add(self.set_state, "idle")

    def _start_speaking_watchdog(self):
        """Avvia un controllo periodico per sbloccare lo stato se l'audio termina inaspettatamente."""
        ticks = 0
        def _check():
            nonlocal ticks
            ticks += 1
            if str(self._state).lower().endswith("speaking") or self._state == "speaking":
                if ticks > 3 and not getattr(self.audio_player, 'is_playing', False):
                    logger.info("[Watchdog] Rilevato stato 'speaking' senza audio in riproduzione. Ripristino stato 'idle'.")
                    self.set_state("idle")
                    return False
                return True
            return False
        GLib.timeout_add(1000, _check)

    def reset_wakeword_recognizer(self):
        if hasattr(self, 'ww_model') and self.ww_model:
            from vosk import KaldiRecognizer
            try:
                self.ww_recognizer = KaldiRecognizer(self.ww_model, 16000)
            except Exception as e:
                logger.warning(f"Errore reset ww_recognizer: {e}")

    def trigger_assistant(self):
        import time
        # 1. Stop ongoing LLM streaming & audio playback immediately for Barge-in / Interruption
        if hasattr(self, 'pipeline_controller') and self.pipeline_controller:
            self.pipeline_controller.cancel_pipeline(target_state=None)
        elif hasattr(self, 'audio_player') and self.audio_player:
            self.audio_player.stop_playback()

        # 2. Svuota la coda audio per eliminare l'eco residuo degli altoparlanti
        while not q.empty():
            try:
                q.get_nowait()
            except Exception:
                break

        # 3. Finestra di cooldown (350ms) per permettere ai buffer hardware/PipeWire di scaricarsi
        self._ignore_audio_until = time.time() + 0.35

        # 4. Reset pulito dei riconoscitori STT e Wakeword
        self.reset_wakeword_recognizer()
        if hasattr(self, 'provider') and self.provider:
            self.provider.reset()

        self._listening_start_time = time.time()
        self._last_speech_time = None
        self._last_partial_text = ""
        self._last_partial_change_time = None
        self.set_state("listening")
        logger.info("Ora ti ascolto... Parla!")

        if hasattr(self, 'audio_player') and self.audio_player:
            self.audio_player.play_wakeword_chime()

    def _audio_loop(self):
        """Loop che legge costantemente l'audio dal microfono."""
        import time
        import numpy as np
        
        silence_timeout = 1.0  # Secondi di testo parziale invariato per scatenare la trascrizione finale

        if not hasattr(self, 'audio_filter') or self.audio_filter is None:
            from audio.filter import AudioFilter
            self.audio_filter = AudioFilter(sample_rate=16000)

        try:
            while True:
                try:
                    raw_data = q.get(timeout=0.2)
                except Exception:
                    raw_data = None

                if self._state == "disabled":
                    continue

                # Passa l'audio attraverso il filtro DSP a basso rumore (High-Pass + Dynamic Noise Gate)
                data = self.audio_filter.process(raw_data) if raw_data else b""

                # Controllo finestra di cooldown post-interruzione per prevenire ghost echo
                ignore_until = getattr(self, '_ignore_audio_until', 0)
                if time.time() < ignore_until:
                    if hasattr(self, 'provider') and self.provider:
                        self.provider.reset()
                    continue
                    
                if self._state in ("idle", "speaking", "processing", "AssistantState.IDLE", "AssistantState.SPEAKING", "AssistantState.PROCESSING"):
                    if self.ww_recognizer:
                        import json
                        wakeword_lower = self.wakeword.lower().strip()
                        ww_no_h = wakeword_lower.replace('h', '')
                        
                        recognized_str = ""
                        if self.ww_recognizer.AcceptWaveform(data):
                            res_json = self.ww_recognizer.Result()
                            res = json.loads(res_json)
                            recognized_str = res.get("text", "").strip().lower()
                        else:
                            partial_json = self.ww_recognizer.PartialResult()
                            partial = json.loads(partial_json)
                            recognized_str = partial.get("partial", "").strip().lower()

                        # Variante flessibile per italiano/inglese e wakeword personalizzate
                        ww_variants = {wakeword_lower, ww_no_h}
                        if wakeword_lower == "assistente":
                            ww_variants.update(["assistenti", "assistenza", "assiste"])
                        elif "anthon" in wakeword_lower or "anton" in wakeword_lower:
                            ww_variants.update(["anthon", "anton", "antonio", "antoni", "anto", "anthony"])

                        is_speaking_or_proc = self._state in ("speaking", "processing", "AssistantState.SPEAKING", "AssistantState.PROCESSING")
                        if is_speaking_or_proc:
                            ww_variants.update(["stop", "basta", "zitto", "fermati", "silenzio", "interrompi", "cancella"])

                        words = recognized_str.split()

                        # Durante la riproduzione/elaborazione, accettiamo SOLO parole esatte per prevenire falsi positivi da auto-eco
                        if is_speaking_or_proc:
                            matched_ww = next((v for v in ww_variants if v in words), None)
                        else:
                            matched_ww = next((v for v in ww_variants if v in words or (len(v) >= 4 and v in recognized_str)), None)
                            if not matched_ww and len(wakeword_lower) >= 3:
                                import difflib
                                for w in words:
                                    if len(w) >= 3:
                                        ratio = difflib.SequenceMatcher(None, ww_no_h, w.replace('h', '')).ratio()
                                        if ratio >= 0.75:
                                            matched_ww = w
                                            break

                        if matched_ww:
                            logger.info(f"--- Wakeword/Barge-in '{matched_ww}' rilevata in: '{recognized_str}'! Interruzione in corso... ---")
                            parts = recognized_str.split(matched_ww, 1)
                            remainder = parts[1].strip() if len(parts) > 1 else ""
                            
                            self.trigger_assistant()

                            # Verifichiamo se il remainder è un vero comando o solo rumore/interiezione (es. "e", "uh", "um")
                            filler_words = {"e", "ed", "uh", "um", "ah", "oh", "eh", "o", "il", "la", "le", "lo", "un", "una", "uno", "a", "di", "da", "in", "con", "su", "per", "tra", "fra"}
                            remainder_words = [w for w in remainder.split() if w not in filler_words]
                            
                            # Eseguiamo immediatamente il remainder solo se contiene almeno 2 parole significative o se è un comando FastPath valido
                            is_valid_command = False
                            if len(remainder_words) >= 2:
                                is_valid_command = True
                            elif len(remainder_words) == 1 and hasattr(self, 'fast_path'):
                                matched, _, _, _ = self.fast_path.dispatch(remainder)
                                if matched:
                                    is_valid_command = True

                            if is_valid_command:
                                logger.info(f"Comando allegato alla wakeword valido: '{remainder}'")
                                self._process_text(remainder)
                                self._listening_start_time = None
                                self._last_speech_time = None
                                self._last_partial_text = ""
                                self._last_partial_change_time = None

                elif self._state in ("listening", "AssistantState.LISTENING"):
                    if not hasattr(self, 'provider') or not self.provider:
                        continue

                    text, partial_text = self.provider.process_chunk(data)
                    now = time.time()

                    if not hasattr(self, '_listening_start_time') or self._listening_start_time is None:
                        self._listening_start_time = now
                    
                    filler_words = {"e", "ed", "uh", "um", "ah", "oh", "eh", "o", "il", "la", "le", "lo", "un", "una", "uno", "a", "di", "da", "in", "con", "su", "per", "tra", "fra"}
                    ww_lower = self.wakeword.lower().strip()
                    ww_noh = ww_lower.replace('h', '')
                    ww_known_variants = {ww_lower, ww_noh, "assistente", "anton", "anto", "antonio", "anthony"}

                    # Caso A: Vosk ha completato la frase (AcceptWaveform = True)
                    if text:
                        self._listening_start_time = None
                        self._last_partial_text = ""
                        self._last_partial_change_time = None
                        words_in_text = [w.lower() for w in text.strip().split()]
                        meaningful = [w for w in words_in_text if w not in filler_words]
                        is_only_ww = all(w in ww_known_variants for w in meaningful) if meaningful else True
                        
                        if is_only_ww:
                            logger.info(f"Trascrizione immediata '{text}' contiene solo la wakeword, torno in idle.")
                            self.provider.reset()
                            self.set_state("idle")
                        else:
                            self._process_text(text, is_voice=True)
                        continue

                    # Caso B: Elaborazione testo parziale o silenzio
                    partial_clean = partial_text.strip().lower()
                    last_partial = getattr(self, '_last_partial_text', "")
                    last_change = getattr(self, '_last_partial_change_time', None)

                    if partial_clean:
                        if partial_clean != last_partial:
                            self._last_partial_text = partial_clean
                            self._last_partial_change_time = now

                    # 1. Se c'è stato del parlato parziale e l'utente ha smesso di parlare per >= 1.0s:
                    if last_change and (now - last_change) >= 1.0:
                        logger.info(f"Silenzio/Stabilità parziale per 1.0s ('{last_partial}'), procedo con la trascrizione...")
                        batch_text = self.provider.flush_and_transcribe()
                        self._listening_start_time = None
                        self._last_partial_text = ""
                        self._last_partial_change_time = None
                        
                        words_in_batch = [w.lower() for w in batch_text.strip().split()]
                        meaningful = [w for w in words_in_batch if w not in filler_words]
                        is_only_ww = all(w in ww_known_variants for w in meaningful) if meaningful else True

                        if batch_text and not is_only_ww:
                            self._process_text(batch_text, is_voice=True)
                        else:
                            logger.info("Trascrizione finale vuota o contenente solo la wakeword, ritorno in idle.")
                            self.set_state("idle")

                    # 2. Se l'utente NON ha pronunciato alcuna parola parziale entro 2.5s dall'avvio dell'ascolto:
                    elif not last_change and (now - self._listening_start_time) >= 2.5:
                        logger.info("Nessun parlato rilevato entro 2.5 secondi, chiusura ascolto e ritorno in idle.")
                        if hasattr(self.provider, 'reset'):
                            self.provider.reset()
                        self._listening_start_time = None
                        self._last_partial_text = ""
                        self._last_partial_change_time = None
                        self.set_state("idle")

                    # 3. Guardrail di sicurezza: ascolto in corso da oltre 6 secondi
                    elif (now - self._listening_start_time) >= 6.0:
                        logger.info("Timeout massimo ascolto raggiunto (6s), ritorno in idle.")
                        batch_text = self.provider.flush_and_transcribe()
                        self._listening_start_time = None
                        self._last_partial_text = ""
                        self._last_partial_change_time = None
                        
                        words_in_batch = [w.lower() for w in batch_text.strip().split()]
                        meaningful = [w for w in words_in_batch if w not in filler_words]
                        is_only_ww = all(w in ww_known_variants for w in meaningful) if meaningful else True

                        if batch_text and not is_only_ww:
                            self._process_text(batch_text, is_voice=True)
                        else:
                            self.set_state("idle")

        except Exception as e:
            logger.critical(f"Errore critico nel thread audio: {e}", exc_info=True)
            ErrorCollector.record_error(*sys.exc_info(), component="VoiceAssistant.AudioThread", severity="CRITICAL")
            import time
            time.sleep(2)
            if self._state != "disabled":
                logger.info("Tentativo di riavvio del thread audio...")
                self._audio_thread = threading.Thread(target=self._audio_loop, daemon=True)
                self._audio_thread.start()

    def _process_text(self, text, is_voice=False):
        if not text or not text.strip():
            return
            
        logger.info(f"[Testo Riconosciuto]: {text} (is_voice={is_voice})")
        
        # Se l'input proviene dal microfono (STT), aggiungi il messaggio utente alla GUI.
        # Se proviene da digitazione su GUI, il bubble utente è già stato aggiunto in _on_send_text!
        if is_voice and hasattr(self, '_gui_window') and self._gui_window is not None:
            GLib.idle_add(self._gui_window.add_user_message, text)
            
        try:
            self.TranscriptReceived(text, True)
        except Exception:
            pass

        self.set_state("processing")
        
        # Esecuzione tramite Pipeline Controller (speak=is_voice: l'audio TTS viene riprodotto SOLO se l'input proviene dalla voce)
        res = self.pipeline_controller.process_text_input(text, speak=is_voice)
        
        # Gestione risposte non stramate (Fast-Path o Fallback TTS)
        if res.get("fast_path") and res.get("response"):
            resp = res.get("response")
            # Se la risposta è avvenuta da tastiera (speak=False), _on_tts_engine non è stato chiamato: aggiungi il bubble ora
            if not is_voice and hasattr(self, '_gui_window') and self._gui_window is not None:
                GLib.idle_add(self._gui_window.add_assistant_message, resp)
            try:
                self.ResponseTokenStreamed(resp, True)
            except Exception:
                pass
        elif not res.get("fast_path") and not res.get("response"):
            resp = f"Ho ascoltato: {text}"
            if is_voice:
                logger.info(f"[TTS Fallback] Sintesi vocale per: '{text}'")
                self.tts_manager.speak(resp)
            if hasattr(self, '_gui_window') and self._gui_window is not None:
                GLib.idle_add(self._gui_window.add_assistant_message, resp)
            try:
                self.ResponseTokenStreamed(resp, True)
            except Exception:
                pass
        
        if hasattr(self, '_gui_window') and self._gui_window is not None:
            def _reset_bubble():
                if hasattr(self._gui_window, 'current_assistant_bubble'):
                    self._gui_window.current_assistant_bubble = None
                return False
            GLib.idle_add(_reset_bubble)

        if not getattr(self.audio_player, 'is_playing', False) and not (is_voice and self._state == "speaking"):
            GLib.idle_add(self.set_state, "idle")

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
        logger.info("Uscita.")
        sys.exit(0)
