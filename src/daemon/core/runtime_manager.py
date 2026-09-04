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

_OPTIONAL_DEPS = [
    ("vosk",           "vosk",             "Riconoscimento vocale Vosk (Wake Word / STT)", False),
    ("sherpa_onnx",    "sherpa-onnx",      "Motore Wake Word Sherpa-ONNX",                False),
    ("faster_whisper", "faster-whisper",   "Trascrizione vocale Whisper",                 False),
    ("onnxruntime",    "onnxruntime",      "Runtime ONNX per modelli ML",                 False),
    ("llama_cpp",      "llama-cpp-python", "LLM locale (GGUF)",                           False),
    ("piper",          "piper-tts",        "Sintesi vocale Piper TTS",                    False),
    ("keyring",        "keyring",          "Gestione credenziali MCP",                    False),
    ("openwakeword",   "openwakeword",     "Motore Wake Word OpenWakeWord",               False),
]


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
        self.owner.wakeword_engine = self.owner.settings.get_string("wakeword-engine") or "vosk"
        self.owner.oww_model_name = self.owner.settings.get_string("oww-model") or "alexa"
        self.owner.sherpa_ww_model_dir = self.owner.settings.get_string("sherpa-ww-model-dir") or ""

        try:
            extra_str = self.owner.settings.get_string("stt-extra")
            self.owner.extra_config = json.loads(extra_str) if extra_str else {}
        except json.JSONDecodeError:
            self.owner.extra_config = {}

        self.owner.settings.connect("changed::language", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::wakeword", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::wakeword-engine", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::oww-model", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::sherpa-ww-model-dir", self.owner.on_settings_changed)
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
        engine = getattr(self.owner, 'wakeword_engine', 'vosk')
        if engine == 'openwakeword':
            threading.Thread(target=self._load_oww, daemon=True).start()
        elif engine == 'sherpa-onnx':
            threading.Thread(target=self._load_sherpa_ww, daemon=True).start()
        else:
            threading.Thread(target=self._load_vosk_ww, daemon=True).start()

    def _load_vosk_ww(self):
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
            logger.error(f"Errore inizializzazione Wake Word Vosk: {e}", exc_info=True)
            self.owner.ww_recognizer = None

    def _load_sherpa_ww(self):
        try:
            import sherpa_onnx
        except ImportError:
            logger.error("sherpa-onnx non installato.")
            self.owner.sherpa_spotter = None
            self.owner.notify_dependency_required("sherpa-onnx", "Motore Wake Word Sherpa-ONNX", False)
            return

        model_dir = (getattr(self.owner, 'sherpa_ww_model_dir', '') or '').strip()
        if not model_dir:
            # Auto-download del modello KWS predefinito (gigaspeech 3.3M, English)
            models_base = getattr(self.owner, 'models_dir', '') or os.path.expanduser("~/.local/share/voice-assistant/models")
            model_dir = os.path.join(models_base, "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01")
            if not os.path.isdir(model_dir):
                self._download_sherpa_kws_model(model_dir)

        encoder = os.path.join(model_dir, "encoder-epoch-12-avg-2-chunk-16-left-64.onnx")
        decoder = os.path.join(model_dir, "decoder-epoch-12-avg-2.onnx")
        joiner  = os.path.join(model_dir, "joiner-epoch-12-avg-2.onnx")
        tokens  = os.path.join(model_dir, "tokens.txt")

        for f in (encoder, decoder, joiner, tokens):
            if not os.path.isfile(f):
                logger.error(f"File Sherpa-ONNX mancante: {f}")
                self.owner.sherpa_spotter = None
                return

        keyword = getattr(self.owner, 'wakeword', 'assistente')
        keywords_path = os.path.join(model_dir, "_kws_keyword.txt")
        try:
            with open(keywords_path, 'w', encoding='utf-8') as fh:
                fh.write(keyword + "\n")
        except Exception as e:
            logger.error(f"Impossibile scrivere keywords file Sherpa: {e}")
            self.owner.sherpa_spotter = None
            return

        try:
            spotter = sherpa_onnx.KeywordSpotter(
                tokens=tokens,
                encoder=encoder,
                decoder=decoder,
                joiner=joiner,
                keywords_file=keywords_path,
                num_threads=1,
                provider="cpu",
            )
            self.owner.sherpa_spotter = spotter
            self.owner.sherpa_stream = spotter.create_stream()
            logger.info(f"Motore Wake Word (Sherpa-ONNX, keyword: '{keyword}') inizializzato.")
        except Exception as e:
            logger.error(f"Errore inizializzazione Sherpa-ONNX: {e}", exc_info=True)
            self.owner.sherpa_spotter = None

    def _download_sherpa_kws_model(self, model_dir: str):
        import urllib.request
        base = "https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01"
        files = {
            "encoder-epoch-12-avg-2-chunk-16-left-64.onnx": f"{base}/encoder-epoch-12-avg-2-chunk-16-left-64.onnx",
            "decoder-epoch-12-avg-2.onnx": f"{base}/decoder-epoch-12-avg-2.onnx",
            "joiner-epoch-12-avg-2.onnx": f"{base}/joiner-epoch-12-avg-2.onnx",
            "tokens.txt": f"{base}/tokens.txt",
        }
        os.makedirs(model_dir, exist_ok=True)
        for fname, url in files.items():
            dest = os.path.join(model_dir, fname)
            if os.path.isfile(dest):
                continue
            logger.info(f"Download Sherpa-ONNX: {fname}...")
            try:
                urllib.request.urlretrieve(url, dest)
            except Exception as e:
                logger.error(f"Errore download {fname}: {e}")

    def _load_oww(self):
        try:
            import openwakeword
            from openwakeword.model import Model
            openwakeword.utils.download_models()
            model_name = getattr(self.owner, 'oww_model_name', 'alexa')
            self.owner.oww_model_instance = Model(
                wakeword_models=[model_name],
                inference_framework="onnx",
            )
            self.owner._oww_buffer = []
            logger.info(f"Motore Wake Word (OpenWakeWord: {model_name}) inizializzato con successo.")
        except Exception as e:
            logger.error(f"Errore inizializzazione OpenWakeWord: {e}", exc_info=True)
            self.owner.oww_model_instance = None

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

    def _probe_optional_deps(self):
        """Verifica tutte le dipendenze opzionali in un unico passaggio all'avvio."""
        import importlib
        for import_name, package_name, description, is_critical in _OPTIONAL_DEPS:
            try:
                importlib.import_module(import_name)
            except ImportError:
                logger.warning(f"Dipendenza opzionale mancante: {package_name} ({import_name})")
                self.owner.notify_dependency_required(package_name, description, is_critical)

    def _notify_missing_deps_summary(self):
        """Emette una singola notifica desktop se ci sono dipendenze opzionali mancanti."""
        deps = getattr(self.owner, '_missing_deps', [])
        if not deps:
            return
        names = ", ".join(d["package"] for d in deps)
        try:
            notif = notify2.Notification(
                "Dipendenze mancanti",
                f"Pacchetti non disponibili: {names}.\nApri l'assistente vocale per installarli.",
                "dialog-warning",
            )
            notif.show()
        except Exception as e:
            logger.warning(f"Impossibile mostrare notifica dipendenze mancanti: {e}")

    def bootstrap(self):
        self._probe_optional_deps()
        self.register_gresource()
        self.load_settings()
        self.initialize_notifications()
        self.initialize_wakeword()
        self.initialize_services()
        self.initialize_pipeline()
        self.start_background_load()
        self._notify_missing_deps_summary()
