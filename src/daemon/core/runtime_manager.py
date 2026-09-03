"""Startup helpers for daemon initialization without keeping all boot logic in main.py."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading

import notify2
from gi.repository import GLib, Gio

from audio.player import AudioPlayer
from services.tts_service import TTSServiceManager
from services.llm_service import LLMServiceManager
from core.daemon_protocol import DaemonOwner
from core.pipeline import PipelineController
from core.state import StateMachine

logger = logging.getLogger("VoiceAssistant.RuntimeManager")


class DaemonRuntimeManager:
    """Encapsulates initialization of settings, wakeword, services and pipeline."""

    def __init__(self, owner: DaemonOwner):
        self.owner = owner

    def register_gresource(self):
        res_file = os.path.expanduser("~/.local/share/gnome-shell/extensions/voice-assistant@scroker.github.io/org.gnome.shell.extensions.voice-assistant.gresource")
        if os.path.exists(res_file):
            try:
                resource = Gio.Resource.load(res_file)
                Gio.resources_register(resource)
                logger.info(f"Registrata risorsa gresource in daemon: {res_file}")
            except Exception as e:
                logger.warning(f"Impossibile registrare gresource in daemon: {e}")

    def load_settings(self):
        self.owner.settings = Gio.Settings.new("org.gnome.shell.extensions.voice-assistant")
        self.owner.wakeword = self.owner.settings.get_string("wakeword")
        self.owner.provider_name = self.owner.settings.get_string("stt-provider")
        self.owner.model_name = self.owner.settings.get_string("stt-model")
        self.owner.hardware = self.owner.settings.get_string("stt-hardware")
        self.owner.models_dir = self.owner.settings.get_string("models-dir")
        self.owner.model_manager.idle_timeout_sec = self.owner.settings.get_int("idle-unload-timeout")
        self.owner.model_manager.set_idle_timeouts({
            "stt": self.owner.settings.get_int("stt-idle-unload-timeout"),
            "llm": self.owner.settings.get_int("llm-idle-unload-timeout"),
            "tts": self.owner.settings.get_int("tts-idle-unload-timeout"),
        })

        self.owner.language = self.owner.settings.get_string("language")
        if not self.owner.language or self.owner.language.strip() == "":
            import locale
            env_lang = os.environ.get("LANG", "") or os.environ.get("LC_MESSAGES", "")
            sys_loc = (locale.getdefaultlocale()[0] or "").lower()
            full_lang = (env_lang or sys_loc).lower()
            if full_lang.startswith("it"):
                self.owner.language = "it"
            elif full_lang.startswith("en"):
                self.owner.language = "en"
            else:
                self.owner.language = "it"
            try:
                self.owner.settings.set_string("language", self.owner.language)
            except Exception:
                pass

        self.owner.vosk_ww_model = "vosk-model-small-it-0.22" if self.owner.language == "it" else "vosk-model-small-en-us-0.15"

        try:
            extra_str = self.owner.settings.get_string("stt-extra")
            self.owner.extra_config = json.loads(extra_str) if extra_str else {}
        except json.JSONDecodeError:
            self.owner.extra_config = {}

        self.owner.settings.connect("changed::language", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::wakeword", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::stt-provider", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::stt-model", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::stt-hardware", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::stt-extra", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::models-dir", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::idle-unload-timeout", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::stt-idle-unload-timeout", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::llm-idle-unload-timeout", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::tts-idle-unload-timeout", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::mcp-registry-url", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::mcp-enabled", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::enabled", self.owner.on_settings_changed)

    def initialize_notifications(self):
        try:
            notify2.init("Voice Assistant")
        except Exception as e:
            logger.warning(f"Impossibile inizializzare notify2: {e}")

    def initialize_wakeword(self):
        def _load_ww():
            from providers.vosk_provider import VoskProvider
            from vosk import KaldiRecognizer
            try:
                def progress_cb(pct):
                    try:
                        self.owner.DownloadProgress(pct)
                    except Exception:
                        pass

                self.owner.ww_provider = VoskProvider(self.owner.vosk_ww_model, "cpu", {}, progress_callback=progress_cb, models_dir=self.owner.models_dir)
                self.owner.ww_model = self.owner.ww_provider.model
                self.owner.ww_recognizer = KaldiRecognizer(self.owner.ww_model, 16000)
                logger.info(f"Motore Wake Word (Vosk: {self.owner.vosk_ww_model}) inizializzato con successo.")
            except Exception as e:
                logger.error(f"Errore inizializzazione Wake Word: {e}", exc_info=True)
                self.owner.ww_recognizer = None

        threading.Thread(target=_load_ww, daemon=True).start()

    def initialize_services(self):
        self.owner.audio_player = AudioPlayer(on_playback_finished=self.owner._on_playback_finished)
        self.owner.audio_player.start()

        model_manager = getattr(self.owner, "model_manager", None)
        tts_kwargs = {"audio_player": self.owner.audio_player}
        if model_manager:
            tts_kwargs["model_manager"] = model_manager
        self.owner.tts_manager = TTSServiceManager(**tts_kwargs)

        try:
            from mcp.manager import MCPManager
            self.owner.mcp_manager = MCPManager(
                registry_url=self.owner.settings.get_string("mcp-registry-url"),
            )
            self.owner.mcp_manager.enabled = self.owner.settings.get_boolean("mcp-enabled")
            asyncio.run(self.owner.mcp_manager.initialize())
        except Exception as e:
            logger.warning(f"Inizializzazione MCPManager in main.py: {e}")
            self.owner.mcp_manager = None

        llm_kwargs = {"settings_observer": self.owner, "mcp_manager": self.owner.mcp_manager}
        if model_manager:
            llm_kwargs["model_manager"] = model_manager
        self.owner.llm_service = LLMServiceManager(**llm_kwargs)

    def initialize_pipeline(self):
        self.owner.state_machine = StateMachine()
        self.owner.state_machine.add_callback(self.owner.set_state)

        def _on_tts_engine(text):
            try:
                self.owner.ResponseTokenStreamed(text, True)
            except Exception:
                pass
            if self.owner.tts_manager.speak(text):
                self.owner.set_state("speaking")

        self.owner.pipeline_controller = PipelineController(
            state_machine=self.owner.state_machine,
            audio_player=self.owner.audio_player,
            llm_streamer=lambda prompt: self.owner.llm_service.stream_tokens(prompt),
            tts_engine=_on_tts_engine,
            mcp_manager=self.owner.mcp_manager,
        )
        self.owner.pipeline_controller.on_token_callback = self.owner._on_llm_token
        self.owner.pipeline_controller.fast_path.intent_handler = self.owner._handle_fast_path_intent

    def start_background_load(self):
        self.owner._load_id = 1
        threading.Thread(target=self.owner.load_provider, args=(self.owner._load_id,), daemon=True).start()

        self.owner._audio_thread = threading.Thread(target=self.owner._audio_loop, daemon=True)
        self.owner._audio_thread.start()

        self.owner._model_idle_watch_id = GLib.timeout_add_seconds(
            30,
            self._check_model_idle,
        )

        is_enabled = self.owner.settings.get_boolean("enabled")
        self.owner.set_state("idle" if is_enabled else "disabled")

        self._report_initial_context()

    def ensure_stt_provider(self, target_state: str):
        """Reload the STT provider on demand after idle memory reclamation."""
        if getattr(self.owner, "provider", None) or getattr(self.owner, "_stt_load_pending", False):
            return

        self.owner._stt_load_pending = True
        self.owner._pending_state_after_provider_load = target_state
        self.owner._load_id = getattr(self.owner, "_load_id", 0) + 1
        threading.Thread(
            target=self.owner.load_provider,
            args=(self.owner._load_id,),
            daemon=True,
        ).start()

    def _check_model_idle(self):
        try:
            self.owner.model_manager.check_idle_and_purge()
        except Exception:
            logger.exception("Errore durante il controllo idle dei modelli")
        return GLib.SOURCE_CONTINUE

    def _report_initial_context(self):
        from core.logger import ErrorCollector
        ErrorCollector.set_context_dict({
            "stt_provider": self.owner.provider_name,
            "stt_model": self.owner.model_name,
            "hardware": self.owner.hardware,
            "wakeword": self.owner.wakeword,
            "language": self.owner.language,
            "state": "idle" if self.owner.settings.get_boolean("enabled") else "disabled",
        })

    def bootstrap(self):
        self.register_gresource()
        self.load_settings()
        self.initialize_notifications()
        self.initialize_wakeword()
        self.initialize_services()
        self.initialize_pipeline()
        self.start_background_load()
