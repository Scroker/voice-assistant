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
import logging
from typing import Callable, Optional, Dict, Any, List, Tuple
from .state import StateMachine, AssistantState
from .smart_path_controller import SmartPathController
from skills.vector_intent_matcher import VectorIntentMatcher
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


class FastPathDispatcher:
    """
    Fast-Path Vector & Intent Dispatcher for quick system actions (<10ms execution).
    Bypasses the LLM for direct deterministic commands (volume, brightness, theme, app launch).
    """

    IT_NUMBERS = {
        'zero': 0, 'uno': 1, 'due': 2, 'tre': 3, 'quattro': 4, 'cinque': 5,
        'sei': 6, 'sette': 7, 'otto': 8, 'nove': 9, 'dieci': 10, 'quindici': 15,
        'venti': 20, 'trenta': 30, 'quaranta': 40, 'cinquanta': 50,
        'sessanta': 60, 'settanta': 70, 'ottanta': 80, 'novanta': 90, 'cento': 100
    }

    INTENT_PATTERNS = [
        # Theme / Appearance
        (r'(?:attiva|metti|imposta)?\s*(?:la\s*)?(?:modalità|tema)\s+scur[ao]', 'set_theme_dark', lambda m: {'dark': True}, 'Modalità scura attivata'),
        (r'(?:attiva|metti|imposta)?\s*(?:la\s*)?(?:modalità|tema)\s+chiar[ao]', 'set_theme_light', lambda m: {'dark': False}, 'Modalità chiara attivata'),

        # Date & Time
        (r'(?:che\s+ore?\s+sono|che\s+ora\s+è|orario|dimmi\s+l\'ora)', 'get_time', lambda m: {}, 'Orario richiesto'),
        (r'(?:che\s+giorno\s+è|data\s+di\s+oggi|dimmi\s+la\s+data)', 'get_date', lambda m: {}, 'Data richiesta'),

        # App Launchers
        (r'(?:apri|aprire|avrei|avvia|lancia|mostra|mostrami|fammi\s+vedere|aprimi)?\s*(?:il\s+|la\s+|le\s+|l\'|i\s+)?(?:browser|firefox)', 'launch_app', lambda m: {'app': 'firefox'}, 'Apro Firefox'),
        (r'(?:apri|aprire|avrei|avvia|lancia|mostra|mostrami|fammi\s+vedere|aprimi)?\s*(?:il\s+|la\s+|le\s+|l\'|i\s+)?terminale', 'launch_app', lambda m: {'app': 'terminal'}, 'Apro il terminale'),
        (r'(?:apri|aprire|avrei|avvia|lancia|mostra|mostrami|fammi\s+vedere|aprimi)?\s*(?:il\s+|la\s+|le\s+|l\'|i\s+)?calcolatrice', 'launch_app', lambda m: {'app': 'calculator'}, 'Apro la calcolatrice'),
        (r'(?:apri|aprire|avrei|avvia|lancia|mostra|mostrami|fammi\s+vedere|aprimi)?\s*(?:il\s+|la\s+|le\s+|l\'|i\s+)?calendario', 'launch_app', lambda m: {'app': 'calendario'}, 'Apro il calendario'),
        (r'(?:apri|aprire|avrei|avvia|lancia|mostra|mostrami|fammi\s+vedere|aprimi)?\s*(?:le\s+|i\s+|l\'|la\s+)?impostazioni', 'launch_app', lambda m: {'app': 'impostazioni'}, 'Apro le impostazioni'),
        (r'(?:apri|aprire|avrei|avvia|lancia|mostra|mostrami|fammi\s+vedere|aprimi)?\s*(?:l\'|il\s+)?orologio', 'launch_app', lambda m: {'app': 'orologio'}, 'Apro l\'orologio'),
        (r'(?:apri|aprire|avrei|avvia|lancia|mostra|mostrami|fammi\s+vedere|aprimi)?\s*(?:i\s+|la\s+cartella\s+)?file', 'launch_app', lambda m: {'app': 'nautilus'}, 'Apro i file'),

        # Media Player
        (r'pausa|interrompi\s+musica', 'media_pause', lambda m: {}, 'Musica in pausa'),
        (r'riproduci|play', 'media_play', lambda m: {}, 'Riproduzione avviata'),

        # Relative Volume / Mute
        (r'alza\s+(?:il\s+)?volume', 'volume_up', lambda m: {'delta': 10}, 'Volume alzato'),
        (r'abbassa\s+(?:il\s+)?volume', 'volume_down', lambda m: {'delta': -10}, 'Volume abbassato'),
        (r'(?:silenzia|disattiva)\s+(?:il\s+)?audio', 'mute', lambda m: {}, 'Audio silenziato'),
    ]

    def __init__(self, intent_handler: Optional[Callable[[str, Dict[str, Any]], Tuple[bool, str]]] = None):
        self.intent_handler = intent_handler
        self.vector_matcher = VectorIntentMatcher(SkillRegistry.from_default_directory())

    def dispatch(self, text: str) -> Tuple[bool, Optional[str], Dict[str, Any], Optional[str]]:
        """
        Analizza il testo. Se corrisponde a un intent Fast-Path, lo esegue e restituisce:
        (matched: bool, intent_name: str|None, params: dict, response_text: str|None)

        Questo mantiene il comportamento deterministico del regex, ma aggiunge un fallback
        semantico offline per varianti colloquiali non esplicitamente matchate.
        """
        clean_text = text.strip().lower()
        if not clean_text:
            return (False, None, {}, None)

        # 1. Check Volume Set Intent (Digits or Italian words)
        vol_pattern = r'(?:impost[aeo]|metti|porta|regola|setta|cambia)?\s*(?:il\s*)?volume\s*(?:a|al|allo|del)?\s*(\d+|' + '|'.join(self.IT_NUMBERS.keys()) + r')\s*(?:%|per\s*cento)?'
        m_vol = re.search(vol_pattern, clean_text)
        if m_vol:
            raw_val = m_vol.group(1)
            val = int(raw_val) if raw_val.isdigit() else self.IT_NUMBERS.get(raw_val, 50)
            val = max(0, min(100, val))
            params = {'volume': val}
            response_text = f"Volume del sistema impostato al {val}%."
            if self.intent_handler:
                try:
                    success, custom_resp = self.intent_handler('set_volume', params, clean_text)
                    if custom_resp:
                        response_text = custom_resp
                except Exception as e:
                    logger.error(f"[FastPath] Errore handler set_volume: {e}")
            return (True, 'set_volume', params, response_text)

        # 2. Check Static Intent Patterns
        for pattern, intent_name, param_extractor, response_template in self.INTENT_PATTERNS:
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

        semantic_match = self.vector_matcher.match(clean_text)
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
    ):
        self.state_machine = state_machine
        self.audio_player = audio_player
        self.llm_streamer = llm_streamer
        self.tts_engine = tts_engine
        self.mcp_manager = mcp_manager
        
        self.fast_path = FastPathDispatcher()
        self.smart_path = SmartPathController()
        self.sentence_aggregator = SentenceAggregator(sentence_callback=self._on_sentence_ready)
        self._streaming_active = False

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

        # 1. Fast-Path Check (<10ms)
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

        # 2. SMART PATH Check (with RAG, Memory, LLM)
        logger.info(f"[Pipeline] Fast-Path no match, attempting SMART PATH: '{text}' (speak={speak})")
        try:
            success, smart_response, tool_result = self.smart_path.execute_smart_path(
                text,
                llm_streamer=self.llm_streamer,
                mcp_manager=self.mcp_manager,
            )
            
            if success and smart_response:
                logger.info(f"[Pipeline] Smart-Path success: '{smart_response}' (speak={speak})")
                if speak:
                    self.state_machine.set_state(AssistantState.SPEAKING)
                    if self.tts_engine:
                        self.tts_engine(smart_response)
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
