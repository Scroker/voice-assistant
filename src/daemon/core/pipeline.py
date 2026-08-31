"""
Streaming Audio & LLM Response Pipeline with Fast-Path Intent Dispatcher
"""
import re
import logging
from typing import Callable, Optional, Dict, Any, List, Tuple
from .state import StateMachine, AssistantState

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
        if remaining:
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

    INTENT_PATTERNS = [
        # Volume control
        (r'imposta volume (?:a|al) (\d+)%', 'set_volume', lambda m: {'volume': int(m.group(1))}, 'Volume impostato al {volume}%'),
        (r'alza (?:il )?volume', 'volume_up', lambda m: {'delta': 10}, 'Volume alzato'),
        (r'abbassa (?:il )?volume', 'volume_down', lambda m: {'delta': -10}, 'Volume abbassato'),
        (r'(?:silenzia|disattiva) (?:il )?audio', 'mute', lambda m: {}, 'Audio silenziato'),

        # Theme / Appearance
        (r'attiva (?:la )?modalità scura|tema scuro', 'set_theme_dark', lambda m: {'dark': True}, 'Modalità scura attivata'),
        (r'attiva (?:la )?modalità chiara|tema chiaro', 'set_theme_light', lambda m: {'dark': False}, 'Modalità chiara attivata'),

        # App Launchers
        (r'apri (?:il )?browser|apri firefox', 'launch_app', lambda m: {'app': 'firefox'}, 'Apertura di Firefox in corso'),
        (r'apri (?:il )?terminale', 'launch_app', lambda m: {'app': 'terminal'}, 'Apertura del terminale in corso'),
        (r'apri (?:la )?calcolatrice', 'launch_app', lambda m: {'app': 'calculator'}, 'Apertura della calcolatrice in corso'),

        # Media Player
        (r'pausa|interrompi musica', 'media_pause', lambda m: {}, 'Musica in pausa'),
        (r'riproduci|play', 'media_play', lambda m: {}, 'Riproduzione avviata'),
    ]

    def __init__(self, intent_handler: Optional[Callable[[str, Dict[str, Any]], Tuple[bool, str]]] = None):
        self.intent_handler = intent_handler

    def dispatch(self, text: str) -> Tuple[bool, Optional[str], Dict[str, Any], Optional[str]]:
        """
        Analizza il testo. Se corrisponde a un intent Fast-Path, lo esegue e restituisce:
        (matched: bool, intent_name: str|None, params: dict, response_text: str|None)
        """
        clean_text = text.strip().lower()
        if not clean_text:
            return (False, None, {}, None)

        for pattern, intent_name, param_extractor, response_template in self.INTENT_PATTERNS:
            match = re.search(pattern, clean_text)
            if match:
                params = param_extractor(match)
                response_text = response_template.format(**params) if params else response_template
                
                if self.intent_handler:
                    try:
                        success, custom_resp = self.intent_handler(intent_name, params)
                        if custom_resp:
                            response_text = custom_resp
                    except Exception as e:
                        logger.error(f"[FastPath] Errore esecuzione handler intent {intent_name}: {e}")

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
        tts_engine: Optional[Callable[[str], None]] = None
    ):
        self.state_machine = state_machine
        self.audio_player = audio_player
        self.llm_streamer = llm_streamer
        self.tts_engine = tts_engine
        
        self.fast_path = FastPathDispatcher()
        self.sentence_aggregator = SentenceAggregator(sentence_callback=self._on_sentence_ready)
        self._streaming_active = False

    def _on_sentence_ready(self, sentence: str):
        """Callback invocata dall'aggregatore quando una frase completa è pronta."""
        logger.info(f"[Pipeline] Frase pronta per TTS: '{sentence}'")
        if self.tts_engine:
            try:
                self.tts_engine(sentence)
            except Exception as e:
                logger.error(f"[Pipeline] Errore sintesi TTS della frase '{sentence}': {e}")

    def process_text_input(self, text: str) -> Dict[str, Any]:
        """
        Elabora il testo trascritto dall'STT.
        Controlla prima il Fast-Path; se non c'è match, avvia il flusso LLM Streaming.
        """
        if not text or not text.strip():
            self.state_machine.set_state(AssistantState.IDLE)
            return {"fast_path": False, "transcription": "", "response": ""}

        self.state_machine.set_state(AssistantState.PROCESSING)

        # 1. Fast-Path Check (<10ms)
        matched, intent, params, response_text = self.fast_path.dispatch(text)
        if matched and response_text:
            logger.info(f"[Pipeline] Fast-Path match: {intent} -> '{response_text}'")
            self.state_machine.set_state(AssistantState.SPEAKING)
            if self.tts_engine:
                self.tts_engine(response_text)
            self.state_machine.set_state(AssistantState.IDLE)
            return {
                "fast_path": True,
                "intent": intent,
                "params": params,
                "transcription": text,
                "response": response_text
            }

        # 2. LLM Streaming Path
        logger.info(f"[Pipeline] Nessun Fast-Path, invio all'LLM Streaming: '{text}'")
        full_response = ""
        self.sentence_aggregator.reset()
        self._streaming_active = True

        if self.llm_streamer:
            try:
                self.state_machine.set_state(AssistantState.SPEAKING)
                for token in self.llm_streamer(text):
                    if not self._streaming_active:
                        break
                    full_response += token
                    self.sentence_aggregator.add_token(token)

                self.sentence_aggregator.flush()
            except Exception as e:
                logger.error(f"[Pipeline] Errore durante lo streaming LLM: {e}")
            finally:
                self._streaming_active = False

        self.state_machine.set_state(AssistantState.IDLE)
        return {
            "fast_path": False,
            "transcription": text,
            "response": full_response
        }

    def cancel_pipeline(self):
        """Interrompe immediatamente l'elaborazione corrente."""
        self._streaming_active = False
        self.sentence_aggregator.reset()
        if self.audio_player and hasattr(self.audio_player, 'stop'):
            self.audio_player.stop()
        self.state_machine.set_state(AssistantState.IDLE)
