"""Interaction runtime helpers for wakeword, audio loop and text processing."""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
import threading
import time

try:
    from gi.repository import GLib
except Exception:  # pragma: no cover - optional in minimal test envs
    class _DummyGLib:
        @staticmethod
        def idle_add(*args, **kwargs):
            return None

        @staticmethod
        def timeout_add(*args, **kwargs):
            return None

    GLib = _DummyGLib()

from core.async_bridge import run_async
from core.audio_runtime import AUDIO_FILTER_KEYS
from core.daemon_protocol import DaemonOwner
from skills.skill_registry import SkillRegistry
from skills.skill_executor import SkillExecutor
from skills.app_slot_matcher import AppSlotMatcher

logger = logging.getLogger("VoiceAssistant.AssistantRuntime")

import re as _re

# Mapping intent → (tool_name, static_args)
def _load_intent_tool_map() -> dict:
    """Carica la mappatura intent -> (tool, args) da data/mcp/intent_tools.json."""
    try:
        from core.data_loader import load_json_data
        raw = load_json_data("mcp/intent_tools.json", fallback_default={}) or {}
        res = {}
        for intent, val in raw.items():
            if isinstance(val, dict):
                res[intent] = (val.get("tool", ""), val.get("args", {}))
            elif isinstance(val, (list, tuple)) and len(val) >= 2:
                res[intent] = (val[0], val[1])
        if res:
            return res
    except Exception as e:
        logger.debug(f"Errore caricamento intent_tools.json: {e}")

    return {
        "volume_up":       ("set_volume",        {"direction": "up"}),
        "volume_down":     ("set_volume",        {"direction": "down"}),
        "mute":            ("set_volume",        {"mute": True}),
        "unmute":          ("set_volume",        {"mute": False}),
        "set_theme_dark":  ("quick_settings",   {"setting": "dark_style", "enabled": True}),
        "set_theme_light": ("quick_settings",   {"setting": "dark_style", "enabled": False}),
        "theme_dark":      ("quick_settings",   {"setting": "dark_style", "enabled": True}),
        "theme_light":     ("quick_settings",   {"setting": "dark_style", "enabled": False}),
        "media_play":      ("media_control",     {"action": "play"}),
        "media_pause":     ("media_control",     {"action": "pause"}),
        "media_next":      ("media_control",     {"action": "next"}),
        "media_prev":      ("media_control",     {"action": "previous"}),
        "wifi_on":         ("quick_settings",    {"setting": "wifi", "enabled": True}),
        "wifi_off":        ("quick_settings",    {"setting": "wifi", "enabled": False}),
        "bluetooth_on":    ("quick_settings",    {"setting": "bluetooth", "enabled": True}),
        "bluetooth_off":   ("quick_settings",    {"setting": "bluetooth", "enabled": False}),
        "night_light_on":  ("quick_settings",    {"setting": "night_light", "enabled": True}),
        "night_light_off": ("quick_settings",    {"setting": "night_light", "enabled": False}),
        "dnd_on":          ("quick_settings",    {"setting": "do_not_disturb", "enabled": True}),
        "dnd_off":         ("quick_settings",    {"setting": "do_not_disturb", "enabled": False}),
    }

_INTENT_TOOL_MAP: dict = _load_intent_tool_map()


class AssistantRuntimeController:
    """Encapsulates wakeword detection, TTS interaction, and audio processing flow."""

    def __init__(self, owner: DaemonOwner):
        self.owner = owner
        self.skill_registry = SkillRegistry.from_default_directory()
        self.app_matcher = AppSlotMatcher()

    def _execute_skill(
        self, skill_name: str, user_text: str, use_llm_fallback: bool = True
    ):
        """Execute a markdown skill with tool mapping and optional LLM fallback."""
        skill = self.skill_registry.find_by_intent(skill_name)
        if not skill:
            return (False, f"Skill {skill_name} non trovata.")

        executor = SkillExecutor(skill)
        llm_fallback = None
        if use_llm_fallback and hasattr(self.owner, "llm_service"):
            try:
                llm_fallback = lambda prompt: self.owner.llm_service.get_response(prompt)
            except Exception:
                pass

        success, response, result = executor.execute(
            user_text, mcp_manager=self.owner.mcp_manager, llm_fallback=llm_fallback
        )
        return (success, response)

    def _launch_desktop_app_native(self, app_name: str) -> bool:
        """Fallback nativo per l'avvio delle applicazioni desktop su GNOME/Linux."""
        if not app_name or not app_name.strip():
            return False
        clean = app_name.lower().strip()
        try:
            from gi.repository import Gio
            app_map = {
                "calendario": ["org.gnome.Calendar.desktop", "gnome-calendar.desktop", "gnome-calendar"],
                "calendar": ["org.gnome.Calendar.desktop", "gnome-calendar.desktop", "gnome-calendar"],
                "calcolatrice": ["org.gnome.Calculator.desktop", "gnome-calculator.desktop", "gnome-calculator"],
                "calculator": ["org.gnome.Calculator.desktop", "gnome-calculator.desktop", "gnome-calculator"],
                "terminale": ["org.gnome.Terminal.desktop", "gnome-terminal.desktop", "ptyxis.desktop", "org.gnome.Ptyxis.desktop", "xterm"],
                "terminal": ["org.gnome.Terminal.desktop", "gnome-terminal.desktop", "ptyxis.desktop", "org.gnome.Ptyxis.desktop", "xterm"],
                "impostazioni": ["org.gnome.Settings.desktop", "gnome-control-center.desktop", "gnome-control-center"],
                "settings": ["org.gnome.Settings.desktop", "gnome-control-center.desktop", "gnome-control-center"],
                "orologio": ["org.gnome.clocks.desktop", "org.gnome.Clocks.desktop", "gnome-clocks"],
                "clocks": ["org.gnome.clocks.desktop", "org.gnome.Clocks.desktop", "gnome-clocks"],
                "file": ["org.gnome.Nautilus.desktop", "nautilus.desktop", "nautilus"],
                "nautilus": ["org.gnome.Nautilus.desktop", "nautilus.desktop", "nautilus"],
                "browser": ["firefox.desktop", "org.mozilla.firefox.desktop", "google-chrome.desktop", "chromium.desktop", "firefox"],
                "firefox": ["firefox.desktop", "org.mozilla.firefox.desktop", "firefox"],
            }
            candidates = app_map.get(clean, [f"{clean}.desktop", clean])
            for cand in candidates:
                if cand.endswith(".desktop"):
                    app_info = Gio.DesktopAppInfo.new(cand)
                    if app_info:
                        return app_info.launch([], None)
                else:
                    import shutil, subprocess
                    bin_path = shutil.which(cand)
                    if bin_path:
                        subprocess.Popen([bin_path], start_new_session=True)
                        return True

            all_apps = Gio.AppInfo.get_all()
            for info in all_apps:
                name = (info.get_name() or "").lower()
                disp = (info.get_display_name() or "").lower()
                if clean in name or clean in disp:
                    return info.launch([], None)
        except Exception as e:
            logger.debug(f"Native app launch fallback error for '{app_name}': {e}")
        return False

    def _handle_fast_path_intent(self, intent_name: str, params, text: str = ""):
        if not self.owner.mcp_manager:
            if intent_name == "launch_app":
                app = params.get("app") or params.get("app_name") or ""
                if self._launch_desktop_app_native(app):
                    target_name = app if app else "l'applicazione"
                    return (True, f"Apro {target_name}.")
            return (False, "")

        def run_tool(tool_name, args):
            result = self.owner.mcp_manager.execute_tool(tool_name, args)
            if asyncio.iscoroutine(result):
                result = run_async(result)
            return result

        try:
            # --- Intent con parametri dinamici ---
            if intent_name == "set_volume":
                vol = max(0, min(100, int(params.get("volume", 50))))
                return (True, run_tool("set_volume", {"volume": float(vol)}))

            if intent_name == "launch_app":
                app = params.get("app") or params.get("app_name") or ""
                if not app and text:
                    m = _re.search(
                        r'(?:apri|avvia|lancia|open)\s+(?:il\s+|la\s+|le\s+|l\'|i\s+)?([\w\s]+)',
                        text.lower()
                    )
                    if m:
                        app = m.group(1).strip()
                resolved = self.app_matcher.match(app) if app else None
                if resolved:
                    res = run_tool("launch_application", {"app_name": resolved["desktop_id"]})
                    if isinstance(res, str) and ("Successfully launched" in res or '"success":true' in res or '"success": true' in res):
                        return (True, f"Apro {resolved['name']}.")
                    if self._launch_desktop_app_native(app):
                        return (True, f"Apro {resolved['name']}.")
                    return (True, res)

                res = run_tool("launch_application", {"app_name": app or "firefox"})
                if isinstance(res, str) and ("Successfully launched" in res or '"success":true' in res or '"success": true' in res):
                    target_name = app if app else "l'applicazione"
                    return (True, f"Apro {target_name}.")
                if self._launch_desktop_app_native(app):
                    target_name = app if app else "l'applicazione"
                    return (True, f"Apro {target_name}.")
                return (True, res)

            if intent_name == "system_control":
                if text:
                    success, response = self._execute_skill("system_control", text)
                    if success:
                        return (success, response)
                action = str(params.get("action", params.get("mode", "") or "")).lower()
                if action in {"volume_up", "increase", "up"}:
                    return (True, run_tool("set_volume", {"direction": "up"}))
                if action in {"volume_down", "decrease", "down"}:
                    return (True, run_tool("set_volume", {"direction": "down"}))
                if action in {"mute", "silence", "silent"}:
                    return (True, run_tool("set_volume", {"mute": True}))
                if action in {"theme_dark", "dark", "set_theme_dark"}:
                    return (True, run_tool("quick_settings", {"setting": "dark_style", "enabled": True}))
                if action in {"theme_light", "light", "set_theme_light"}:
                    return (True, run_tool("quick_settings", {"setting": "dark_style", "enabled": False}))
                app_name = params.get("app") or params.get("app_name") or "firefox"
                if action in {"launch_app", "app", "open_app"}:
                    res = run_tool("launch_application", {"app_name": app_name})
                    if isinstance(res, str) and ("Successfully launched" in res or '"success":true' in res or '"success": true' in res):
                        return (True, f"Apro {app_name}.")
                    if self._launch_desktop_app_native(app_name):
                        return (True, f"Apro {app_name}.")
                    return (True, res)
                return (False, "")

            # --- Tema (gestisce anche i parametri bool legacy) ---
            if intent_name in ("set_theme_dark", "set_theme_light", "theme_control"):
                dark = params.get("dark")
                mode = params.get("mode")
                if dark is None and mode is None and intent_name == "theme_control":
                    return (False, "")
                if dark is not None:
                    mode = "dark" if dark else "light"
                mode = str(mode or "dark").lower()
                is_dark = mode in {"dark", "night", "black", "scuro", "set_theme_dark", "theme_dark"}
                return (True, run_tool("quick_settings", {"setting": "dark_style", "enabled": is_dark}))

            # --- Tabella statica ---
            mapping = _INTENT_TOOL_MAP.get(intent_name)
            if mapping:
                tool_name, static_args = mapping
                return (True, run_tool(tool_name, dict(static_args or {})))

        except Exception as e:
            logger.error(f"Errore Fast-Path intent '{intent_name}': {e}")

        return (False, "")

    def get(self, key: str, default=None):
        try:
            val = self.owner.settings.get_value(key)
            return val.unpack() if val is not None else default
        except Exception as e:
            logger.debug(f"GSettings key '{key}' not found, using default: {e}")
            return default

    def _schedule_reload(self):
        if getattr(self.owner, '_reload_timer', None):
            self.owner._reload_timer.cancel()

        self.owner._load_id = getattr(self.owner, '_load_id', 0) + 1
        current_id = self.owner._load_id
        self.owner._reload_timer = threading.Timer(0.5, lambda: threading.Thread(target=self.owner.load_provider, args=(current_id,), daemon=True).start())
        self.owner._reload_timer.start()

    def on_settings_changed(self, settings, key):
        if key == "wakeword":
            self.owner.wakeword = settings.get_string(key)
            self.reset_wakeword_recognizer()
            if getattr(self.owner, 'wakeword_engine', 'vosk') == 'sherpa-onnx':
                self.owner.sherpa_spotter = None
                self.owner.sherpa_stream = None
                self.owner.runtime_manager.initialize_wakeword()
            while not self.owner.q.empty():
                try:
                    self.owner.q.get_nowait()
                except Exception:
                    break
            self.owner._listening_start_time = None
            self.owner._last_speech_time = None
            if hasattr(self.owner, 'provider') and self.owner.provider:
                self.owner.provider.reset()
            if self.owner._state in ("listening", "speaking", "processing"):
                GLib.idle_add(self.owner.set_state, "idle")
            logger.info(f"Wakeword aggiornata a: '{self.owner.wakeword}' - ripristinato stato idle.")
        elif key == "stt-provider":
            new_val = settings.get_string(key)
            if new_val != getattr(self.owner, 'provider_name', ''):
                self.owner.provider_name = new_val
                self._schedule_reload()
        elif key == "stt-model":
            new_val = settings.get_string(key)
            if new_val != getattr(self.owner, 'model_name', ''):
                self.owner.model_name = new_val
                self._schedule_reload()
        elif key == "stt-hardware":
            new_val = settings.get_string(key)
            if new_val != getattr(self.owner, 'hardware', ''):
                self.owner.hardware = new_val
                self._schedule_reload()
        elif key == "models-dir":
            new_val = settings.get_string(key)
            if new_val != getattr(self.owner, 'models_dir', ''):
                self.owner.models_dir = new_val
                self._schedule_reload()
        elif key == "stt-extra":
            try:
                new_extra = json.loads(settings.get_string(key))
            except json.JSONDecodeError:
                new_extra = {}
            if new_extra != getattr(self.owner, 'extra_config', {}):
                self.owner.extra_config = new_extra
                self._schedule_reload()
        elif key == "enabled":
            new_enabled = settings.get_boolean(key)
            if new_enabled and self.owner._state == "disabled":
                GLib.idle_add(self.owner.set_state, "idle")
            elif not new_enabled and self.owner._state != "disabled":
                GLib.idle_add(self.owner.set_state, "disabled")
        elif key == "wakeword-engine":
            new_engine = settings.get_string(key) or "vosk"
            if new_engine != getattr(self.owner, 'wakeword_engine', 'vosk'):
                self.owner.wakeword_engine = new_engine
                self.owner.ww_recognizer = None
                self.owner.oww_model_instance = None
                self.owner._oww_buffer = []
                self.owner.sherpa_spotter = None
                self.owner.sherpa_stream = None
                self.owner.runtime_manager.initialize_wakeword()
                logger.info(f"Motore wakeword cambiato a: {new_engine}")
        elif key == "oww-model":
            new_model = settings.get_string(key) or "alexa"
            if new_model != getattr(self.owner, 'oww_model_name', 'alexa'):
                self.owner.oww_model_name = new_model
                if getattr(self.owner, 'wakeword_engine', 'vosk') == 'openwakeword':
                    self.owner.oww_model_instance = None
                    self.owner._oww_buffer = []
                    self.owner.runtime_manager.initialize_wakeword()
                    logger.info(f"Modello OpenWakeWord cambiato a: {new_model}")
        elif key == "vosk-ww-model":
            new_model = settings.get_string(key)
            if new_model and new_model != getattr(self.owner, 'vosk_ww_model', ''):
                self.owner.vosk_ww_model = new_model
                if getattr(self.owner, 'wakeword_engine', 'vosk') == 'vosk':
                    self.owner.ww_recognizer = None
                    self.owner.ww_model = None
                    self.owner.runtime_manager.initialize_wakeword()
                logger.info(f"Modello Vosk Wakeword cambiato a: {new_model}")
        elif key == "sherpa-model":
            new_model = settings.get_string(key)
            if new_model and new_model != getattr(self.owner, 'sherpa_model', ''):
                self.owner.sherpa_model = new_model
                if getattr(self.owner, 'wakeword_engine', 'vosk') == 'sherpa-onnx':
                    self.owner.sherpa_spotter = None
                    self.owner.sherpa_stream = None
                    self.owner.runtime_manager.initialize_wakeword()
                logger.info(f"Modello Sherpa-ONNX cambiato a: {new_model}")
        elif key == "sherpa-ww-model-dir":
            new_dir = settings.get_string(key) or ""
            if new_dir != getattr(self.owner, 'sherpa_ww_model_dir', ''):
                self.owner.sherpa_ww_model_dir = new_dir
                if getattr(self.owner, 'wakeword_engine', 'vosk') == 'sherpa-onnx':
                    self.owner.sherpa_spotter = None
                    self.owner.sherpa_stream = None
                    self.owner.runtime_manager.initialize_wakeword()
                logger.info(f"Directory modello Sherpa-ONNX cambiata: {new_dir}")
        elif key == "language":
            raw_lang = settings.get_string(key)
            from core.locale_utils import get_system_language
            new_lang = raw_lang.strip() if raw_lang and raw_lang.strip() else get_system_language()
            if new_lang != getattr(self.owner, 'language', ''):
                self.owner.language = new_lang
                from providers import get_default_model
                self.owner.vosk_ww_model = get_default_model("vosk", self.owner.language)
                if getattr(self.owner, 'wakeword_engine', 'vosk') == 'vosk':
                    self.owner.ww_recognizer = None
                    self.owner.ww_model = None
                    self.owner.runtime_manager.initialize_wakeword()
                self._schedule_reload()
                logger.info(f"Lingua assistente cambiata a: {new_lang}")
        elif key in ("fast-path-enabled", "medium-path-enabled"):
            enabled = settings.get_boolean(key)
            pipeline = self.owner.pipeline_controller
            if pipeline:
                if key == "fast-path-enabled":
                    pipeline.fast_path_enabled = enabled
                else:
                    pipeline.medium_path_enabled = enabled
            label = "Fast-Path" if key == "fast-path-enabled" else "Medium-Path"
            logger.info(f"{label} {'abilitato' if enabled else 'disabilitato'}.")
        elif key == "audio-aec-enabled":
            enabled = settings.get_boolean(key)
            logger.info(
                f"Cancellazione eco (AEC) {'abilitata' if enabled else 'disabilitata'}: "
                "verrà applicata alla prossima apertura dello stream audio."
            )
        elif key in AUDIO_FILTER_KEYS:
            from core.audio_runtime import apply_filter_settings
            apply_filter_settings(getattr(self.owner, 'audio_filter', None), settings)
        elif key == "mcp-registry-url" and getattr(self.owner, "mcp_manager", None):
            self.owner.mcp_manager.set_registry_url(settings.get_string(key))
        elif key == "mcp-enabled" and getattr(self.owner, "mcp_manager", None):
            self.owner.mcp_manager.enabled = settings.get_boolean(key)
        elif key == "semantic-router-confidence-threshold" and getattr(self.owner, "pipeline_controller", None):
            self.owner.pipeline_controller.fast_path.semantic_min_score = settings.get_double(key)
        elif key in {"idle-unload-timeout", "stt-idle-unload-timeout", "llm-idle-unload-timeout", "tts-idle-unload-timeout"}:
            self.owner.model_manager.idle_timeout_sec = settings.get_int("idle-unload-timeout")
            self.owner.model_manager.set_idle_timeouts({
                "stt": settings.get_int("stt-idle-unload-timeout"),
                "llm": settings.get_int("llm-idle-unload-timeout"),
                "tts": settings.get_int("tts-idle-unload-timeout"),
            })
        elif key in ("tts-provider", "tts-engine"):
            new_prov = settings.get_string(key)
            logger.info(f"Motore/provider TTS aggiornato a: '{new_prov}'")
        elif key == "tts-voice":
            new_voice = settings.get_string(key)
            tts_mgr = getattr(self.owner, "tts_manager", None)
            if tts_mgr and hasattr(tts_mgr, "providers") and "piper" in tts_mgr.providers:
                piper_p = tts_mgr.providers["piper"]
                if hasattr(piper_p, "unload_voice"):
                    piper_p.unload_voice()
            logger.info(f"Voce TTS aggiornata a: '{new_voice}'")
        elif key in ("llm-mode", "llm-model"):
            new_val = settings.get_string(key)
            llm_svc = getattr(self.owner, "llm_service", None)
            if llm_svc and hasattr(llm_svc, "local_gguf_provider"):
                llm_svc.local_gguf_provider.unload_model()
            logger.info(f"Impostazione LLM '{key}' aggiornata a: '{new_val}'")

    def reset_wakeword_recognizer(self):
        engine = getattr(self.owner, 'wakeword_engine', 'vosk')
        if engine == 'openwakeword':
            self.owner._oww_buffer = []
            oww = getattr(self.owner, 'oww_model_instance', None)
            if oww is not None:
                try:
                    if hasattr(oww, 'reset'):
                        oww.reset()
                    if hasattr(oww, 'preprocessor') and oww.preprocessor:
                        import numpy as np
                        prep = oww.preprocessor
                        if hasattr(prep, 'raw_data_buffer'):
                            prep.raw_data_buffer.clear()
                        prep.accumulated_samples = 0
                        prep.melspectrogram_buffer = np.ones((76, 32))
                        prep.feature_buffer = np.zeros((116, 96), dtype=np.float32)
                except Exception as e:
                    logger.warning(f"Errore reset OpenWakeWord: {e}")
            return

        if engine == 'sherpa-onnx':
            spotter = getattr(self.owner, 'sherpa_spotter', None)
            stream = getattr(self.owner, 'sherpa_stream', None)
            if spotter is not None and stream is not None:
                try:
                    if hasattr(spotter, 'reset_stream'):
                        spotter.reset_stream(stream)
                    elif hasattr(stream, 'reset'):
                        stream.reset()
                except Exception as e:
                    logger.warning(f"Errore reset stream Sherpa-ONNX: {e}")
            return

        # Vosk
        ww_model = getattr(self.owner, 'ww_model', None)
        if not ww_model:
            return

        if isinstance(ww_model, str):
            logger.warning("Modello wakeword non valido per Vosk: reset del recognizer saltato.")
            self.owner.ww_recognizer = None
            return

        try:
            from vosk import KaldiRecognizer
        except ImportError:
            logger.warning("Vosk non disponibile: reset wakeword ignorato.")
            self.owner.ww_recognizer = None
            return

        try:
            import json as _json
            wakeword = getattr(self.owner, 'wakeword', 'assistente').lower().strip()
            ww_no_h = wakeword.replace('h', '')
            ww_variants = list({wakeword, ww_no_h})
            if wakeword == "assistente":
                ww_variants += ["assistenti", "assistenza", "assiste"]
            elif "anthon" in wakeword or "anton" in wakeword:
                ww_variants += ["anthon", "anton", "antonio", "anthony"]
            ww_variants += ["stop", "basta", "zitto", "fermati", "silenzio", "interrompi", "cancella"]
            grammar = _json.dumps(list(set(ww_variants)) + ["[unk]"])
            self.owner.ww_recognizer = KaldiRecognizer(ww_model, 16000, grammar)
        except Exception as e:
            logger.warning(f"Errore reset ww_recognizer: {e}")
            self.owner.ww_recognizer = None

    def _check_sherpa_wakeword(self, data: bytes):
        """Invia chunk PCM a Sherpa-ONNX KeywordSpotter in modalità streaming."""
        import numpy as np
        spotter = getattr(self.owner, 'sherpa_spotter', None)
        stream = getattr(self.owner, 'sherpa_stream', None)
        if spotter is None or stream is None:
            return
        if not data:
            return

        samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        try:
            stream.accept_waveform(sample_rate=16000, waveform=samples)
            while spotter.is_ready(stream):
                if hasattr(spotter, 'decode_stream'):
                    spotter.decode_stream(stream)
                elif hasattr(spotter, 'decode'):
                    spotter.decode(stream)

                result = spotter.get_result(stream)
                keyword = getattr(result, 'keyword', result) if not isinstance(result, str) else result
                if keyword and str(keyword).strip():
                    logger.info(f"Sherpa-ONNX: keyword '{str(keyword).strip()}' rilevata!")
                    if hasattr(spotter, 'reset_stream'):
                        spotter.reset_stream(stream)
                    elif hasattr(stream, 'reset'):
                        stream.reset()
                    self.trigger_assistant()
                    return
        except Exception as e:
            logger.warning(f"Errore Sherpa-ONNX detect: {e}")

    def _check_oww_wakeword(self, data: bytes):
        """Accumula chunk PCM e li invia a OpenWakeWord ogni 1280 campioni (80ms)."""
        import numpy as np
        oww = getattr(self.owner, 'oww_model_instance', None)
        if oww is None:
            return

        buf: list = self.owner._oww_buffer
        if data:
            chunk = np.frombuffer(data, dtype=np.int16)
            buf.extend(chunk.tolist())

        OWW_FRAME = 1280
        while len(buf) >= OWW_FRAME:
            frame = np.array(buf[:OWW_FRAME], dtype=np.int16)
            del buf[:OWW_FRAME]
            try:
                prediction = oww.predict(frame)
                model_name = getattr(self.owner, 'oww_model_name', 'alexa')
                score = 0.0
                if model_name in prediction:
                    score = prediction[model_name]
                else:
                    matched_scores = [v for k, v in prediction.items() if model_name.lower() in k.lower()]
                    if matched_scores:
                        score = max(matched_scores)
                    elif len(prediction) == 1:
                        score = list(prediction.values())[0]

                if score > 0.5:
                    logger.info(f"OpenWakeWord: '{model_name}' rilevata (score={score:.2f})")
                    self.reset_wakeword_recognizer()
                    self.trigger_assistant()
                    return
            except Exception as e:
                logger.warning(f"Errore OWW predict: {e}")

        self.owner._oww_buffer = buf

    def trigger_assistant(self):
        import time
        if hasattr(self.owner, 'pipeline_controller') and self.owner.pipeline_controller:
            self.owner.pipeline_controller.cancel_pipeline(target_state=None)
        elif hasattr(self.owner, 'audio_player') and self.owner.audio_player:
            self.owner.audio_player.stop_playback()

        while not self.owner.q.empty():
            try:
                self.owner.q.get_nowait()
            except Exception:
                break

        self.owner._ignore_audio_until = time.time() + 0.5

        self.reset_wakeword_recognizer()
        if hasattr(self.owner, 'provider') and self.owner.provider:
            self.owner.provider.reset()

        self.owner._listening_start_time = time.time()
        self.owner._last_speech_time = None
        self.owner._last_partial_text = ""
        self.owner._last_partial_change_time = None
        self.owner.set_state("listening")
        logger.info("Ora ti ascolto... Parla!")

        if hasattr(self.owner, 'audio_player') and self.owner.audio_player:
            self.owner.audio_player.play_wakeword_chime()

    def _audio_loop(self):
        import numpy as np
        silence_timeout = 1.0

        if not hasattr(self.owner, 'audio_filter') or self.owner.audio_filter is None:
            from audio.filter import AudioFilter
            from core.audio_runtime import build_filter_config
            self.owner.audio_filter = AudioFilter(
                sample_rate=16000,
                **build_filter_config(getattr(self.owner, 'settings', None)),
            )

        try:
            while True:
                try:
                    raw_data = self.owner.q.get(timeout=0.2)
                except Exception:
                    raw_data = None

                if self.owner._state == "disabled":
                    continue

                data = self.owner.audio_filter.process(raw_data) if raw_data else b""

                ignore_until = getattr(self.owner, '_ignore_audio_until', 0)
                if time.time() < ignore_until:
                    if hasattr(self.owner, 'provider') and self.owner.provider:
                        self.owner.provider.reset()
                    continue

                if self.owner._state in ("idle", "speaking", "processing", "AssistantState.IDLE", "AssistantState.SPEAKING", "AssistantState.PROCESSING"):
                    engine = getattr(self.owner, 'wakeword_engine', 'vosk')
                    if engine == 'openwakeword':
                        if getattr(self.owner, 'oww_model_instance', None) is not None:
                            self._check_oww_wakeword(data)
                        continue
                    if engine == 'sherpa-onnx':
                        if getattr(self.owner, 'sherpa_spotter', None) is not None:
                            self._check_sherpa_wakeword(data)
                        continue
                    ww_recognizer = self.owner.ww_recognizer
                    if ww_recognizer:
                        import json as json_mod
                        wakeword_lower = self.owner.wakeword.lower().strip()
                        ww_no_h = wakeword_lower.replace('h', '')

                        recognized_str = ""
                        if ww_recognizer.AcceptWaveform(data):
                            res_json = ww_recognizer.Result()
                            res = json_mod.loads(res_json)
                            recognized_str = res.get("text", "").strip().lower()
                        else:
                            partial_json = ww_recognizer.PartialResult()
                            partial = json_mod.loads(partial_json)
                            recognized_str = partial.get("partial", "").strip().lower()

                        ww_variants = {wakeword_lower, ww_no_h}
                        if wakeword_lower == "assistente":
                            ww_variants.update(["assistenti", "assistenza", "assiste"])
                        elif "anthon" in wakeword_lower or "anton" in wakeword_lower:
                            ww_variants.update(["anthon", "anton", "antonio", "antoni", "anto", "anthony"])

                        is_speaking_or_proc = self.owner._state in ("speaking", "processing", "AssistantState.SPEAKING", "AssistantState.PROCESSING")
                        if is_speaking_or_proc:
                            ww_variants.update(["stop", "basta", "zitto", "fermati", "silenzio", "interrompi", "cancella"])

                        words = recognized_str.split()

                        if is_speaking_or_proc:
                            matched_ww = next((v for v in ww_variants if v in words), None)
                        else:
                            matched_ww = next((v for v in ww_variants if v in words or (len(v) >= 4 and v in recognized_str)), None)
                            if not matched_ww and len(wakeword_lower) >= 4:
                                min_word_len = max(4, len(wakeword_lower) - 3)
                                for w in words:
                                    if len(w) >= min_word_len:
                                        ratio = difflib.SequenceMatcher(None, ww_no_h, w.replace('h', '')).ratio()
                                        if ratio >= 0.80:
                                            matched_ww = w
                                            break

                        if matched_ww:
                            logger.info(f"--- Wakeword/Barge-in '{matched_ww}' rilevata in: '{recognized_str}'! Interruzione in corso... ---")
                            parts = recognized_str.split(matched_ww, 1)
                            remainder = parts[1].strip() if len(parts) > 1 else ""

                            self.trigger_assistant()

                            filler_words = {"e", "ed", "uh", "um", "ah", "oh", "eh", "o", "il", "la", "le", "lo", "un", "una", "uno", "a", "di", "da", "in", "con", "su", "per", "tra", "fra"}
                            remainder_words = [w for w in remainder.split() if w not in filler_words]

                            is_valid_command = False
                            if len(remainder_words) >= 2:
                                is_valid_command = True
                            elif len(remainder_words) == 1 and hasattr(self.owner, 'fast_path'):
                                matched, _, _, _ = self.owner.fast_path.dispatch(remainder)
                                if matched:
                                    is_valid_command = True

                            if is_valid_command:
                                logger.info(f"Comando allegato alla wakeword valido: '{remainder}'")
                                self._process_text(remainder, is_voice=True)
                                self.owner._listening_start_time = None
                                self.owner._last_speech_time = None
                                self.owner._last_partial_text = ""
                                self.owner._last_partial_change_time = None

                elif self.owner._state in ("listening", "AssistantState.LISTENING"):
                    if not hasattr(self.owner, 'provider') or not self.owner.provider:
                        continue

                    text, partial_text = self.owner.provider.process_chunk(data)
                    now = time.time()

                    if not hasattr(self.owner, '_listening_start_time') or self.owner._listening_start_time is None:
                        self.owner._listening_start_time = now

                    filler_words = {"e", "ed", "uh", "um", "ah", "oh", "eh", "o", "il", "la", "le", "lo", "un", "una", "uno", "a", "di", "da", "in", "con", "su", "per", "tra", "fra"}
                    ww_lower = self.owner.wakeword.lower().strip()
                    ww_noh = ww_lower.replace('h', '')
                    ww_known_variants = {ww_lower, ww_noh}
                    if ww_lower == "assistente":
                        ww_known_variants.update(["assistenti", "assistenza", "assiste"])
                    elif "anthon" in ww_lower or "anton" in ww_lower:
                        ww_known_variants.update(["anthon", "anton", "antonio", "antoni", "anto", "anthony"])

                    if text:
                        self.owner._listening_start_time = None
                        self.owner._last_partial_text = ""
                        self.owner._last_partial_change_time = None
                        words_in_text = [w.lower() for w in text.strip().split()]
                        meaningful = [w for w in words_in_text if w not in filler_words]
                        is_only_ww = all(w in ww_known_variants for w in meaningful) if meaningful else True

                        if is_only_ww:
                            logger.info(f"Trascrizione immediata '{text}' contiene solo la wakeword, torno in idle.")
                            self.owner.provider.reset()
                            self.owner.set_state("idle")
                        else:
                            self._process_text(text, is_voice=True)
                        continue

                    partial_clean = partial_text.strip().lower()
                    last_partial = getattr(self.owner, '_last_partial_text', "")
                    last_change = getattr(self.owner, '_last_partial_change_time', None)

                    if partial_clean:
                        if partial_clean != last_partial:
                            self.owner._last_partial_text = partial_clean
                            self.owner._last_partial_change_time = now

                    if last_change and (now - last_change) >= 1.0:
                        logger.info(f"Silenzio/Stabilità parziale per 1.0s ('{last_partial}'), procedo con la trascrizione...")
                        batch_text = self.owner.provider.flush_and_transcribe()
                        self.owner._listening_start_time = None
                        self.owner._last_partial_text = ""
                        self.owner._last_partial_change_time = None

                        words_in_batch = [w.lower() for w in batch_text.strip().split()]
                        meaningful = [w for w in words_in_batch if w not in filler_words]
                        is_only_ww = all(w in ww_known_variants for w in meaningful) if meaningful else True

                        if batch_text and not is_only_ww:
                            self._process_text(batch_text, is_voice=True)
                        else:
                            logger.info("Trascrizione finale vuota o contenente solo la wakeword, ritorno in idle.")
                            self.owner.set_state("idle")

                    elif not last_change and (now - self.owner._listening_start_time) >= 2.5:
                        logger.info("Nessun parlato rilevato entro 2.5 secondi, chiusura ascolto e ritorno in idle.")
                        if hasattr(self.owner.provider, 'reset'):
                            self.owner.provider.reset()
                        self.owner._listening_start_time = None
                        self.owner._last_partial_text = ""
                        self.owner._last_partial_change_time = None
                        self.owner.set_state("idle")

                    elif (now - self.owner._listening_start_time) >= 6.0:
                        logger.info("Timeout massimo ascolto raggiunto (6s), ritorno in idle.")
                        batch_text = self.owner.provider.flush_and_transcribe()
                        self.owner._listening_start_time = None
                        self.owner._last_partial_text = ""
                        self.owner._last_partial_change_time = None

                        words_in_batch = [w.lower() for w in batch_text.strip().split()]
                        meaningful = [w for w in words_in_batch if w not in filler_words]
                        is_only_ww = all(w in ww_known_variants for w in meaningful) if meaningful else True

                        if batch_text and not is_only_ww:
                            self._process_text(batch_text, is_voice=True)
                        else:
                            self.owner.set_state("idle")

        except Exception as e:
            logger.critical(f"Errore critico nel thread audio: {e}", exc_info=True)
            self.owner._report_error(e)
            time.sleep(2)
            if self.owner._state != "disabled":
                logger.info("Tentativo di riavvio del thread audio...")
                self.owner._audio_thread = threading.Thread(target=self.owner._audio_loop, daemon=True)
                self.owner._audio_thread.start()

    def _process_text(self, text, is_voice=False):
        if not text or not text.strip():
            return

        logger.info(f"[Testo Riconosciuto]: {text} (is_voice={is_voice})")

        if is_voice:
            try:
                self.owner.TranscriptReceived(text, True)
            except Exception:
                pass

        self.owner.set_state("processing")

        res = self.owner.pipeline_controller.process_text_input(text, speak=is_voice)

        if res.get("fast_path") and res.get("response"):
            resp = res.get("response")
            try:
                self.owner.ResponseTokenStreamed(resp, True)
            except Exception:
                pass
        elif res.get("medium_path") and res.get("response"):
            resp = res.get("response")
            try:
                self.owner.ResponseTokenStreamed(resp, True)
            except Exception:
                pass
        elif res.get("smart_path") or (not res.get("fast_path") and res.get("response")):
            # Streamed tokens were emitted via on_token_callback; signal stream completion
            try:
                self.owner.ResponseTokenStreamed("", True)
            except Exception:
                pass
        elif not res.get("fast_path") and not res.get("response"):
            resp = f"Ho ascoltato: {text}"
            if is_voice:
                logger.info(f"[TTS Fallback] Sintesi vocale per: '{text}'")
                self.owner.tts_manager.speak(resp)
            try:
                self.owner.ResponseTokenStreamed(resp, True)
            except Exception:
                pass

        if not getattr(self.owner.audio_player, 'is_playing', False) and not (is_voice and self.owner._state == "speaking"):
            GLib.idle_add(self.owner.set_state, "idle")
