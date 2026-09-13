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
from typing import Any, Dict, Tuple

from dasbus.server.interface import dbus_interface, dbus_signal
try:
    import sounddevice as sd
except ImportError:  # pragma: no cover - optional dependency in test environments
    sd = None
from providers import get_provider
import notify2

from audio.player import AudioPlayer
from services.tts_service import TTSServiceManager
from services.llm_service import LLMServiceManager
from core.pipeline import PipelineController
from core.state import StateMachine
from core.power import PowerInhibitor
from core.audio_runtime import AudioRuntimeController
from core.lifecycle import DaemonLifecycle
from core.provider_manager import ProviderManager
from core.service_bootstrap import register_dbus_service, run_event_loop
from core.runtime_manager import DaemonRuntimeManager
from core.assistant_runtime import AssistantRuntimeController
from core.model_manager import ModelManager
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

@dbus_interface("org.local.VoiceAssistant")
class VoiceAssistant(object):
    def __init__(self):
        self._daemon_start_time = time.time()
        self._inhibitor = PowerInhibitor()

        # Core state
        self._state = "disabled"
        self._listening = False
        self._stream = None
        self.q = q

        # Download / cancel tracking
        self._downloading_models: dict = {}
        self._cancel_requests: set = set()
        self._active_notifs: dict = {}

        # Dipendenze Python opzionali mancanti (rilevate al bootstrap)
        self._missing_deps: list = []

        # Settings (populated by DaemonRuntimeManager.load_settings)
        self.settings = None
        self.wakeword = ""
        self.provider_name = ""
        self.model_name = ""
        self.hardware = "cpu"
        self.models_dir = ""
        self.language = ""
        self.extra_config: dict = {}
        self.vosk_ww_model = ""

        # Wake-word engine selection
        self.wakeword_engine = "vosk"   # "vosk" | "openwakeword"
        self.oww_model_name = "alexa"

        # Vosk wake-word (populated by DaemonRuntimeManager._load_vosk_ww in a thread)
        self.ww_provider = None
        self.ww_model = None
        self.ww_recognizer = None

        # OpenWakeWord (populated by DaemonRuntimeManager._load_oww in a thread)
        self.oww_model_instance = None
        self._oww_buffer: list = []

        # Sherpa-ONNX keyword spotter (populated by DaemonRuntimeManager._load_sherpa_ww)
        self.sherpa_ww_model_dir = ""
        self.sherpa_spotter = None
        self.sherpa_stream = None

        # STT provider (populated by load_provider)
        self.provider = None
        self._stt_load_pending = False
        self._pending_state_after_provider_load = None
        self._load_id = 0

        # Audio processing state
        self.audio_filter = None
        self.audio_player = None
        self._audio_thread = None
        self._ignore_audio_until: float = 0.0
        self._listening_start_time = None
        self._last_speech_time = None
        self._last_partial_text = ""
        self._last_partial_change_time = None
        self._reload_timer = None

        # Services (populated by DaemonRuntimeManager.initialize_services / initialize_pipeline)
        self.tts_manager = None
        self.mcp_manager = None
        self.llm_service = None
        self.state_machine = None
        self.pipeline_controller = None
        self._model_idle_watch_id = None

        self.model_manager = ModelManager()
        self.lifecycle = DaemonLifecycle(self)
        self.provider_manager = ProviderManager(self)
        self.runtime_manager = DaemonRuntimeManager(self)
        self.assistant_runtime = AssistantRuntimeController(self)
        self.runtime_manager.bootstrap()

    def _handle_fast_path_intent(self, intent_name: str, params: Dict[str, Any], text: str = "") -> Tuple[bool, str]:
        return self.assistant_runtime._handle_fast_path_intent(intent_name, params, text)

    def get(self, key: str, default: Any = None) -> Any:
        return self.assistant_runtime.get(key, default)

    def _schedule_reload(self):
        return self.assistant_runtime._schedule_reload()

    def on_settings_changed(self, settings, key):
        return self.assistant_runtime.on_settings_changed(settings, key)

    def _show_notification(self, notif):
        return self.provider_manager._show_notification(notif)

    def _has_installed_models(self) -> bool:
        return self.provider_manager.has_installed_models()

    def load_provider(self, load_id):
        return self.provider_manager.load_provider(load_id)

    @dbus_signal
    def StateChanged(self, new_state: str):
        pass

    @dbus_signal
    def DependencyRequired(self, package: str, description: str, is_critical: bool):
        pass

    @dbus_signal
    def DownloadProgress(self, provider: str, model_name: str, percent: int):
        pass

    @dbus_signal
    def TranscriptReceived(self, text: str, is_final: bool):
        pass

    @dbus_signal
    def ResponseTokenStreamed(self, token: str, is_complete: bool):
        pass

    @dbus_signal
    def SpeakerEnrollmentProgress(self, progress: float, level: float):
        pass

    @dbus_signal
    def SpeakerEnrollmentFinished(self, success: bool, profile_id: str, message: str):
        pass

    @dbus_signal
    def SpeakerIdentified(self, profile_name: str, score: float, status: str, overlap_detected: bool):
        pass

    @dbus_signal
    def SpeakerRejected(self, reason: str, message: str):
        pass

    @dbus_signal
    def ConversationCreated(self, context_id: str, reason: str):
        pass

    @dbus_signal
    def ConversationToken(self, context_id: str, token: str, is_complete: bool):
        pass

    @dbus_signal
    def ConversationTranscript(self, context_id: str, text: str, is_final: bool):
        pass

    def GetMissingDependencies(self) -> str:
        """Ritorna la lista JSON delle dipendenze mancanti (Python e di sistema) aggiornata."""
        if hasattr(self, 'runtime_manager') and hasattr(self.runtime_manager, 'refresh_missing_deps'):
            try:
                self.runtime_manager.refresh_missing_deps()
            except Exception as e:
                logger.warning(f"Errore durante refresh_missing_deps: {e}")
        return json.dumps(self._missing_deps)

    def GetSpeakerStatus(self) -> str:
        """Ritorna lo stato del sottosistema speaker ID (disponibilità, download in corso)."""
        ctrl = getattr(self, "speaker_id_controller", None)
        if not ctrl:
            return json.dumps({
                "available": False,
                "downloading": False,
                "download_percent": 0,
                "status": "unavailable",
                "message": "SpeakerIdController non inizializzato nel demone.",
            })
        return json.dumps(ctrl.get_status())

    def GetSpeakerProfiles(self) -> str:
        """Ritorna l'elenco dei profili vocali salvati in formato JSON."""
        ctrl = getattr(self, "speaker_id_controller", None)
        if not ctrl:
            return json.dumps([])
        return json.dumps(ctrl.get_profiles())

    def StartSpeakerEnrollment(self, display_name: str, duration_s: float) -> bool:
        """Avvia la registrazione per l'arruolamento di un nuovo profilo vocale."""
        ctrl = getattr(self, "speaker_id_controller", None)
        if not ctrl:
            self.SpeakerEnrollmentFinished(False, "", "unavailable")
            return False

        is_disabled = (str(self._state).lower() in ("disabled", "assistantstate.disabled"))
        stream_open = getattr(getattr(self, "_stream", None), "active", True)
        if is_disabled or not stream_open:
            logger.warning("StartSpeakerEnrollment rejected: state=%s, stream_open=%s", self._state, stream_open)
            self.SpeakerEnrollmentFinished(False, "", "no_audio")
            return False

        if self._state in ("listening", "speaking", "processing"):
            self.set_state("idle")
        return ctrl.start_enrollment(display_name, duration_s=duration_s)

    def CancelSpeakerEnrollment(self) -> bool:
        """Annulla la registrazione di arruolamento vocale in corso."""
        ctrl = getattr(self, "speaker_id_controller", None)
        if not ctrl:
            return False
        return ctrl.cancel_enrollment()

    def DeleteSpeakerProfile(self, profile_id: str) -> bool:
        """Elimina un profilo vocale memorizzato."""
        ctrl = getattr(self, "speaker_id_controller", None)
        if not ctrl:
            return False
        return ctrl.delete_profile(profile_id)

    def notify_dependency_required(
        self,
        package: str,
        description: str,
        is_critical: bool,
        dep_type: str = "pip",
        system_packages: dict | None = None,
        mcp_server: str | None = None,
    ) -> None:
        """Registra una dipendenza mancante ed emette il segnale D-Bus corrispondente."""
        entry = {
            "package": package,
            "description": description,
            "is_critical": is_critical,
            "type": dep_type,
            "system_packages": system_packages or {},
            "mcp_server": mcp_server,
        }
        existing = next(
            (item for item in self._missing_deps if item.get("package") == package and item.get("type", "pip") == dep_type),
            None
        )
        if not existing:
            self._missing_deps.append(entry)
        else:
            existing.update(entry)
        try:
            self.DependencyRequired(package, description, is_critical)
        except Exception:
            pass

    def emit_download_progress(self, provider: str, model_name: str, percent: int):
        self.lifecycle.emit_download_progress(provider, model_name, percent)

    def _ensure_pipewire_aec(self):
        if not hasattr(self, '_audio_runtime'):
            self._audio_runtime = AudioRuntimeController(self, q, audio_callback)
        self._audio_runtime.ensure_pipewire_aec()

    def _get_input_device(self):
        if not hasattr(self, '_audio_runtime'):
            self._audio_runtime = AudioRuntimeController(self, q, audio_callback)
        return self._audio_runtime.get_input_device()

    def _create_stream(self):
        if not hasattr(self, '_audio_runtime'):
            self._audio_runtime = AudioRuntimeController(self, q, audio_callback)
        self._audio_runtime.create_stream()

    def _close_stream(self):
        if hasattr(self, '_audio_runtime'):
            self._audio_runtime.close_stream()

    def set_state(self, state):
        self.lifecycle.set_state(state)

    def GetState(self) -> str:
        """Ritorna lo stato attuale dell'assistente vocale."""
        return str(self._state)

    def ToggleListening(self) -> bool:
        """Metodo chiamato dall'estensione GNOME quando l'utente clicca sull'icona della barra superiore."""
        return self.ToggleListeningInContext("voice")

    def TriggerListening(self) -> bool:
        """Avvia o forza l'ascolto vocale immediato (Push-to-Talk da GUI/Estensione)."""
        logger.info("[D-Bus] Richiesta ascolto vocale immediato da GUI/Estensione.")
        return self.TriggerListeningInContext("voice")

    def ToggleListeningInContext(self, context_id: str) -> bool:
        """Alterna l'ascolto vocale legato a uno specifico contesto di chat."""
        if self._state == "disabled":
            logger.info("Abilitazione dell'assistente.")
            self.settings.set_boolean("enabled", True)
            target_ctx = context_id if (hasattr(self, 'context_manager') and self.context_manager and self.context_manager.is_valid_chat_id(context_id)) else "voice"
            self.trigger_assistant(origin="manual", context_id=target_ctx)
            return True
        elif self._state in ("listening", "speaking", "processing"):
            logger.info("Interruzione assistente e ritorno in idle.")
            if hasattr(self, 'pipeline_controller') and self.pipeline_controller:
                self.pipeline_controller.cancel_pipeline()
            elif hasattr(self, 'audio_player') and self.audio_player:
                self.audio_player.stop_playback()
            self.set_state("idle")
            return False
        else:
            target_ctx = context_id if (hasattr(self, 'context_manager') and self.context_manager and self.context_manager.is_valid_chat_id(context_id)) else "voice"
            self.trigger_assistant(origin="manual", context_id=target_ctx)
            return True

    def TriggerListeningInContext(self, context_id: str) -> bool:
        """Avvia o forza l'ascolto vocale immediato legato a uno specifico contesto di chat."""
        logger.info(f"[D-Bus] Richiesta ascolto vocale immediato per contesto: {context_id}")
        if self._state == "disabled":
            self.settings.set_boolean("enabled", True)
        target_ctx = context_id if (hasattr(self, 'context_manager') and self.context_manager and self.context_manager.is_valid_chat_id(context_id)) else "voice"
        self.trigger_assistant(origin="manual", context_id=target_ctx)
        return True

    def GetAvailableModels(self, provider: str) -> str:
        """Ritorna una stringa JSON contenente la lista dei modelli disponibili per il provider indicato."""
        return self.provider_manager.get_available_models(provider)

    def GetInstalledModels(self, provider: str = "") -> str:
        """Ritorna una stringa JSON contenente la lista dei modelli installati dal manifest."""
        return self.provider_manager.get_installed_models(provider)

    def GetDownloadingModels(self) -> str:
        """Ritorna una stringa JSON con i modelli attualmente in fase di scaricamento e la relativa percentuale."""
        return json.dumps(self._downloading_models)

    def GetResourceMetrics(self) -> str:
        """Ritorna metriche di memoria del daemon e dei modelli in-process in formato JSON."""
        return json.dumps(self.model_manager.get_resource_metrics())

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
                daemon_start_time=self._daemon_start_time
            )
            return bundle_path
        except Exception as e:
            logger.error(f"Errore durante la generazione del bundle diagnostico: {e}", exc_info=True)
            ErrorCollector.record_error(*sys.exc_info(), component="VoiceAssistant.DiagnosticBundler")
            return ""

    def DownloadModel(self, provider: str, model_name: str) -> bool:
        """Avvia lo scaricamento di un modello in background via D-Bus senza cambiare il modello in uso."""
        return self.provider_manager.download_model(provider, model_name)

    def _cleanup_partial_download(self, provider: str, model_name: str):
        self.provider_manager.cleanup_partial_download(provider, model_name)

    def CancelDownload(self, provider: str, model_name: str) -> bool:
        """Annulla lo scaricamento di un modello in corso."""
        return self.provider_manager.cancel_download(provider, model_name)

    def DeleteModel(self, provider: str, model_name: str) -> bool:
        """Elimina un modello scaricato dal disco via D-Bus."""
        return self.provider_manager.delete_model(provider, model_name)

    def ShowWindow(self):
        """Metodo D-Bus per lanciare la finestra interattiva dell'assistente (app separata)."""
        logger.info("[D-Bus] Richiesta apertura finestra interattiva assistente.")
        self._launch_gui()

    def OpenSettings(self):
        """Metodo D-Bus per aprire il pannello di preferenze dell'assistente vocale."""
        logger.info("[D-Bus] Richiesta apertura finestra impostazioni assistente.")
        self._launch_gui("--open-settings")

    def _launch_gui(self, *args):
        """Lancia l'applicazione GUI con argomenti opzionali."""
        logger.info(f"[_launch_gui] Richiesta apertura finestra interattiva (args={args}).")
        import subprocess
        daemon_dir = os.path.dirname(os.path.abspath(__file__))
        ext_dir = os.path.dirname(daemon_dir)
        cmd_args = list(args) if args else []

        candidates = [
            os.path.join(ext_dir, "gui", "start.sh"),
            os.path.expanduser("~/.local/share/gnome-shell/extensions/voice-assistant@mkswap.github.io/gui/start.sh"),
            os.path.expanduser("~/.local/share/gnome-shell/extensions/voice-assistant@scroker.github.io/gui/start.sh"),
        ]
        gui_start = next((p for p in candidates if os.path.exists(p)), None)

        env = os.environ.copy()
        runtime_dir = env.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        if "WAYLAND_DISPLAY" not in env:
            for name in ("wayland-0", "wayland-1"):
                if os.path.exists(os.path.join(runtime_dir, name)):
                    env["WAYLAND_DISPLAY"] = name
                    break
        if "DISPLAY" not in env:
            env["DISPLAY"] = ":0"

        if gui_start:
            cmd = ["bash", gui_start] + cmd_args
            logger.info(f"[_launch_gui] Esecuzione: {cmd}")
            subprocess.Popen(cmd, env=env)
        else:
            main_candidates = [
                os.path.join(ext_dir, "gui", "main.py"),
                os.path.expanduser("~/.local/share/gnome-shell/extensions/voice-assistant@mkswap.github.io/gui/main.py"),
                os.path.expanduser("~/.local/share/gnome-shell/extensions/voice-assistant@scroker.github.io/gui/main.py"),
            ]
            gui_main = next((p for p in main_candidates if os.path.exists(p)), None)
            if gui_main:
                cmd = [sys.executable, gui_main] + cmd_args
                logger.info(f"[_launch_gui] Fallback esecuzione: {cmd}")
                subprocess.Popen(cmd, env=env)
            else:
                logger.warning(f"[_launch_gui] GUI non trovata in {candidates}")

    def ProcessTextInput(self, text: str):
        """Metodo D-Bus per inviare testo direttamente alla pipeline dell'assistente (senza sintesi audio)."""
        logger.info(f"[D-Bus] Ricevuto testo da input GUI: '{text}'")
        target_ctx = None
        if hasattr(self, 'context_manager') and self.context_manager:
            convs = self.context_manager.list_conversations()
            if convs:
                target_ctx = convs[0]["id"]
            else:
                target_ctx = self.context_manager.create()
        if not target_ctx:
            target_ctx = "voice"
        import threading
        threading.Thread(target=self.assistant_runtime.enqueue_request, args=(text, False, target_ctx), daemon=True).start()

    def ProcessTextInContext(self, text: str, context_id: str):
        """Metodo D-Bus per inviare testo a una chat specifica."""
        logger.info(f"[D-Bus] Ricevuto testo da input GUI per contesto {context_id}: '{text}'")
        if not hasattr(self, 'context_manager') or not self.context_manager or not self.context_manager.is_valid_chat_id(context_id):
            logger.warning(f"ProcessTextInContext rifiutato per context_id non valido: {context_id}")
            return
        import threading
        threading.Thread(target=self.assistant_runtime.enqueue_request, args=(text, False, context_id), daemon=True).start()

    @staticmethod
    def _run_mcp_operation(operation, *args):
        """Resolve an MCP coroutine in the synchronous dasbus handler thread."""
        from core.async_bridge import run_async
        return run_async(operation(*args))

    def get_marketplace_featured(self) -> str:
        """Returns the featured MCP servers JSON list."""
        if not hasattr(self, 'mcp_manager') or self.mcp_manager is None:
            return json.dumps([])
        return self._run_mcp_operation(self.mcp_manager.get_marketplace_featured) if hasattr(self.mcp_manager, 'get_marketplace_featured') else json.dumps([])

    def GetMarketplaceFeatured(self) -> str:
        return self.get_marketplace_featured()

    def search_marketplace(self, query: str) -> str:
        """Searches the marketplace for a query string."""
        if not hasattr(self, 'mcp_manager') or self.mcp_manager is None:
            return json.dumps([])
        if hasattr(self.mcp_manager, 'search_marketplace'):
            return self._run_mcp_operation(self.mcp_manager.search_marketplace, query)
        return json.dumps([])

    def SearchMarketplace(self, query: str) -> str:
        return self.search_marketplace(query)

    def get_server_details(self, server_name: str) -> str:
        """Returns the details of a given MCP server."""
        if not hasattr(self, 'mcp_manager') or self.mcp_manager is None:
            return json.dumps({})
        if hasattr(self.mcp_manager, 'get_server_details'):
            return self._run_mcp_operation(self.mcp_manager.get_server_details, server_name)
        return json.dumps({})

    def GetServerDetails(self, server_name: str) -> str:
        return self.get_server_details(server_name)

    def get_marketplace_categories(self) -> str:
        """Returns the available marketplace categories."""
        if not hasattr(self, 'mcp_manager') or self.mcp_manager is None:
            return json.dumps([])
        if hasattr(self.mcp_manager, 'get_marketplace_categories'):
            return self._run_mcp_operation(self.mcp_manager.get_marketplace_categories)
        return json.dumps([])

    def GetMarketplaceCategories(self) -> str:
        return self.get_marketplace_categories()

    def filter_marketplace_by_category(self, category: str) -> str:
        """Returns the servers matching a category."""
        if not hasattr(self, 'mcp_manager') or self.mcp_manager is None:
            return json.dumps([])
        if hasattr(self.mcp_manager, 'filter_marketplace_by_category'):
            return self._run_mcp_operation(self.mcp_manager.filter_marketplace_by_category, category)
        return json.dumps([])

    def FilterMarketplaceByCategory(self, category: str) -> str:
        return self.filter_marketplace_by_category(category)

    def install_mcp_server(self, server_name: str, server_config: str, env_vars: str = ""):
        """Installs an MCP server via the configured manager."""
        if not hasattr(self, 'mcp_manager') or self.mcp_manager is None:
            return False, "MCP manager non inizializzato"
        if hasattr(self.mcp_manager, 'install_mcp_server'):
            res = self._run_mcp_operation(self.mcp_manager.install_mcp_server, server_name, server_config, env_vars)
            if hasattr(self, 'runtime_manager') and hasattr(self.runtime_manager, 'refresh_missing_deps'):
                self.runtime_manager.refresh_missing_deps()
                self.runtime_manager._notify_missing_deps_summary()
            return res
        return False, "install_mcp_server non supportato"

    def InstallMCPServer(self, server_name: str, server_config: str, env_vars: str = ""):
        return self.install_mcp_server(server_name, server_config, env_vars)

    def start_mcp_server(self, server_name: str):
        """Avvia un server MCP configurato e aggiorna la disponibilità dei tool."""
        if not hasattr(self, 'mcp_manager') or self.mcp_manager is None:
            return False, "MCP manager non inizializzato"
        if hasattr(self.mcp_manager, 'start_server'):
            res = self._run_mcp_operation(self.mcp_manager.start_server, server_name)
            if hasattr(self, 'runtime_manager') and hasattr(self.runtime_manager, 'refresh_missing_deps'):
                self.runtime_manager.refresh_missing_deps()
                self.runtime_manager._notify_missing_deps_summary()
            return res
        return False, "start_server non supportato"

    def StartMCPServer(self, server_name: str):
        return self.start_mcp_server(server_name)

    def uninstall_mcp_server(self, server_name: str):
        """Uninstalls a configured MCP server."""
        if not hasattr(self, 'mcp_manager') or self.mcp_manager is None:
            return False, "MCP manager non inizializzato"
        if hasattr(self.mcp_manager, 'uninstall_mcp_server'):
            return self._run_mcp_operation(self.mcp_manager.uninstall_mcp_server, server_name)
        return False, "uninstall_mcp_server non supportato"

    def UninstallMCPServer(self, server_name: str):
        return self.uninstall_mcp_server(server_name)

    def test_mcp_server(self, server_name: str):
        """Runs a quick smoke test for a configured MCP server."""
        if not hasattr(self, 'mcp_manager') or self.mcp_manager is None:
            return False, "MCP manager non inizializzato"
        if hasattr(self.mcp_manager, 'test_mcp_server'):
            return self._run_mcp_operation(self.mcp_manager.test_mcp_server, server_name)
        return False, "test_mcp_server non supportato"

    def TestMCPServer(self, server_name: str):
        return self.test_mcp_server(server_name)

    def update_server_config(self, server_name: str, env_vars: str, enabled: bool):
        """Updates a server's runtime config in the manager."""
        if not hasattr(self, 'mcp_manager') or self.mcp_manager is None:
            return False, "MCP manager non inizializzato"
        if hasattr(self.mcp_manager, 'update_server_config'):
            return self._run_mcp_operation(self.mcp_manager.update_server_config, server_name, env_vars, enabled)
        return False, "update_server_config non supportato"

    def UpdateServerConfig(self, server_name: str, env_vars: str, enabled: bool):
        return self.update_server_config(server_name, env_vars, enabled)

    def get_installed_servers(self) -> str:
        """Returns the installed MCP servers list."""
        if not hasattr(self, 'mcp_manager') or self.mcp_manager is None:
            return json.dumps([])
        if hasattr(self.mcp_manager, 'get_installed_servers'):
            return self._run_mcp_operation(self.mcp_manager.get_installed_servers)
        return json.dumps([])

    def GetInstalledServers(self) -> str:
        return self.get_installed_servers()

    def _reload_skill_dependents(self) -> None:
        """Refreshes every component that caches a SkillRegistry snapshot."""
        if getattr(self, "pipeline_controller", None):
            self.pipeline_controller.fast_path.reload_skills()
        if getattr(self, "assistant_runtime", None):
            self.assistant_runtime.reload_skills()

    def get_skills(self) -> str:
        """Returns every skill (built-in and custom) known to the Direct Action Engine."""
        try:
            from skills.skill_store import list_all_skills
            return json.dumps(list_all_skills())
        except Exception as e:
            logger.warning(f"Errore lettura skill: {e}")
            return json.dumps([])

    def GetSkills(self) -> str:
        return self.get_skills()

    def save_skill(self, skill_json: str) -> Tuple[bool, str]:
        """Validates and persists a custom skill, then reloads it into the running pipeline."""
        try:
            from skills.skill_store import save_user_skill
            skill = json.loads(skill_json)
            save_user_skill(skill)
            self._reload_skill_dependents()
            return True, "Skill salvata."
        except Exception as e:
            return False, str(e)

    def SaveSkill(self, skill_json: str) -> Tuple[bool, str]:
        return self.save_skill(skill_json)

    def delete_skill(self, intent: str) -> Tuple[bool, str]:
        """Deletes a custom skill by intent, then reloads the running pipeline."""
        try:
            from skills.skill_store import delete_user_skill
            if delete_user_skill(intent):
                self._reload_skill_dependents()
                return True, "Skill eliminata."
            return False, "Skill non trovata tra quelle personalizzate."
        except Exception as e:
            return False, str(e)

    def DeleteSkill(self, intent: str) -> Tuple[bool, str]:
        return self.delete_skill(intent)

    def _report_error(self, exc: Exception) -> None:
        """Raccoglie e invia a Bugzilla le eccezioni critiche del thread audio."""
        from core.logger import ErrorCollector
        from core.bug_reporter import BugReporter
        ErrorCollector.record_error(
            type(exc), exc, exc.__traceback__,
            extra_info={"thread": "audio_loop"},
            component="VoiceAssistant.AudioLoop",
            severity="ERROR",
        )
        BugReporter.submit_async(
            type(exc), exc, exc.__traceback__,
            component="VoiceAssistant.AudioLoop",
        )

    def _on_llm_token(self, token: str, context_id: str = "voice"):
        """Callback invocata a ogni token generato dall'LLM; emette i segnali D-Bus."""
        try:
            if context_id == "voice":
                self.ResponseTokenStreamed(token, False)
        except Exception:
            pass
        try:
            self.ConversationToken(context_id, token, False)
        except Exception:
            pass

    def ListConversations(self) -> str:
        """Restituisce le conversazioni GUI (JSON string)."""
        if not hasattr(self, 'context_manager') or not self.context_manager:
            return json.dumps([])
        return json.dumps(self.context_manager.list_conversations())

    def CreateConversation(self) -> str:
        """Crea una nuova conversazione e ne restituisce l'ID."""
        if not hasattr(self, 'context_manager') or not self.context_manager:
            return ""
        return self.context_manager.create()

    def DeleteConversation(self, context_id: str) -> bool:
        """Elimina una conversazione."""
        if not hasattr(self, 'context_manager') or not self.context_manager:
            return False
        deleted = self.context_manager.delete(context_id)

        # Pulizia dal vector store RAG
        try:
            sp = None
            if hasattr(self, 'pipeline_controller') and hasattr(self.pipeline_controller, 'smart_path'):
                sp = self.pipeline_controller.smart_path
            elif hasattr(self, 'assistant_runtime') and hasattr(self.assistant_runtime, 'smart_path'):
                sp = self.assistant_runtime.smart_path
            if sp and hasattr(sp, 'vector_store') and sp.vector_store:
                sp.vector_store.delete_by_metadata("context_id", context_id)
        except Exception as ex:
            logger.warning(f"Errore pulizia RAG per conversazione {context_id}: {ex}")

        return bool(deleted)

    def GetConversationMessages(self, context_id: str) -> str:
        """Restituisce i messaggi di una conversazione (JSON string)."""
        if context_id == "voice":
            return json.dumps([])
        if not hasattr(self, 'context_manager') or not self.context_manager:
            return json.dumps([])
        if not self.context_manager.is_valid_chat_id(context_id):
            return json.dumps([])
        ctx = self.context_manager.get(context_id)
        if not ctx:
            return json.dumps([])
        return json.dumps(ctx.to_dict().get("messages", []))

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
        return self.assistant_runtime.reset_wakeword_recognizer()

    def trigger_assistant(self, origin: str = "manual", context_id: str = "voice"):
        return self.assistant_runtime.trigger_assistant(origin=origin, context_id=context_id)

    def _audio_loop(self):
        return self.assistant_runtime._audio_loop()

    def _process_text(self, text, is_voice=False):
        return self.assistant_runtime._process_text(text, is_voice=is_voice)
if __name__ == '__main__':
    setup_logger()
    install_global_exception_hooks()
    assistant = VoiceAssistant()
    from core.logger import set_error_submitted_callback
    from core.bug_reporter import BugReporter
    set_error_submitted_callback(BugReporter.submit_async)
    register_dbus_service(assistant)
    run_event_loop()
