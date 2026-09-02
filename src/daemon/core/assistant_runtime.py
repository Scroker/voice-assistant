"""Interaction runtime helpers for wakeword, audio loop and text processing."""

import difflib
import json
import logging
import threading
import time

from gi.repository import GLib

logger = logging.getLogger("VoiceAssistant.AssistantRuntime")


class AssistantRuntimeController:
    """Encapsulates wakeword detection, TTS interaction, and audio processing flow."""

    def __init__(self, owner):
        self.owner = owner

    def _handle_fast_path_intent(self, intent_name: str, params):
        if not self.owner.mcp_manager:
            return (False, "")

        try:
            if intent_name == "set_volume":
                vol = params.get("volume", 50)
                res = self.owner.mcp_manager.execute_tool("system_volume", {"action": "set", "level": vol})
                return (True, res)
            elif intent_name == "volume_up":
                res = self.owner.mcp_manager.execute_tool("system_volume", {"action": "increase", "level": 10})
                return (True, res)
            elif intent_name == "volume_down":
                res = self.owner.mcp_manager.execute_tool("system_volume", {"action": "decrease", "level": 10})
                return (True, res)
            elif intent_name == "mute":
                res = self.owner.mcp_manager.execute_tool("system_volume", {"action": "mute"})
                return (True, res)
            elif intent_name == "set_theme_dark":
                res = self.owner.mcp_manager.execute_tool("dark_mode", {"mode": "dark"})
                return (True, res)
            elif intent_name == "set_theme_light":
                res = self.owner.mcp_manager.execute_tool("dark_mode", {"mode": "light"})
                return (True, res)
            elif intent_name == "launch_app":
                app = params.get("app", "firefox")
                res = self.owner.mcp_manager.execute_tool("app_launcher", {"app_name": app})
                return (True, res)
            elif intent_name == "get_time":
                res = self.owner.mcp_manager.execute_tool("date_time", {"action": "time"})
                return (True, res)
            elif intent_name == "get_date":
                res = self.owner.mcp_manager.execute_tool("date_time", {"action": "date"})
                return (True, res)
            elif intent_name == "media_pause":
                res = self.owner.mcp_manager.execute_tool("system_media", {"action": "pause"})
                return (True, res)
            elif intent_name == "media_play":
                res = self.owner.mcp_manager.execute_tool("system_media", {"action": "play"})
                return (True, res)
        except Exception as e:
            logger.error(f"Errore esecuzione Fast-Path MCP: {e}")

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

    def reset_wakeword_recognizer(self):
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
            self.owner.ww_recognizer = KaldiRecognizer(ww_model, 16000)
        except Exception as e:
            logger.warning(f"Errore reset ww_recognizer: {e}")
            self.owner.ww_recognizer = None

    def _start_speaking_watchdog(self):
        ticks = 0

        def _check():
            nonlocal ticks
            ticks += 1
            if str(self.owner._state).lower().endswith("speaking") or self.owner._state == "speaking":
                if ticks > 3 and not getattr(self.owner.audio_player, 'is_playing', False):
                    logger.info("[Watchdog] Rilevato stato 'speaking' senza audio in riproduzione. Ripristino stato 'idle'.")
                    self.owner.set_state("idle")
                    return False
                return True
            return False

        GLib.timeout_add(1000, _check)

    def _on_llm_token(self, token: str):
        if hasattr(self.owner, '_gui_window') and self.owner._gui_window is not None:
            GLib.idle_add(self.owner._gui_window.append_assistant_token, token)
        try:
            self.owner.ResponseTokenStreamed(token, False)
        except Exception:
            pass

    def _on_playback_finished(self):
        if str(self.owner._state).lower().endswith("speaking") or self.owner._state == "speaking":
            logger.info("[AudioPlayer] Riproduzione audio completata. Ripristino stato idle.")
            GLib.idle_add(self.owner.set_state, "idle")

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

        self.owner._ignore_audio_until = time.time() + 0.35

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
            self.owner.audio_filter = AudioFilter(sample_rate=16000)

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
                    if self.owner.ww_recognizer:
                        import json as json_mod
                        wakeword_lower = self.owner.wakeword.lower().strip()
                        ww_no_h = wakeword_lower.replace('h', '')

                        recognized_str = ""
                        if self.owner.ww_recognizer.AcceptWaveform(data):
                            res_json = self.owner.ww_recognizer.Result()
                            res = json_mod.loads(res_json)
                            recognized_str = res.get("text", "").strip().lower()
                        else:
                            partial_json = self.owner.ww_recognizer.PartialResult()
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
                            if not matched_ww and len(wakeword_lower) >= 3:
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
                                self._process_text(remainder)
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
                    ww_known_variants = {ww_lower, ww_noh, "assistente", "anton", "anto", "antonio", "anthony"}

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

        if is_voice and hasattr(self.owner, '_gui_window') and self.owner._gui_window is not None:
            GLib.idle_add(self.owner._gui_window.add_user_message, text)

        try:
            self.owner.TranscriptReceived(text, True)
        except Exception:
            pass

        self.owner.set_state("processing")

        res = self.owner.pipeline_controller.process_text_input(text, speak=is_voice)

        if res.get("fast_path") and res.get("response"):
            resp = res.get("response")
            if not is_voice and hasattr(self.owner, '_gui_window') and self.owner._gui_window is not None:
                GLib.idle_add(self.owner._gui_window.add_assistant_message, resp)
            try:
                self.owner.ResponseTokenStreamed(resp, True)
            except Exception:
                pass
        elif not res.get("fast_path") and not res.get("response"):
            resp = f"Ho ascoltato: {text}"
            if is_voice:
                logger.info(f"[TTS Fallback] Sintesi vocale per: '{text}'")
                self.owner.tts_manager.speak(resp)
            if hasattr(self.owner, '_gui_window') and self.owner._gui_window is not None:
                GLib.idle_add(self.owner._gui_window.add_assistant_message, resp)
            try:
                self.owner.ResponseTokenStreamed(resp, True)
            except Exception:
                pass

        if hasattr(self.owner, '_gui_window') and self.owner._gui_window is not None:
            def _reset_bubble():
                if hasattr(self.owner._gui_window, 'current_assistant_bubble'):
                    self.owner._gui_window.current_assistant_bubble = None
                return False
            GLib.idle_add(_reset_bubble)

        if not getattr(self.owner.audio_player, 'is_playing', False) and not (is_voice and self.owner._state == "speaking"):
            GLib.idle_add(self.owner.set_state, "idle")
