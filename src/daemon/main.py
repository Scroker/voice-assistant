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

        # Settings (populated by DaemonRuntimeManager.load_settings)
        self.settings = None
        self.wakeword = ""
        self.provider_name = ""
        self.model_name = ""
        self.hardware = "cpu"
        self.models_dir = ""
        self.language = "it"
        self.extra_config: dict = {}
        self.vosk_ww_model = ""

        # Wake-word engine (populated by DaemonRuntimeManager.initialize_wakeword in a thread)
        self.ww_provider = None
        self.ww_model = None
        self.ww_recognizer = None

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
    def DownloadProgress(self, provider: str, model_name: str, percent: int):
        pass

    @dbus_signal
    def TranscriptReceived(self, text: str, is_final: bool):
        pass

    @dbus_signal
    def ResponseTokenStreamed(self, token: str, is_complete: bool):
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
        return self.provider_manager.get_available_models(provider)

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

    def ShowWindow(self):
        """Metodo D-Bus per lanciare la finestra interattiva dell'assistente (app separata)."""
        logger.info("[D-Bus] Richiesta apertura finestra interattiva assistente.")
        import subprocess
        daemon_dir = os.path.dirname(os.path.abspath(__file__))
        ext_dir = os.path.dirname(daemon_dir)
        gui_start = os.path.join(ext_dir, "gui", "start.sh")
        if os.path.exists(gui_start):
            subprocess.Popen(["bash", gui_start])
        else:
            logger.warning(f"[ShowWindow] GUI start.sh non trovato: {gui_start}")

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

    @staticmethod
    def _run_mcp_operation(operation, *args):
        """Resolve an MCP coroutine in the synchronous dasbus handler thread."""
        return asyncio.run(operation(*args))

    def get_marketplace_featured(self) -> str:
        """Returns the featured MCP servers JSON list."""
        if not hasattr(self, 'mcp_manager') or self.mcp_manager is None:
            return json.dumps([])
        return self._run_mcp_operation(self.mcp_manager.get_marketplace_featured) if hasattr(self.mcp_manager, 'get_marketplace_featured') else json.dumps([])

    def search_marketplace(self, query: str) -> str:
        """Searches the marketplace for a query string."""
        if not hasattr(self, 'mcp_manager') or self.mcp_manager is None:
            return json.dumps([])
        if hasattr(self.mcp_manager, 'search_marketplace'):
            return self._run_mcp_operation(self.mcp_manager.search_marketplace, query)
        return json.dumps([])

    def get_server_details(self, server_name: str) -> str:
        """Returns the details of a given MCP server."""
        if not hasattr(self, 'mcp_manager') or self.mcp_manager is None:
            return json.dumps({})
        if hasattr(self.mcp_manager, 'get_server_details'):
            return self._run_mcp_operation(self.mcp_manager.get_server_details, server_name)
        return json.dumps({})

    def get_marketplace_categories(self) -> str:
        """Returns the available marketplace categories."""
        if not hasattr(self, 'mcp_manager') or self.mcp_manager is None:
            return json.dumps([])
        if hasattr(self.mcp_manager, 'get_marketplace_categories'):
            return self._run_mcp_operation(self.mcp_manager.get_marketplace_categories)
        return json.dumps([])

    def filter_marketplace_by_category(self, category: str) -> str:
        """Returns the servers matching a category."""
        if not hasattr(self, 'mcp_manager') or self.mcp_manager is None:
            return json.dumps([])
        if hasattr(self.mcp_manager, 'filter_marketplace_by_category'):
            return self._run_mcp_operation(self.mcp_manager.filter_marketplace_by_category, category)
        return json.dumps([])

    def install_mcp_server(self, server_name: str, server_config: str, env_vars: str = ""):
        """Installs an MCP server via the configured manager."""
        if not hasattr(self, 'mcp_manager') or self.mcp_manager is None:
            return False, "MCP manager non inizializzato"
        if hasattr(self.mcp_manager, 'install_mcp_server'):
            return self._run_mcp_operation(self.mcp_manager.install_mcp_server, server_name, server_config, env_vars)
        return False, "install_mcp_server non supportato"

    def uninstall_mcp_server(self, server_name: str):
        """Uninstalls a configured MCP server."""
        if not hasattr(self, 'mcp_manager') or self.mcp_manager is None:
            return False, "MCP manager non inizializzato"
        if hasattr(self.mcp_manager, 'uninstall_mcp_server'):
            return self._run_mcp_operation(self.mcp_manager.uninstall_mcp_server, server_name)
        return False, "uninstall_mcp_server non supportato"

    def test_mcp_server(self, server_name: str):
        """Runs a quick smoke test for a configured MCP server."""
        if not hasattr(self, 'mcp_manager') or self.mcp_manager is None:
            return False, "MCP manager non inizializzato"
        if hasattr(self.mcp_manager, 'test_mcp_server'):
            return self._run_mcp_operation(self.mcp_manager.test_mcp_server, server_name)
        return False, "test_mcp_server non supportato"

    def update_server_config(self, server_name: str, env_vars: str, enabled: bool):
        """Updates a server's runtime config in the manager."""
        if not hasattr(self, 'mcp_manager') or self.mcp_manager is None:
            return False, "MCP manager non inizializzato"
        if hasattr(self.mcp_manager, 'update_server_config'):
            return self._run_mcp_operation(self.mcp_manager.update_server_config, server_name, env_vars, enabled)
        return False, "update_server_config non supportato"

    def get_installed_servers(self) -> str:
        """Returns the installed MCP servers list."""
        if not hasattr(self, 'mcp_manager') or self.mcp_manager is None:
            return json.dumps([])
        if hasattr(self.mcp_manager, 'get_installed_servers'):
            return self._run_mcp_operation(self.mcp_manager.get_installed_servers)
        return json.dumps([])

    def _on_llm_token(self, token: str):
        """Callback invocata a ogni token generato dall'LLM; emette il segnale D-Bus."""
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
        return self.assistant_runtime.reset_wakeword_recognizer()

    def trigger_assistant(self):
        return self.assistant_runtime.trigger_assistant()

    def _audio_loop(self):
        return self.assistant_runtime._audio_loop()

    def _process_text(self, text, is_voice=False):
        return self.assistant_runtime._process_text(text, is_voice=is_voice)
if __name__ == '__main__':
    setup_logger()
    install_global_exception_hooks()
    assistant = VoiceAssistant()
    register_dbus_service(assistant)
    run_event_loop()
