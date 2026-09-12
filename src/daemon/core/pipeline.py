"""
Streaming Audio & LLM Response Pipeline with Fast-Path Intent Dispatcher

Performance Metrics Integration:
    This module can be instrumented with performance_metrics decorators:
    
    Example:
        from core.performance_metrics import OperationContext, measure_latency
        
        with OperationContext("stt_processing", component="STT") as ctx:
            # STT processing here
            ctx.record_metric("vosk_decode", 150.5, status="success")
            ctx.record_metric("confidence_check", 50.2, status="success")
        
        # Log output will include operation_id for tracing
"""
import re
import json
import logging
from typing import Callable, Optional, Dict, Any, List, Tuple
from .state import StateMachine, AssistantState
from .smart_path_controller import SmartPathController
from skills.semantic_router import SemanticIntentRouter
from skills.skill_registry import SkillRegistry

logger = logging.getLogger("VoiceAssistant.Pipeline")

class SentenceAggregator:
    """
    Aggregates incoming streaming LLM text tokens into complete sentences.
    Emits full sentences immediately upon detecting terminal punctuation (. ! ? \n),
    avoiding premature splitting on common abbreviations.
    """
    PUNCT_REGEX = re.compile(r'(?<=[.!?\n])\s+')
    ABBREVIATIONS = {'art.', 'cap.', 'e.g.', 'i.e.', 'vs.', 'dr.', 'prof.', 'sig.', 'sig.ra', 'dott.', 'pag.'}

    def __init__(self, sentence_callback: Optional[Callable[[str], None]] = None):
        self.sentence_callback = sentence_callback
        self._buffer = ""

    @staticmethod
    def _is_technical_text(text: str) -> bool:
        """Restituisce True se la frase è un blocco di codice markdown o JSON tecnico."""
        clean = text.strip()
        if "```" in clean or clean.startswith("{") or clean.startswith("}") or clean.endswith("}"):
            return True
        if '"tool":' in clean or '"args":' in clean:
            return True
        return False

    def add_token(self, token: str) -> List[str]:
        """
        Aggiunge un token di testo allo stream. Restituisce la lista di frasi completate.
        """
        self._buffer += token
        completed_sentences = []

        parts = self.PUNCT_REGEX.split(self._buffer)
        if len(parts) > 1:
            for part in parts[:-1]:
                clean_part = part.strip()
                if clean_part:
                    words = clean_part.split()
                    if words and words[-1].lower() in self.ABBREVIATIONS:
                        continue
                    if self._is_technical_text(clean_part):
                        continue
                    completed_sentences.append(clean_part)
                    if self.sentence_callback:
                        self.sentence_callback(clean_part)
            self._buffer = parts[-1]

        return completed_sentences

    def flush(self) -> Optional[str]:
        """
        Svuota il buffer finale quando lo stream dell'LLM si interrompe.
        """
        remaining = self._buffer.strip()
        self._buffer = ""
        if remaining and not self._is_technical_text(remaining):
            if self.sentence_callback:
                self.sentence_callback(remaining)
            return remaining
        return None

    def reset(self):
        """Resetta lo stato del buffer."""
        self._buffer = ""


def load_number_words(lang: str = "") -> Dict[str, int]:
    """Carica la mappatura dei numeri in lettere da data/locales/number_words.json."""
    if not lang:
        try:
            from core.locale_utils import get_system_language
            lang = get_system_language(default="it")
        except Exception:
            lang = "it"
    from core.data_loader import load_json_data
    data = load_json_data("locales/number_words.json", fallback_default={}) or {}
    return data.get(lang) or data.get("it") or {
        'zero': 0, 'uno': 1, 'due': 2, 'tre': 3, 'quattro': 4, 'cinque': 5,
        'sei': 6, 'sette': 7, 'otto': 8, 'nove': 9, 'dieci': 10, 'quindici': 15,
        'venti': 20, 'trenta': 30, 'quaranta': 40, 'cinquanta': 50,
        'sessanta': 60, 'settanta': 70, 'ottanta': 80, 'novanta': 90, 'cento': 100
    }


def load_fast_path_responses(lang: str = "") -> Dict[str, str]:
    """Carica le frasi di risposta predefinite da data/locales/responses.json."""
    if not lang:
        try:
            from core.locale_utils import get_system_language
            lang = get_system_language(default="it")
        except Exception:
            lang = "it"
    from core.data_loader import load_json_data
    data = load_json_data("locales/responses.json", fallback_default={}) or {}
    loc_data = data.get(lang) or data.get("it") or {}
    return loc_data.get("fast_path", {})


def load_fast_path_patterns(lang: str = "") -> List[Tuple[str, str, Any, str]]:
    """Carica i pattern regex Fast-Path da data/nlu/fast_path_patterns.json."""
    from core.data_loader import load_json_data
    raw_patterns = load_json_data("nlu/fast_path_patterns.json", fallback_default=[]) or []
    responses = load_fast_path_responses(lang)
    res = []
    for item in raw_patterns:
        pattern = item.get("pattern", "")
        intent = item.get("intent", "")
        params = item.get("params", {})
        resp_key = item.get("response_key", intent)
        resp_tmpl = responses.get(resp_key, "")
        res.append((pattern, intent, lambda m, p=params: dict(p), resp_tmpl))
    return res


class FastPathDispatcher:
    """
    Fast-Path Vector & Intent Dispatcher for quick system actions (<10ms execution).
    Bypasses the LLM for direct deterministic commands (volume, theme, app launch).
    """

    IT_NUMBERS = load_number_words("it")
    INTENT_PATTERNS = load_fast_path_patterns("it")

    def __init__(
        self,
        intent_handler: Optional[Callable[[str, Dict[str, Any]], Tuple[bool, str]]] = None,
        enabled: bool = True,
    ):
        self.enabled = enabled
        self.intent_handler = intent_handler
        self.semantic_router = SemanticIntentRouter(SkillRegistry.from_default_directory())
        self.semantic_min_score = SemanticIntentRouter.DEFAULT_MIN_SCORE
        self._skill_patterns = self._load_skill_patterns()
        self.number_words = load_number_words()
        self.intent_patterns = load_fast_path_patterns()
        self.responses = load_fast_path_responses()

    def _load_skill_patterns(self):
        """Carica pattern regex e tool routing dai file di skill."""
        import json as _json
        registry = SkillRegistry.from_default_directory()
        patterns = []
        for skill in registry.skills:
            pattern = skill.get("pattern", "")
            if not pattern:
                continue
            patterns.append({
                "pattern": pattern,
                "intent": skill.get("intent", ""),
                "tool": skill.get("tool", ""),
                "args": skill.get("args") or {},
                "param_extract": skill.get("param_extract", ""),
                "param_key": skill.get("param_key", ""),
            })
        return patterns

    def reload_skills(self) -> None:
        """Rebuild the skill-derived semantic router and regex patterns.

        Called after the Skills console saves or deletes a custom skill so the
        running daemon picks it up without a restart.
        """
        self.semantic_router = SemanticIntentRouter(SkillRegistry.from_default_directory())
        self._skill_patterns = self._load_skill_patterns()

    def dispatch(self, text: str) -> Tuple[bool, Optional[str], Dict[str, Any], Optional[str]]:
        """
        Analizza il testo. Se corrisponde a un intent Fast-Path, lo esegue e restituisce:
        (matched: bool, intent_name: str|None, params: dict, response_text: str|None)
        """
        if not self.enabled:
            return (False, None, {}, None)

        clean_text = text.strip().lower()
        if not clean_text:
            return (False, None, {}, None)

        # 1. Check Volume Set Intent (Digits or words)
        words = self.number_words or self.IT_NUMBERS
        vol_pattern = r'(?:impost[aeo]|metti|porta|regola|setta|cambia)?\s*(?:il\s*)?volume\s*(?:a|al|allo|del)?\s*(\d+|' + '|'.join(words.keys()) + r')\s*(?:%|per\s*cento)?'
        m_vol = re.search(vol_pattern, clean_text)
        if m_vol:
            raw_val = m_vol.group(1)
            val = int(raw_val) if raw_val.isdigit() else words.get(raw_val, 50)
            val = max(0, min(100, val))
            params = {'volume': val}
            vol_tmpl = self.responses.get("volume_set", "Volume del sistema impostato al {volume}%.")
            response_text = vol_tmpl.format(volume=val)
            if self.intent_handler:
                try:
                    success, custom_resp = self.intent_handler('set_volume', params, clean_text)
                    if custom_resp:
                        response_text = custom_resp
                except Exception as e:
                    logger.error(f"[FastPath] Errore handler set_volume: {e}")
            return (True, 'set_volume', params, response_text)

        # 2. Check Static Intent Patterns
        patterns_to_check = self.intent_patterns if self.intent_patterns else self.INTENT_PATTERNS
        for pattern, intent_name, param_extractor, response_template in patterns_to_check:
            match = re.search(pattern, clean_text)
            if match:
                params = param_extractor(match)
                response_text = response_template.format(**params) if params else response_template
                
                if self.intent_handler:
                    try:
                        success, custom_resp = self.intent_handler(intent_name, params, clean_text)
                        if custom_resp:
                            response_text = custom_resp
                    except Exception as e:
                        logger.error(f"[FastPath] Errore esecuzione handler intent {intent_name}: {e}")

                return (True, intent_name, params, response_text)

        # 2b. Skill-defined patterns (loaded from .md files)
        for sp in self._skill_patterns:
            m = re.search(sp["pattern"], clean_text)
            if not m:
                continue
            params: dict = dict(sp["args"])
            if sp["param_extract"] and sp["param_key"]:
                pm = re.search(sp["param_extract"], clean_text)
                if pm:
                    params[sp["param_key"]] = pm.group(1).strip()
            intent_name = sp["intent"]
            if self.intent_handler:
                try:
                    success, custom_resp = self.intent_handler(intent_name, params, clean_text)
                    if success:
                        return (True, intent_name, params, custom_resp or "")
                except Exception as e:
                    logger.error(f"[FastPath] Errore skill pattern '{intent_name}': {e}")
            return (True, intent_name, params, "")

        # 2c. Catch-all per "apri/avvia [nome app]" non catturato dai pattern statici
        m_app_generic = re.search(
            r'(?:apri|avvia|lancia)\s+(?:il\s+|la\s+|le\s+|l\'|i\s+)?([\w\s]{2,30})$',
            clean_text
        )
        if m_app_generic:
            app_name = m_app_generic.group(1).strip()
            if app_name and not re.search(r'\b(?:il|la|le|lo|volume|suono|audio|luminosità)\b', app_name):
                params_app = {"app": app_name}
                if self.intent_handler:
                    try:
                        success, resp = self.intent_handler("launch_app", params_app, clean_text)
                        if success:
                            return (True, "launch_app", params_app, resp or f"Apro {app_name}.")
                    except Exception as e:
                        logger.error(f"[FastPath] Errore catch-all app launch: {e}")

        semantic_match = self.semantic_router.match(clean_text, min_score=self.semantic_min_score)
        if semantic_match:
            intent_name = semantic_match["intent"]
            params = dict((semantic_match.get("skill") or {}).get("params", {}))
            response_text = f"Intento semantico rilevato: {intent_name}."

            if self.intent_handler:
                try:
                    success, custom_resp = self.intent_handler(intent_name, params, clean_text)
                    if custom_resp:
                        response_text = custom_resp
                except Exception as e:
                    logger.error(f"[FastPath] Errore esecuzione handler intent semantico {intent_name}: {e}")

            if intent_name == "volume_up":
                params.setdefault("delta", 10)
                params.setdefault("volume", 60)
            elif intent_name == "volume_down":
                params.setdefault("delta", -10)
                params.setdefault("volume", 40)
            elif intent_name == "mute":
                params.setdefault("volume", 0)
            elif intent_name == "set_theme_dark":
                params.setdefault("dark", True)
            elif intent_name == "set_theme_light":
                params.setdefault("dark", False)

            return (True, intent_name, params, response_text)

        return (False, None, {}, None)


class PipelineController:
    """
    Main Controller for the Voice Assistant Streaming Pipeline.
    Manages transitions across Listening -> Processing -> Speaking -> Idle.
    """

    def __init__(
        self,
        state_machine: StateMachine,
        audio_player: Optional[Any] = None,
        llm_streamer: Optional[Callable[[str], Any]] = None,
        tts_engine: Optional[Callable[[str], None]] = None,
        mcp_manager: Optional[Any] = None,
        fast_path_enabled: bool = False,
    ):
        self.state_machine = state_machine
        self.audio_player = audio_player
        self.llm_streamer = llm_streamer
        self.tts_engine = tts_engine
        self.mcp_manager = mcp_manager

        self._fast_path_enabled = fast_path_enabled
        self.fast_path = FastPathDispatcher(enabled=fast_path_enabled)
        self.smart_path = SmartPathController()
        self.sentence_aggregator = SentenceAggregator(sentence_callback=self._on_sentence_ready)
        self._streaming_active = False

    @property
    def fast_path_enabled(self) -> bool:
        """Flag per abilitare o disabilitare l'esecuzione del Fast-Path."""
        return self._fast_path_enabled

    @fast_path_enabled.setter
    def fast_path_enabled(self, value: bool) -> None:
        self._fast_path_enabled = bool(value)
        if hasattr(self, 'fast_path') and self.fast_path:
            self.fast_path.enabled = self._fast_path_enabled

    def _on_sentence_ready(self, sentence: str):
        """Callback invocata dall'aggregatore quando una frase completa è pronta."""
        if not getattr(self, '_current_speak', True):
            return
        logger.info(f"[Pipeline] Frase pronta per TTS: '{sentence}'")
        if self.tts_engine:
            try:
                self.tts_engine(sentence)
            except Exception as e:
                logger.error(f"[Pipeline] Errore sintesi TTS della frase '{sentence}': {e}")

    def process_text_input(self, text: str, speak: bool = True) -> Dict[str, Any]:
        """
        Elabora il testo trascritto dall'STT o inviato da GUI.
        Se speak=False, la risposta è puramente testuale nella GUI senza riproduzione audio TTS.
        """
        self._current_speak = speak
        if not text or not text.strip():
            self.state_machine.set_state(AssistantState.IDLE)
            return {"fast_path": False, "transcription": "", "response": ""}

        if speak and self.audio_player and hasattr(self.audio_player, 'prepare_playback'):
            self.audio_player.prepare_playback()

        self.state_machine.set_state(AssistantState.PROCESSING)

        # 1. Fast-Path Check (<10ms) - solo se abilitato
        if self.fast_path_enabled:
            matched, intent, params, response_text = self.fast_path.dispatch(text)
            if matched and response_text:
                logger.info(f"[Pipeline] Fast-Path match: {intent} -> '{response_text}' (speak={speak})")
                if speak:
                    self.state_machine.set_state(AssistantState.SPEAKING)
                    if self.tts_engine:
                        self.tts_engine(response_text)
                if not speak or not (self.audio_player and getattr(self.audio_player, 'is_playing', False) or self.state_machine.state == AssistantState.SPEAKING):
                    self.state_machine.set_state(AssistantState.IDLE)
                return {
                    "fast_path": True,
                    "intent": intent,
                    "params": params,
                    "transcription": text,
                    "response": response_text
                }

        # 2. SMART PATH Check (with RAG, Memory, LLM) - only if MCP is available
        if self.mcp_manager:
            logger.info(f"[Pipeline] Fast-Path no match, attempting SMART PATH: '{text}' (speak={speak})")
            try:
                success, smart_response, tool_result = self.smart_path.execute_smart_path(
                    text,
                    llm_streamer=self.llm_streamer,
                    mcp_manager=self.mcp_manager,
                    token_callback=getattr(self, 'on_token_callback', None),
                    sentence_callback=self._on_sentence_ready if speak else None,
                )
                
                if success and smart_response:
                    logger.info(f"[Pipeline] Smart-Path success: '{smart_response}' (speak={speak})")
                    if speak:
                        self.state_machine.set_state(AssistantState.SPEAKING)
                    if not speak or not (self.audio_player and getattr(self.audio_player, 'is_playing', False) or self.state_machine.state == AssistantState.SPEAKING):
                        self.state_machine.set_state(AssistantState.IDLE)
                    return {
                        "fast_path": False,
                        "smart_path": True,
                        "transcription": text,
                        "response": smart_response,
                        "tool_result": tool_result,
                    }
            except Exception as e:
                logger.warning(f"[Pipeline] SMART PATH error, falling back to LLM: {e}")
        else:
            logger.debug("[Pipeline] MCP Manager not available, skipping SMART PATH")

        # 3. LLM Streaming Path (Fallback)
        logger.info(f"[Pipeline] Nessun Fast-Path, invio all'LLM Streaming: '{text}' (speak={speak})")
        full_response = ""
        self.sentence_aggregator.reset()
        self._streaming_active = True

        if self.llm_streamer:
            try:
                self.state_machine.set_state(AssistantState.PROCESSING)
                for token in self.llm_streamer(text):
                    if not self._streaming_active:
                        break
                    full_response += token
                    if getattr(self, 'on_token_callback', None):
                        try:
                            self.on_token_callback(token)
                        except Exception:
                            pass
                    self.sentence_aggregator.add_token(token)

                self.sentence_aggregator.flush()
            except Exception as e:
                logger.error(f"[Pipeline] Errore durante lo streaming LLM: {e}")
            finally:
                self._streaming_active = False

        if not speak or not (self.audio_player and getattr(self.audio_player, 'is_playing', False) or self.state_machine.state == AssistantState.SPEAKING):
            self.state_machine.set_state(AssistantState.IDLE)
        return {
            "fast_path": False,
            "transcription": text,
            "response": full_response
        }

    def cancel_pipeline(self, target_state=AssistantState.IDLE):
        """Interrompe immediatamente l'elaborazione corrente."""
        self._streaming_active = False
        self.sentence_aggregator.reset()
        if self.audio_player and hasattr(self.audio_player, 'stop_playback'):
            self.audio_player.stop_playback()
        if target_state:
            self.state_machine.set_state(target_state)
