"""Startup helpers for daemon initialization without keeping all boot logic in main.py."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import threading

import notify2
from gi.repository import GLib, Gio

from audio.player import AudioPlayer
from services.tts_service import TTSServiceManager
from services.llm_service import LLMServiceManager
from core.audio_runtime import AUDIO_FILTER_KEYS
from core.daemon_protocol import DaemonOwner
from core.pipeline import PipelineController
from core.settings import get_boolean_setting
from core.state import StateMachine
from core.data_loader import load_json_data

logger = logging.getLogger("VoiceAssistant.RuntimeManager")


def _check_portaudio() -> bool:
    try:
        import sounddevice
        return True
    except Exception:
        import ctypes.util
        return ctypes.util.find_library("portaudio") is not None


def _is_wayland() -> bool:
    return os.environ.get("XDG_SESSION_TYPE") == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))


def _check_clipboard() -> bool:
    if _is_wayland():
        return bool(shutil.which("wl-copy") and shutil.which("wl-paste"))
    return bool(shutil.which("xclip"))


def _load_optional_python_deps() -> list[tuple[str, str, str, bool]]:
    raw = load_json_data("dependencies/python_deps.json", fallback_default=[])
    if not raw:
        return []
    return [
        (
            item["import_name"],
            item["package_name"],
            item["description"],
            item.get("is_critical", False),
        )
        for item in raw
    ]


_OPTIONAL_PYTHON_DEPS = _load_optional_python_deps()
_OPTIONAL_DEPS = _OPTIONAL_PYTHON_DEPS


def _load_system_deps() -> list[dict]:
    raw = load_json_data("dependencies/system_deps.json", fallback_default=[])
    if not raw:
        return []

    is_wayland = _is_wayland()
    deps = []
    for item in raw:
        # Se è un prerequisito di un server MCP, viene gestito specificamente da _probe_mcp_deps
        if item.get("mcp_server"):
            continue

        pkg_name = item.get("package", "")
        packages = dict(item.get("packages", {}))
        provide_file = item.get("provide_file", "")

        check_type = item.get("check_type", "")
        if check_type == "portaudio":
            check_fn = _check_portaudio
        elif check_type == "clipboard":
            if not is_wayland and "package_x11" in item:
                pkg_name = item["package_x11"]
                packages = dict(item.get("packages_x11", packages))
                provide_file = item.get("provide_file_x11", provide_file)
            check_fn = _check_clipboard
        elif check_type == "command":
            commands = item.get("commands", [pkg_name])
            check_fn = (lambda cmds=commands: any(bool(shutil.which(cmd)) for cmd in cmds))
        else:
            check_fn = (lambda p=pkg_name: bool(shutil.which(p)))

        deps.append({
            "package": pkg_name,
            "description": item.get("description", ""),
            "is_critical": item.get("is_critical", False),
            "check": check_fn,
            "packages": packages,
            "provide_file": provide_file,
        })
    return deps


_SYSTEM_DEPS = _load_system_deps()


def _get_deps_notif_file() -> str:
    try:
        runtime_dir = GLib.get_user_runtime_dir()
        if runtime_dir and os.path.isdir(runtime_dir):
            return os.path.join(runtime_dir, "voice_assistant_deps_notif_id")
    except Exception:
        pass
    try:
        from core.path_utils import get_data_dir
        base = str(get_data_dir())
    except Exception:
        base = os.path.expanduser("~/.local/share/voice-assistant")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "deps_notif_id")


def _read_persisted_deps_notif_id() -> int:
    try:
        fpath = _get_deps_notif_file()
        if os.path.exists(fpath):
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content.isdigit():
                    return int(content)
    except Exception as e:
        logger.debug(f"Impossibile leggere ID notifica persistente: {e}")
    return 0


def _write_persisted_deps_notif_id(notif_id: int):
    try:
        fpath = _get_deps_notif_file()
        if notif_id <= 0:
            if os.path.exists(fpath):
                os.remove(fpath)
        else:
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(str(notif_id))
    except Exception as e:
        logger.debug(f"Impossibile scrivere ID notifica persistente: {e}")


class DaemonRuntimeManager:
    """Gestisce il caricamento e l'inizializzazione delle componenti del demone."""

    def __init__(self, owner: DaemonOwner):
        self.owner = owner
        self._gdbus_sub_id = 0
        self._gdbus_closed_sub_id = 0

    def ensure_desktop_file(self):
        """Assicura che il file .desktop sia presente in ~/.local/share/applications per il raggruppamento notifiche."""
        try:
            apps_dir = os.path.join(GLib.get_user_data_dir(), "applications")
            os.makedirs(apps_dir, exist_ok=True)
            desktop_path = os.path.join(apps_dir, "org.local.VoiceAssistant.desktop")
            if not os.path.exists(desktop_path):
                content = None
                try:
                    res_bytes = Gio.resources_lookup_data(
                        "/org/gnome/shell/extensions/voice-assistant/services/org.local.VoiceAssistant.desktop.in",
                        Gio.ResourceLookupFlags.NONE
                    )
                    content = res_bytes.get_data().decode('utf-8')
                except Exception:
                    pass

                if not content:
                    src_candidates = [
                        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "services", "org.local.VoiceAssistant.desktop.in"),
                        "/usr/share/voice-assistant/services/org.local.VoiceAssistant.desktop.in",
                    ]
                    for candidate in src_candidates:
                        if os.path.exists(candidate):
                            with open(candidate, "r", encoding="utf-8") as f:
                                content = f.read()
                            break

                if not content:
                    content = (
                        "[Desktop Entry]\n"
                        "Name=Voice Assistant\n"
                        "Name[it]=Assistente Vocale\n"
                        "Comment=Local & Offline Voice Assistant for GNOME\n"
                        "Comment[it]=Assistente vocale locale ed offline per GNOME\n"
                        "Exec=gdbus call --session --dest org.local.VoiceAssistant --object-path /org/local/VoiceAssistant --method org.local.VoiceAssistant.ShowWindow\n"
                        "Icon=vocal-assistant-icon\n"
                        "Terminal=false\n"
                        "Type=Application\n"
                        "Categories=Utility;AudioVideo;Audio;\n"
                        "Keywords=Voice;Assistant;Speech;Offline;Vosk;Whisper;\n"
                        "StartupNotify=true\n"
                        "StartupWMClass=org.local.VoiceAssistant.GUI\n"
                        "X-GNOME-UsesNotifications=true\n"
                    )

                with open(desktop_path, "w", encoding="utf-8") as f:
                    f.write(content)
                gui_desktop_path = os.path.join(apps_dir, "org.local.VoiceAssistant.GUI.desktop")
                with open(gui_desktop_path, "w", encoding="utf-8") as f:
                    f.write(content)
                logger.info(f"Creato file .desktop in {desktop_path} e {gui_desktop_path}.")
        except Exception as e:
            logger.warning(f"Impossibile assicurare file .desktop in applications: {e}")

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
        from providers import get_default_model
        from core.locale_utils import get_system_language

        raw_lang = self.owner.settings.get_string("language")
        self.owner.language = raw_lang.strip() if raw_lang and raw_lang.strip() else get_system_language()

        default_vosk = get_default_model("vosk", self.owner.language)
        default_whisper = get_default_model("whisper", self.owner.language)

        self.owner.provider_name = self.owner.settings.get_string("stt-provider") or "vosk"
        self.owner.model_name = self.owner.settings.get_string("stt-model") or default_vosk
        self.owner.hardware = self.owner.settings.get_string("stt-hardware")
        self.owner.models_dir = self.owner.settings.get_string("models-dir")

        _VALID_WHISPER_SIZES = {
            "tiny.en", "tiny", "base.en", "base", "small.en", "small",
            "medium.en", "medium", "large-v1", "large-v2", "large-v3",
            "large", "distil-large-v2", "distil-medium.en", "distil-small.en",
            "distil-large-v3", "distil-large-v3.5", "large-v3-turbo", "turbo",
        }

        if self.owner.provider_name == "vosk":
            if not self.owner.model_name.startswith("vosk"):
                logger.warning(f"Discrepanza impostazioni: Provider STT è 'vosk' ma il modello è '{self.owner.model_name}'. Correzione automatica in '{default_vosk}'.")
                self.owner.model_name = default_vosk
                try:
                    self.owner.settings.set_string("stt-model", default_vosk)
                except Exception:
                    pass
        elif self.owner.provider_name == "whisper":
            _wm = self.owner.model_name
            if _wm.startswith("vosk-") or _wm not in _VALID_WHISPER_SIZES:
                logger.warning(
                    f"Discrepanza impostazioni: Provider STT è 'whisper' ma il modello è '{_wm}' "
                    f"(non è una dimensione faster-whisper valida). Correzione automatica in '{default_whisper}'."
                )
                self.owner.model_name = default_whisper
                try:
                    self.owner.settings.set_string("stt-model", default_whisper)
                except Exception:
                    pass
        elif self.owner.provider_name in ("openai_cloud", "groq_cloud"):
            _cm = self.owner.model_name
            if _cm.startswith("vosk-") or _cm in _VALID_WHISPER_SIZES:
                default_cloud = get_default_model(self.owner.provider_name, self.owner.language)
                logger.warning(f"Discrepanza impostazioni: Provider STT è '{self.owner.provider_name}' ma il modello è '{_cm}'. Correzione automatica in '{default_cloud}'.")
                self.owner.model_name = default_cloud
                try:
                    self.owner.settings.set_string("stt-model", default_cloud)
                except Exception:
                    pass
        self.owner.model_manager.idle_timeout_sec = self.owner.settings.get_int("idle-unload-timeout")
        self.owner.model_manager.set_idle_timeouts({
            "stt": self.owner.settings.get_int("stt-idle-unload-timeout"),
            "llm": self.owner.settings.get_int("llm-idle-unload-timeout"),
            "tts": self.owner.settings.get_int("tts-idle-unload-timeout"),
        })

        try:
            self.owner.vosk_ww_model = self.owner.settings.get_string("vosk-ww-model") or get_default_model("vosk", self.owner.language)
        except Exception:
            self.owner.vosk_ww_model = get_default_model("vosk", self.owner.language)
        self.owner.wakeword_engine = self.owner.settings.get_string("wakeword-engine") or "vosk"
        self.owner.oww_model_name = self.owner.settings.get_string("oww-model") or "alexa"
        self.owner.sherpa_ww_model_dir = self.owner.settings.get_string("sherpa-ww-model-dir") or ""
        try:
            self.owner.sherpa_model = self.owner.settings.get_string("sherpa-model") or "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01"
        except Exception:
            self.owner.sherpa_model = "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01"

        try:
            extra_str = self.owner.settings.get_string("stt-extra")
            if extra_str and extra_str.strip().startswith("{"):
                self.owner.extra_config = json.loads(extra_str)
            elif extra_str and extra_str.strip():
                self.owner.extra_config = {"api_key": extra_str.strip()}
            else:
                self.owner.extra_config = {}
        except Exception:
            self.owner.extra_config = {"api_key": extra_str.strip()} if (extra_str and extra_str.strip()) else {}

        self.owner.settings.connect("changed::language", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::wakeword", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::wakeword-engine", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::oww-model", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::vosk-ww-model", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::sherpa-model", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::sherpa-ww-model-dir", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::stt-provider", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::stt-model", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::stt-hardware", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::stt-extra", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::models-dir", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::fast-path-enabled", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::medium-path-enabled", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::audio-aec-enabled", self.owner.on_settings_changed)
        for _audio_key in AUDIO_FILTER_KEYS:
            self.owner.settings.connect(f"changed::{_audio_key}", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::idle-unload-timeout", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::stt-idle-unload-timeout", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::llm-idle-unload-timeout", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::tts-idle-unload-timeout", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::mcp-registry-url", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::mcp-enabled", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::enabled", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::tts-voice", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::tts-provider", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::tts-engine", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::tts-speed", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::llm-mode", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::llm-model", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::llm-endpoint", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::llm-api-key", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::llm-system-prompt", self.owner.on_settings_changed)
        self.owner.settings.connect("changed::llm-temperature", self.owner.on_settings_changed)

    def initialize_notifications(self):
        self._gdbus_sub_id = 0
        self._gdbus_closed_sub_id = 0
        self.ensure_desktop_file()
        self.owner._last_deps_notif_id = _read_persisted_deps_notif_id()

        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self._gdbus_sub_id = bus.signal_subscribe(
                None,
                "org.freedesktop.Notifications",
                "ActionInvoked",
                "/org/freedesktop/Notifications",
                None,
                Gio.DBusSignalFlags.NONE,
                self._on_gdbus_action_invoked,
            )
            self._gdbus_closed_sub_id = bus.signal_subscribe(
                None,
                "org.freedesktop.Notifications",
                "NotificationClosed",
                "/org/freedesktop/Notifications",
                None,
                Gio.DBusSignalFlags.NONE,
                self._on_gdbus_notification_closed,
            )
            logger.info("Sottoscritti segnali ActionInvoked e NotificationClosed via GDBus.")
        except Exception as e:
            logger.warning(f"Impossibile sottoscrivere segnali notifiche via GDBus: {e}")

        try:
            notify2.init("Voice Assistant", mainloop='glib')
            logger.info("Inizializzato notify2 con mainloop glib per gestione eventi D-Bus.")
        except Exception as e:
            logger.warning(f"Impossibile inizializzare notify2 con glib mainloop: {e}, fallback su notify2 standard")
            try:
                notify2.init("Voice Assistant")
            except Exception as e2:
                logger.warning(f"Impossibile inizializzare notify2: {e2}")

    def _on_gdbus_action_invoked(self, conn, sender, path, iface, signal, params):
        try:
            nid, action_key = params.unpack()
            logger.info(f"[GDBus Notification] Azione '{action_key}' invocata per notifica ID {nid}")
            last_id = getattr(self.owner, '_last_deps_notif_id', 0)
            if last_id == 0 or nid == last_id:
                self._handle_deps_notification_action(action_key)
        except Exception as e:
            logger.error(f"Errore gestione _on_gdbus_action_invoked: {e}")

    def _on_gdbus_notification_closed(self, conn, sender, path, iface, signal, params):
        try:
            nid, reason = params.unpack()
            logger.debug(f"[GDBus Notification] Notifica ID {nid} chiusa (motivo: {reason})")
            last_id = getattr(self.owner, '_last_deps_notif_id', 0)
            if last_id and nid == last_id:
                self.owner._last_deps_notif_id = 0
                _write_persisted_deps_notif_id(0)
        except Exception as e:
            logger.error(f"Errore gestione _on_gdbus_notification_closed: {e}")

    def _handle_deps_notification_action(self, action: str):
        logger.info(f"[Notifica] Gestione azione '{action}': apertura GUI installazione.")
        launcher = getattr(self.owner, '_launch_gui', None)
        if callable(launcher):
            if action == "install":
                GLib.idle_add(launcher, "--install-deps", "--auto-install")
            else:
                GLib.idle_add(launcher, "--install-deps")
        else:
            GLib.idle_add(self.owner.ShowWindow)

    def notify_user(self, title: str, message: str, icon: str = "dialog-warning"):
        """Invia una notifica desktop all'utente via notify2 o GDBus diretto."""
        try:
            notif = notify2.Notification(title, message, icon)
            try:
                notif.set_hint_string("desktop-entry", "org.local.VoiceAssistant")
            except Exception:
                pass
            notif.show()
            return
        except Exception as e:
            logger.warning(f"Invio notifica via notify2 non riuscito ({e}), fallback su GDBus.")

        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            params = GLib.Variant(
                "(susssasa{sv}i)",
                (
                    "Voice Assistant",
                    0,
                    icon,
                    title,
                    message,
                    [],
                    {"desktop-entry": GLib.Variant("s", "org.local.VoiceAssistant")},
                    -1,
                ),
            )
            bus.call_sync(
                "org.freedesktop.Notifications",
                "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications",
                "Notify",
                params,
                GLib.VariantType("(u)"),
                Gio.DBusCallFlags.NONE,
                3000,
                None,
            )
        except Exception as e2:
            logger.warning(f"Invio notifica GDBus diretto non riuscito: {e2}")

    def fallback_to_vosk_wakeword(self, reason: str, exc: Exception | None = None):
        """Esegue il fallback automatico su Vosk, registra l'errore, invia notifica e aggiorna i settings."""
        logger.warning(f"[WakeWord Fallback] {reason} -> Passaggio automatico al motore Vosk.")

        # 1. Registra su ErrorCollector e BugReporter
        from core.logger import ErrorCollector
        if exc is not None:
            ErrorCollector.record_error(
                type(exc), exc, exc.__traceback__,
                extra_info={"reason": reason, "fallback": "vosk"},
                component="VoiceAssistant.WakeWord",
                severity="ERROR",
            )
            try:
                from core.bug_reporter import BugReporter
                BugReporter.submit_async(
                    type(exc), exc, exc.__traceback__,
                    component="VoiceAssistant.WakeWord",
                )
            except Exception:
                pass

        # 2. Mostra notifica desktop
        self.notify_user(
            "Assistente Vocale - Errore Wake Word",
            f"{reason}.\nÈ stato attivato il fallback sul motore locale Vosk.",
            icon="dialog-warning",
        )

        # 3. Aggiorna stato interno
        self.owner.wakeword_engine = 'vosk'
        self.owner.oww_model_instance = None
        self.owner._oww_buffer = []
        self.owner.sherpa_spotter = None
        self.owner.sherpa_stream = None

        # 4. Aggiorna GSettings per allineare l'interfaccia grafica
        if hasattr(self.owner, 'settings') and self.owner.settings:
            try:
                if self.owner.settings.get_string("wakeword-engine") != "vosk":
                    self.owner.settings.set_string("wakeword-engine", "vosk")
            except Exception as se:
                logger.warning(f"Impossibile aggiornare impostazione wakeword-engine: {se}")

        # 5. Inizializza il motore Vosk
        self._load_vosk_ww()

    def initialize_wakeword(self):
        engine = getattr(self.owner, 'wakeword_engine', 'vosk')
        if engine == 'openwakeword':
            threading.Thread(target=self._load_oww, daemon=True).start()
        elif engine == 'sherpa-onnx':
            threading.Thread(target=self._load_sherpa_ww, daemon=True).start()
        else:
            threading.Thread(target=self._load_vosk_ww, daemon=True).start()

    def _load_vosk_ww(self):
        try:
            from providers.vosk_provider import VoskProvider
            from vosk import KaldiRecognizer

            def progress_cb(pct):
                try:
                    self.owner.DownloadProgress(pct)
                except Exception:
                    pass

            self.owner.ww_provider = VoskProvider(self.owner.vosk_ww_model, "cpu", {}, progress_callback=progress_cb, models_dir=self.owner.models_dir)
            self.owner.ww_model = self.owner.ww_provider.model

            # Build a grammar so Vosk only tries to recognise the wakeword and
            # its phonetic variants.  Without a grammar the full language model
            # is used and a large model like vosk-model-en-us-0.22 produces
            # multi-word transcriptions that never contain just the wakeword.
            import json as _json
            wakeword = getattr(self.owner, 'wakeword', 'assistente').lower().strip()
            ww_no_h = wakeword.replace('h', '')
            ww_variants = list({wakeword, ww_no_h})
            if wakeword == "assistente":
                ww_variants += ["assistenti", "assistenza", "assiste"]
            elif "anthon" in wakeword or "anton" in wakeword:
                ww_variants += ["anthon", "anton", "antonio", "anthony"]
            # Always allow stop/interrupt words for barge-in
            ww_variants += ["stop", "basta", "zitto", "fermati", "silenzio", "interrompi", "cancella"]
            grammar = _json.dumps(list(set(ww_variants)) + ["[unk]"])

            self.owner.ww_recognizer = KaldiRecognizer(self.owner.ww_model, 16000, grammar)
            logger.info(f"Motore Wake Word (Vosk: {self.owner.vosk_ww_model}) inizializzato con successo. Grammar: {grammar}")
        except Exception as e:
            logger.error(f"Errore inizializzazione Wake Word Vosk: {e}", exc_info=True)
            self.owner.ww_recognizer = None
            from core.logger import ErrorCollector
            ErrorCollector.record_error(
                type(e), e, e.__traceback__,
                component="VoiceAssistant.WakeWord.Vosk",
                severity="CRITICAL",
            )
            self.notify_user(
                "Assistente Vocale - Errore Critico",
                f"Impossibile inizializzare il motore wakeword Vosk: {e}",
                icon="dialog-error",
            )

    def _find_sherpa_files(self, model_dir: str):
        """Trova i file encoder, decoder, joiner e tokens all'interno di model_dir."""
        if not os.path.isdir(model_dir):
            return None, None, None, None

        tokens = os.path.join(model_dir, "tokens.txt")
        if not os.path.isfile(tokens):
            tokens = None

        def find_file(prefix: str):
            candidates = [
                f"{prefix}-epoch-12-avg-2-chunk-16-left-64.onnx",
                f"{prefix}.onnx",
            ]
            for c in candidates:
                p = os.path.join(model_dir, c)
                if os.path.isfile(p):
                    return p
            try:
                files = os.listdir(model_dir)
            except OSError:
                return None

            non_int8 = [os.path.join(model_dir, f) for f in files if f.startswith(prefix) and f.endswith(".onnx") and ".int8." not in f]
            if non_int8:
                return non_int8[0]
            int8 = [os.path.join(model_dir, f) for f in files if f.startswith(prefix) and f.endswith(".onnx")]
            if int8:
                return int8[0]
            return None

        encoder = find_file("encoder")
        decoder = find_file("decoder")
        joiner = find_file("joiner")

        return encoder, decoder, joiner, tokens

    def _encode_sherpa_keyword(self, keyword: str, tokens_path: str) -> str:
        """Encode keyword(s) for Sherpa-ONNX KeywordSpotter.

        Supports two token vocabulary formats:
        - Arpabet phoneme tokens (e.g. 'AA0', 'B', 'CH') — used by zh-en and
          gigaspeech phoneme models.  Each word is looked up in the companion
          en.phone CMU dictionary; words not found are approximated via a
          simple letter->phoneme table that covers Italian/English.
        - BPE subword tokens (contain the word-boundary prefix) — legacy path
          kept for other models.

        Multiple keywords can be separated by ',' or ';'.
        Each keyword line in the output has an optional '@LABEL' suffix.
        """
        if not keyword or not os.path.isfile(tokens_path):
            return keyword

        # Detect vocabulary type
        tokens_set = set()
        is_phoneme_vocab = False
        try:
            with open(tokens_path, 'r', encoding='utf-8') as f:
                for line in f:
                    parts = line.strip().split()
                    if parts:
                        tokens_set.add(parts[0])
            # Arpabet tokens are all-caps and contain digits (e.g. AA0, AH1).
            # BPE tokens contain the word-boundary character \u2581.
            is_phoneme_vocab = any(
                t[0].isupper() and any(c.isdigit() for c in t)
                for t in tokens_set
                if len(t) >= 2
            )
        except Exception:
            return keyword

        if is_phoneme_vocab:
            # Load CMU en.phone dictionary from the model directory
            phone_dict: dict = {}
            phone_path = os.path.join(os.path.dirname(tokens_path), "en.phone")
            if os.path.isfile(phone_path):
                try:
                    with open(phone_path, 'r', encoding='utf-8') as f:
                        for line in f:
                            parts = line.rstrip('\n').split()
                            if len(parts) >= 2:
                                phone_dict[parts[0].upper()] = parts[1:]
                except Exception:
                    pass

            # Fallback letter-to-phoneme map for Italian/English characters
            _LETTER_PHONE = {
                'a': ['AH0'], 'b': ['B'], 'c': ['K'], 'd': ['D'],
                'e': ['EH1'], 'f': ['F'], 'g': ['G'], 'h': [],
                'i': ['IH0'], 'j': ['JH'], 'k': ['K'], 'l': ['L'],
                'm': ['M'], 'n': ['N'], 'o': ['OW0'], 'p': ['P'],
                'q': ['K', 'W'], 'r': ['R'], 's': ['S'], 't': ['T'],
                'u': ['UH0'], 'v': ['V'], 'w': ['W'], 'x': ['K', 'S'],
                'y': ['IH0'], 'z': ['Z'],
            }

            def word_to_phonemes(w):
                w_up = w.strip().upper()
                if w_up in phone_dict:
                    return phone_dict[w_up]
                base = w_up.split('(')[0]
                if base in phone_dict:
                    return phone_dict[base]
                phones = []
                for ch in w.lower():
                    phones.extend(_LETTER_PHONE.get(ch, []))
                return phones

            def encode_phrase_phoneme(phrase):
                phones = []
                for w in phrase.strip().split():
                    ph = word_to_phonemes(w)
                    valid = [p for p in ph if p in tokens_set]
                    if not valid:
                        valid = [p.rstrip('012') for p in ph if p.rstrip('012') in tokens_set]
                    phones.extend(valid)
                return ' '.join(phones)

            raw_items = [p.strip() for p in keyword.replace(';', ',').split(',') if p.strip()]
            if not raw_items:
                raw_items = [keyword.strip()]

            final_lines = []
            for item in raw_items:
                encoded = encode_phrase_phoneme(item)
                if encoded and encoded not in final_lines:
                    label = '@' + item.upper().replace(' ', '_')
                    final_lines.append(f"{encoded} {label}")

            return '\n'.join(final_lines) if final_lines else keyword

        # BPE subword-vocab path (original logic)
        def encode_word_bpe(w):
            prefix = '\u2581'
            res = []
            idx = 0
            while idx < len(w):
                match = None
                for length in range(len(w) - idx, 0, -1):
                    cand = (prefix if idx == 0 else '') + w[idx:idx+length]
                    if cand in tokens_set:
                        match = cand
                        idx += length
                        break
                if match is None:
                    cand = (prefix if idx == 0 else '') + w[idx]
                    if cand in tokens_set:
                        match = cand
                        idx += 1
                    elif w[idx] in tokens_set:
                        match = w[idx]
                        idx += 1
                    else:
                        idx += 1
                if match:
                    res.append(match)
            return res

        def encode_phrase_bpe(phrase):
            if '\u2581' in phrase:
                return phrase.strip()
            tokens_res = []
            for w in phrase.strip().upper().split():
                tokens_res.extend(encode_word_bpe(w))
            return ' '.join(tokens_res) if tokens_res else phrase

        raw_items = [p.strip() for p in keyword.replace(';', ',').split(',') if p.strip()]
        if not raw_items:
            raw_items = [keyword.strip()]

        final_lines = []
        for item in raw_items:
            encoded = encode_phrase_bpe(item)
            if encoded and encoded not in final_lines:
                final_lines.append(encoded)

            item_lower = item.lower()
            variants = []
            if 'th' in item_lower:
                variants.append(item_lower.replace('th', 't'))
            if 'ph' in item_lower:
                variants.append(item_lower.replace('ph', 'f'))
            if 'hey' in item_lower:
                variants.append(item_lower.replace('hey', 'ehi'))
            elif 'ehi' in item_lower:
                variants.append(item_lower.replace('ehi', 'hey'))
            for v in variants:
                enc_v = encode_phrase_bpe(v)
                if enc_v and enc_v not in final_lines:
                    final_lines.append(enc_v)

        return '\n'.join(final_lines) if final_lines else keyword

    def _get_english_cognate_keywords(self, keyword: str, tokens_path: str) -> list:
        """Return extra keyword lines (phoneme-encoded) for English words that are
        acoustically similar to the given wakeword.

        This is needed when the Sherpa model is trained on English/Chinese and the
        configured wakeword is in another language (e.g. Italian).  The acoustic
        model will produce English phonemes even when it hears Italian speech, so
        we add the English cognate as an additional target.
        """
        if not keyword or not os.path.isfile(tokens_path):
            return []

        # Check if this is a phoneme-vocab model (needed for encoding)
        try:
            with open(tokens_path, 'r', encoding='utf-8') as f:
                sample = [f.readline().strip().split()[0] for _ in range(10) if f]
            is_phoneme = any(
                t[0].isupper() and any(c.isdigit() for c in t) for t in sample if len(t) >= 2
            )
        except Exception:
            return []

        if not is_phoneme:
            return []

        # Load en.phone dictionary
        phone_dict: dict = {}
        phone_path = os.path.join(os.path.dirname(tokens_path), "en.phone")
        if os.path.isfile(phone_path):
            try:
                with open(phone_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        parts = line.rstrip('\n').split()
                        if len(parts) >= 2:
                            phone_dict[parts[0].upper()] = parts[1:]
            except Exception:
                return []

        tokens_set: set = set()
        try:
            with open(tokens_path, 'r', encoding='utf-8') as f:
                for line in f:
                    parts = line.strip().split()
                    if parts:
                        tokens_set.add(parts[0])
        except Exception:
            return []

        def encode_en_word(w: str) -> str:
            w_up = w.strip().upper()
            phones = phone_dict.get(w_up, phone_dict.get(w_up.split('(')[0], []))
            valid = [p for p in phones if p in tokens_set]
            if not valid:
                valid = [p.rstrip('012') for p in phones if p.rstrip('012') in tokens_set]
            return ' '.join(valid)

        # Build a table of Italian→English cognate patterns
        kw = keyword.strip().lower()
        cognates = []

        # Explicit known mappings
        _KNOWN = {
            'assistente': ['assistant', 'assistance'],
            'computer': ['computer'],
            'ehi computer': ['hey computer'],
            'ehi assistente': ['hey assistant'],
            'ciao computer': ['hey computer'],
        }
        if kw in _KNOWN:
            cognates.extend(_KNOWN[kw])

        # Generic suffix rules for Italian cognates
        _SUFFIX_MAP = [
            ('ente', 'ent'), ('ente', 'ant'),
            ('ante', 'ant'), ('zione', 'tion'), ('sione', 'sion'),
            ('tore', 'tor'), ('tura', 'ture'), ('ario', 'ary'),
            ('enza', 'ence'), ('anza', 'ance'),
        ]
        for it_sfx, en_sfx in _SUFFIX_MAP:
            if kw.endswith(it_sfx):
                en_word = kw[:-len(it_sfx)] + en_sfx
                cognates.append(en_word)

        result_lines = []
        seen = set()
        for cognate in cognates:
            words = cognate.strip().split()
            phone_parts = []
            for w in words:
                enc = encode_en_word(w)
                if enc:
                    phone_parts.append(enc)
            if phone_parts:
                line = ' '.join(phone_parts)
                label = '@' + cognate.upper().replace(' ', '_') + '_COGNATE'
                full = f"{line} {label}"
                if full not in seen:
                    seen.add(full)
                    result_lines.append(full)
                    logger.debug(f"Sherpa cognate keyword added: {full}")

        return result_lines

    def _load_sherpa_ww(self):
        try:
            import sherpa_onnx
        except ImportError as e:
            logger.error("sherpa-onnx non installato.")
            self.owner.sherpa_spotter = None
            self.owner.notify_dependency_required("sherpa-onnx", "Motore Wake Word Sherpa-ONNX", False)
            self.fallback_to_vosk_wakeword("Dipendenza sherpa-onnx non installata", exc=e)
            return

        model_dir = (getattr(self.owner, 'sherpa_ww_model_dir', '') or '').strip()
        from core.path_utils import get_models_dir
        models_base = str(get_models_dir(getattr(self.owner, 'models_dir', '')))
        is_default_model = not model_dir
        if is_default_model:
            model_name = getattr(self.owner, 'sherpa_model', '') or "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01"
            # Check both models_base/<name> and models_base/wakeword/<name>
            candidate_main = os.path.join(models_base, model_name)
            candidate_ww = os.path.join(models_base, "wakeword", model_name)
            if os.path.isdir(candidate_ww):
                model_dir = candidate_ww
            else:
                model_dir = candidate_main

        encoder, decoder, joiner, tokens = self._find_sherpa_files(model_dir)

        if not all((encoder, decoder, joiner, tokens)) and is_default_model:
            self._download_sherpa_kws_model(model_dir)
            encoder, decoder, joiner, tokens = self._find_sherpa_files(model_dir)

        missing = []
        if not encoder: missing.append("encoder.onnx")
        if not decoder: missing.append("decoder.onnx")
        if not joiner: missing.append("joiner.onnx")
        if not tokens: missing.append("tokens.txt")

        if missing:
            msg = f"File Sherpa-ONNX mancante: {os.path.join(model_dir, missing[0])}"
            logger.error(msg)
            self.owner.sherpa_spotter = None
            self.fallback_to_vosk_wakeword(msg, exc=FileNotFoundError(msg))
            return

        keyword = getattr(self.owner, 'wakeword', 'assistente')
        encoded_keyword = self._encode_sherpa_keyword(keyword, tokens)

        # For phoneme-vocab models (zh-en, gigaspeech), the model may transcribe
        # a foreign-language wakeword as its English cognate.  Add extra entries
        # for English words that are acoustically similar so detection still works.
        extra_lines = self._get_english_cognate_keywords(keyword, tokens)
        if extra_lines:
            encoded_keyword = encoded_keyword + "\n" + "\n".join(extra_lines)

        keywords_path = os.path.join(model_dir, "_kws_keyword.txt")
        try:
            with open(keywords_path, 'w', encoding='utf-8') as fh:
                fh.write(encoded_keyword + "\n")
            logger.info(f"Sherpa keyword file:\n{encoded_keyword}")
        except Exception as e:
            msg = f"Impossibile scrivere keywords file Sherpa: {e}"
            logger.error(msg)
            self.owner.sherpa_spotter = None
            self.fallback_to_vosk_wakeword(msg, exc=e)
            return

        try:
            spotter = sherpa_onnx.KeywordSpotter(
                tokens=tokens,
                encoder=encoder,
                decoder=decoder,
                joiner=joiner,
                keywords_file=keywords_path,
                num_threads=2,
                keywords_score=1.5,
                keywords_threshold=0.25,
                provider="cpu",
            )
            self.owner.sherpa_spotter = spotter
            self.owner.sherpa_stream = spotter.create_stream()
            logger.info(f"Motore Wake Word (Sherpa-ONNX: {os.path.basename(model_dir)}, keyword: '{keyword}') inizializzato.")
        except Exception as e:
            logger.error(f"Errore inizializzazione Sherpa-ONNX: {e}", exc_info=True)
            self.owner.sherpa_spotter = None
            self.fallback_to_vosk_wakeword(f"Errore inizializzazione Sherpa-ONNX ({e})", exc=e)

    def _download_sherpa_kws_model(self, target_path: str):
        import urllib.request
        import tarfile
        import tempfile

        model_name = os.path.basename(target_path)
        extract_dir = os.path.dirname(target_path) if os.path.dirname(target_path) else target_path

        os.makedirs(extract_dir, exist_ok=True)
        url = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/{model_name}.tar.bz2"
        logger.info(f"Download modello Sherpa-ONNX ({url})...")
        tmp_archive = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".tar.bz2", delete=False) as tmp:
                tmp_archive = tmp.name

            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko)"}
            )
            with urllib.request.urlopen(req) as resp, open(tmp_archive, "wb") as out_f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    out_f.write(chunk)

            logger.info("Estrazione archivio Sherpa-ONNX...")
            with tarfile.open(tmp_archive, "r:bz2") as tar:
                if hasattr(tarfile, 'data_filter'):
                    tar.extractall(path=extract_dir, filter='data')
                else:
                    tar.extractall(path=extract_dir)
            logger.info("Download ed estrazione modello Sherpa-ONNX completati con successo.")
        except Exception as e:
            logger.error(f"Errore download o estrazione Sherpa-ONNX: {e}", exc_info=True)
        finally:
            if tmp_archive and os.path.isfile(tmp_archive):
                try:
                    os.unlink(tmp_archive)
                except OSError:
                    pass

    def _load_oww(self):
        try:
            import openwakeword
            from openwakeword.model import Model
        except ImportError as e:
            logger.error("openwakeword non installato.")
            self.owner.oww_model_instance = None
            self.owner.notify_dependency_required("openwakeword", "Motore Wake Word OpenWakeWord", False)
            self.fallback_to_vosk_wakeword("Dipendenza openwakeword non installata", exc=e)
            return

        try:
            model_name = getattr(self.owner, 'oww_model_name', 'alexa')
            from core.path_utils import get_oww_models_dir, get_wakeword_models_dir, get_models_dir
            oww_dir = str(get_oww_models_dir(getattr(self.owner, 'models_dir', '')))
            ww_dir = str(get_wakeword_models_dir(getattr(self.owner, 'models_dir', '')))
            models_base = str(get_models_dir(getattr(self.owner, 'models_dir', '')))

            pretrained = openwakeword.get_pretrained_model_paths()

            # Assicura copia iniziale nella cartella centralizzata dei modelli
            try:
                import shutil
                os.makedirs(oww_dir, exist_ok=True)
                if pretrained:
                    for src_p in pretrained:
                        if os.path.isfile(src_p):
                            dst_p = os.path.join(oww_dir, os.path.basename(src_p))
                            if not os.path.exists(dst_p):
                                try:
                                    shutil.copy2(src_p, dst_p)
                                except Exception:
                                    pass
                    if os.path.isfile(pretrained[0]):
                        res_dir = os.path.dirname(pretrained[0])
                        for extra_n in ("melspectrogram.onnx", "embedding_model.onnx"):
                            extra_s = os.path.join(res_dir, extra_n)
                            if os.path.isfile(extra_s):
                                extra_d = os.path.join(oww_dir, extra_n)
                                if not os.path.exists(extra_d):
                                    try:
                                        shutil.copy2(extra_s, extra_d)
                                    except Exception:
                                        pass
            except Exception as sync_err:
                logger.debug(f"Errore sincronizzazione modelli OpenWakeWord in {oww_dir}: {sync_err}")

            matched = []
            # 1. Cerca nella directory centralizzata OpenWakeWord
            if os.path.isdir(oww_dir):
                cands = [os.path.join(oww_dir, f) for f in os.listdir(oww_dir) if f.endswith(".onnx")]
                m = [p for p in cands if model_name.lower() in os.path.basename(p).lower()]
                if m:
                    matched = [m[0]]

            # 2. Cerca in wakeword, openwakeword e root modelli
            if not matched:
                for base_cand in (
                    os.path.join(ww_dir, f"{model_name}.onnx"),
                    os.path.join(models_base, "openwakeword", f"{model_name}.onnx"),
                    os.path.join(models_base, f"{model_name}.onnx"),
                ):
                    if os.path.isfile(base_cand):
                        matched = [base_cand]
                        break

            # 3. Fallback a pretrained fornito dal pacchetto python
            if not matched:
                try:
                    pretrained = openwakeword.get_pretrained_model_paths()
                    m = [p for p in pretrained if model_name.lower() in os.path.basename(p).lower()]
                    if m:
                        matched = [m[0]]
                except Exception:
                    pass

            if not matched:
                raise FileNotFoundError(f"Modello OpenWakeWord '{model_name}' non trovato tra i modelli pre-addestrati o locali.")

            try:
                self.owner.oww_model_instance = Model(
                    wakeword_model_paths=matched,
                    inference_framework="onnx",
                )
            except TypeError as te:
                if "inference_framework" in str(te):
                    # openWakeWord <= 0.4.0 non accetta inference_framework (utilizza nativamente ed esclusivamente onnxruntime)
                    self.owner.oww_model_instance = Model(
                        wakeword_model_paths=matched,
                    )
                elif "wakeword_model_paths" in str(te):
                    self.owner.oww_model_instance = Model(
                        wakeword_models=matched,
                        inference_framework="onnx",
                    )
                else:
                    raise
            self.owner._oww_buffer = []
            logger.info(f"Motore Wake Word (OpenWakeWord: {model_name}) inizializzato con successo.")
        except Exception as e:
            logger.error(f"Errore inizializzazione OpenWakeWord: {e}", exc_info=True)
            self.owner.oww_model_instance = None
            self.owner._oww_buffer = []
            self.fallback_to_vosk_wakeword(f"Errore inizializzazione OpenWakeWord ({e})", exc=e)

    def initialize_services(self):
        self.owner.audio_player = AudioPlayer(on_playback_finished=self.owner._on_playback_finished)
        self.owner.audio_player.start()

        model_manager = getattr(self.owner, "model_manager", None)
        models_dir = getattr(self.owner, 'models_dir', '')
        from core.path_utils import get_tts_models_dir
        tts_models_dir = str(get_tts_models_dir(models_dir))
        tts_kwargs = {
            "audio_player": self.owner.audio_player,
            "settings_observer": self.owner,
            "models_dir": tts_models_dir,
        }
        if model_manager:
            tts_kwargs["model_manager"] = model_manager
        self.owner.tts_manager = TTSServiceManager(**tts_kwargs)

        try:
            from mcp.manager import MCPManager
            self.owner.mcp_manager = MCPManager(
                registry_url=self.owner.settings.get_string("mcp-registry-url"),
            )
            self.owner.mcp_manager.enabled = self.owner.settings.get_boolean("mcp-enabled")
            from core.async_bridge import run_async
            run_async(self.owner.mcp_manager.initialize())
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
            fast_path_enabled=get_boolean_setting(self.owner.settings, "fast-path-enabled", False),
            medium_path_enabled=get_boolean_setting(self.owner.settings, "medium-path-enabled", True),
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
        """Verifica tutte le dipendenze Python opzionali in un unico passaggio all'avvio."""
        import importlib
        for import_name, package_name, description, is_critical in _OPTIONAL_DEPS:
            try:
                importlib.import_module(import_name)
            except ImportError:
                logger.warning(f"Dipendenza opzionale mancante: {package_name} ({import_name})")
                self.owner.notify_dependency_required(
                    package=package_name,
                    description=description,
                    is_critical=is_critical,
                    dep_type="pip",
                )

    def _probe_system_deps(self):
        """Verifica le dipendenze di sistema (pacchetti OS) e notifica quelle mancanti."""
        for dep in _SYSTEM_DEPS:
            try:
                if not dep["check"]():
                    logger.warning(f"Dipendenza di sistema mancante: {dep['package']}")
                    self.owner.notify_dependency_required(
                        package=dep["package"],
                        description=dep["description"],
                        is_critical=dep["is_critical"],
                        dep_type="system",
                        system_packages=dep.get("packages", {}),
                    )
            except Exception as e:
                logger.warning(f"Errore durante verifica dipendenza di sistema {dep['package']}: {e}")

    def _probe_mcp_deps(self):
        """Verifica le dipendenze degli MCP server abilitati (es. gnome-mcp-server e il rispettivo runner cargo)."""
        settings = getattr(self.owner, 'settings', None)
        if settings:
            try:
                if not settings.get_boolean("mcp-enabled"):
                    return
            except Exception:
                pass

        mcp_manager = getattr(self.owner, 'mcp_manager', None)
        if mcp_manager and hasattr(mcp_manager, "config_loader"):
            config_loader = mcp_manager.config_loader
        else:
            from mcp.config import MCPConfigLoader
            config_loader = MCPConfigLoader()

        try:
            config_data = config_loader.load()
            servers = config_data.get("mcpServers", {})
        except Exception as e:
            logger.warning(f"Errore lettura configurazione mcpServers in _probe_mcp_deps: {e}")
            servers = {}

        if "gnome-mcp-server" not in servers:
            servers["gnome-mcp-server"] = {
                "command": "gnome-mcp-server",
                "enabled": True,
                "description": "GNOME desktop MCP server by Bilal Elmoussaoui (audio, apps, quick settings, notifications, windows)",
            }

        for name, cfg in servers.items():
            if not cfg.get("enabled", True):
                continue
            cmd = cfg.get("command", "")
            if cmd == "builtin":
                continue

            installed = False
            if mcp_manager and hasattr(mcp_manager, "is_server_installed"):
                installed = mcp_manager.is_server_installed(name)
            else:
                cargo_candidate = os.path.expanduser("~/.cargo/bin/gnome-mcp-server")
                installed = bool(shutil.which(cmd) or (cmd == "gnome-mcp-server" and os.path.isfile(cargo_candidate)))

            if not installed:
                # Verifica se il server ha un compilatore/runner prerequisito configurato (es. cargo)
                prereq_pkg = cfg.get("prerequisite_package")
                if not prereq_pkg and (name == "gnome-mcp-server" or cmd == "gnome-mcp-server"):
                    prereq_pkg = "cargo"

                if prereq_pkg:
                    cargo_bin = os.path.expanduser("~/.cargo/bin")
                    search_path = f"{cargo_bin}:{os.environ.get('PATH', '')}"
                    prereq_available = bool(shutil.which(prereq_pkg, path=search_path))
                    if not prereq_available:
                        logger.warning(f"Dipendenza prerequisita mancante per {name}: {prereq_pkg}")
                        all_sys_deps = load_json_data("dependencies/system_deps.json", fallback_default=[])
                        dep_meta = next((d for d in all_sys_deps if d.get("package") == prereq_pkg or d.get("id") == prereq_pkg), {})
                        sys_pkgs = dep_meta.get("packages") or {
                            "dnf": prereq_pkg,
                            "dnf5": prereq_pkg,
                            "apt": prereq_pkg,
                            "pacman": "rust" if prereq_pkg == "cargo" else prereq_pkg,
                            "zypper": prereq_pkg,
                        }
                        desc = dep_meta.get("description", f"Compilatore/runner richiesto per {name}")
                        self.owner.notify_dependency_required(
                            package=prereq_pkg,
                            description=desc,
                            is_critical=False,
                            dep_type="system",
                            system_packages=sys_pkgs,
                            mcp_server=name,
                        )

                logger.warning(f"Server MCP abilitato ma non installato: {name}")
                self.owner.notify_dependency_required(
                    package=name,
                    description=cfg.get("description", f"Server MCP {name}"),
                    is_critical=False,
                    dep_type="mcp",
                    mcp_server=name,
                )

    def refresh_missing_deps(self):
        """Riesegue il controllo completo delle dipendenze aggiornando la lista in _missing_deps."""
        import importlib
        try:
            importlib.invalidate_caches()
        except Exception:
            pass

        self.owner._missing_deps = []
        self._probe_optional_deps()
        self._probe_system_deps()
        self._probe_mcp_deps()

    def _close_deps_notification(self, notif_id: int):
        """Chiude la notifica delle dipendenze mancanti se ancora aperta."""
        if hasattr(self.owner, '_deps_notif') and self.owner._deps_notif is not None:
            try:
                self.owner._deps_notif.close()
            except Exception:
                pass
            self.owner._deps_notif = None
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.call_sync(
                "org.freedesktop.Notifications",
                "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications",
                "CloseNotification",
                GLib.Variant("(u)", (notif_id,)),
                None,
                Gio.DBusCallFlags.NONE,
                1000,
                None,
            )
        except Exception:
            pass

    def _notify_missing_deps_summary(self):
        """Emette una singola notifica desktop interattiva se ci sono dipendenze mancanti."""
        deps = getattr(self.owner, '_missing_deps', [])
        last_id = getattr(self.owner, '_last_deps_notif_id', 0)
        if not deps:
            if last_id > 0:
                self._close_deps_notification(last_id)
                self.owner._last_deps_notif_id = 0
                _write_persisted_deps_notif_id(0)
            return

        names = ", ".join(d["package"] for d in deps)

        def _on_notif_action(n, action, user_data=None):
            self._handle_deps_notification_action(action)

        # 1. Tentativo con notify2 (mantiene compatibilità con test unitari esistenti)
        try:
            notif = notify2.Notification(
                "Dipendenze mancanti",
                f"Pacchetti non disponibili: {names}.\nApri l'assistente vocale per installarli.",
                "dialog-warning",
            )
            try:
                notif.set_hint_string("desktop-entry", "org.local.VoiceAssistant")
            except Exception:
                pass
            if last_id > 0:
                notif.id = last_id
            notif.add_action("default", "Apri", _on_notif_action)
            notif.add_action("install", "Installa", _on_notif_action)
            self.owner._deps_notif = notif
            notif.show()
            if hasattr(notif, 'id') and notif.id:
                new_id = int(notif.id)
                self.owner._last_deps_notif_id = new_id
                _write_persisted_deps_notif_id(new_id)
            return
        except Exception as e:
            logger.warning(f"Invio notifica via notify2 non riuscito ({e}), fallback su GDBus diretto.")

        # 2. Fallback diretto GDBus (nativo GNOME / Freedesktop Notifications)
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            params = GLib.Variant(
                "(susssasa{sv}i)",
                (
                    "Voice Assistant",
                    last_id,
                    "dialog-warning",
                    "Dipendenze mancanti",
                    f"Pacchetti non disponibili: {names}.\nApri l'assistente vocale per installarli.",
                    ["default", "Apri", "install", "Installa"],
                    {"desktop-entry": GLib.Variant("s", "org.local.VoiceAssistant")},
                    -1,
                ),
            )
            res = bus.call_sync(
                "org.freedesktop.Notifications",
                "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications",
                "Notify",
                params,
                GLib.VariantType("(u)"),
                Gio.DBusCallFlags.NONE,
                3000,
                None,
            )
            new_id = res.unpack()[0]
            self.owner._last_deps_notif_id = new_id
            _write_persisted_deps_notif_id(new_id)
            logger.info(f"Notifica inviata direttamente via GDBus con ID {self.owner._last_deps_notif_id}")
        except Exception as e2:
            logger.warning(f"Impossibile mostrare notifica dipendenze mancanti: {e2}")

    def bootstrap(self):
        self.initialize_notifications()
        self.register_gresource()
        self.load_settings()
        self.initialize_wakeword()
        self.initialize_services()
        self.initialize_pipeline()
        self.refresh_missing_deps()
        self.start_background_load()
        self._notify_missing_deps_summary()
