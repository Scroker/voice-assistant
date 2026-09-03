"""
Integration adapter between StreamingPipelineEngine and existing assistant runtime.
Bridges the new concurrent pipeline with legacy callback patterns.
"""
import logging
import threading
import time
from typing import Optional, Callable, Dict, Any

from core.streaming_pipeline import StreamingPipelineEngine
from core.state import StateMachine, AssistantState
from core.performance_metrics import OperationContext

logger = logging.getLogger("VoiceAssistant.PipelineIntegration")


class StreamingPipelineController:
    """
    Adapter that wraps StreamingPipelineEngine for use with existing assistant_runtime.
    
    Converts callback-based patterns to streaming pipeline operations.
    """

    def __init__(self, state_machine: StateMachine, owner=None):
        """
        Initialize pipeline controller.
        
        Args:
            state_machine: StateMachine instance for state transitions
            owner: Reference to main daemon (for callbacks and settings)
        """
        self.state_machine = state_machine
        self.owner = owner
        self.pipeline: Optional[StreamingPipelineEngine] = None
        self.operation_context: Optional[OperationContext] = None
        self._lock = threading.Lock()

    def start_listening_loop(self):
        """Start the streaming pipeline for listening and processing."""
        with self._lock:
            if self.pipeline and self.pipeline.is_active():
                logger.warning("[Pipeline] Pipeline already running")
                return

            # Create operation context for distributed tracing
            self.operation_context = OperationContext(
                operation_id=f"listen_{int(time.time() * 1000) % 10000:04d}",
                component="StreamingPipeline"
            )

            # Setup component callbacks from owner
            audio_source = self._create_audio_source() if self.owner else None
            stt_processor = self._create_stt_processor() if self.owner else None
            intent_dispatcher = self._create_intent_dispatcher() if self.owner else None
            llm_streamer = self._create_llm_streamer() if self.owner else None
            tts_synthesizer = self._create_tts_synthesizer() if self.owner else None
            audio_player = self._create_audio_player() if self.owner else None

            # Create pipeline with all components
            self.pipeline = StreamingPipelineEngine(
                state_machine=self.state_machine,
                audio_source=audio_source,
                stt_processor=stt_processor,
                intent_dispatcher=intent_dispatcher,
                llm_streamer=llm_streamer,
                tts_synthesizer=tts_synthesizer,
                audio_player=audio_player
            )

            # Start pipeline with operation context
            self.pipeline.start(operation_context=self.operation_context)
            logger.info("[Pipeline] Streaming pipeline started")

    def stop_listening_loop(self):
        """Stop the streaming pipeline."""
        with self._lock:
            if self.pipeline:
                self.pipeline.stop()
                self.pipeline = None
                logger.info("[Pipeline] Streaming pipeline stopped")

            if self.operation_context:
                summary = self.operation_context.get_summary()
                logger.debug(f"[Pipeline] Operation summary: {summary}")
                self.operation_context = None

    def pause_pipeline(self):
        """Pause pipeline processing (audio capture continues)."""
        with self._lock:
            if self.pipeline:
                self.pipeline.pause()
                logger.debug("[Pipeline] Pipeline paused")

    def resume_pipeline(self):
        """Resume pipeline processing."""
        with self._lock:
            if self.pipeline:
                self.pipeline.resume()
                logger.debug("[Pipeline] Pipeline resumed")

    def cancel_pipeline(self, target_state: Optional[str] = None):
        """Cancel current pipeline operation."""
        with self._lock:
            if self.pipeline:
                self.pipeline.stop()
                self.pipeline = None
                logger.info(f"[Pipeline] Pipeline cancelled, target state: {target_state}")

            if target_state:
                self.state_machine.set_state(target_state)

    def get_pipeline_status(self) -> Dict[str, Any]:
        """Get current pipeline status."""
        with self._lock:
            if not self.pipeline:
                return {"status": "inactive"}

            return {
                "status": "active" if self.pipeline.is_active() else "stopped",
                "queues": self.pipeline.get_queue_status(),
                "running": self.pipeline._running,
            }

    # ===== Component Factories =====

    def _create_audio_source(self) -> Optional[Callable]:
        """Create audio source callback from owner's audio input."""
        audio_queue = getattr(self.owner, "q", None)
        if audio_queue is None:
            return None

        def audio_source():
            """Read audio chunk from queue."""
            try:
                chunk_data = audio_queue.get_nowait()
                from core.streaming_pipeline import AudioChunk
                return AudioChunk(
                    pcm_data=chunk_data,
                    sample_rate=16000,
                    timestamp=time.time()
                )
            except:
                return None

        return audio_source

    def _create_stt_processor(self) -> Optional[Callable]:
        """Create STT processor callback from owner's provider."""
        if not hasattr(self.owner, "provider"):
            return None

        def stt_processor(audio_chunk):
            """Process audio chunk with STT."""
            if not self.owner.provider:
                return None

            from core.streaming_pipeline import STTResult
            
            try:
                result = self.owner.provider.process_chunk(audio_chunk.pcm_data)

                if isinstance(result, tuple):
                    final_text, partial_text = result if len(result) >= 2 else (result[0], "")
                    return STTResult(
                        partial_text=partial_text or "",
                        final_text=final_text or "",
                        confidence=0.0,
                        is_final=bool(final_text),
                        timestamp=time.time()
                    )
                
                return STTResult(
                    partial_text=result.get("partial_text", ""),
                    final_text=result.get("final_text", ""),
                    confidence=result.get("confidence", 0.0),
                    is_final=result.get("is_final", False),
                    timestamp=time.time()
                )
            except Exception as e:
                logger.error(f"[Pipeline.STT] Error: {e}")
                return None

        return stt_processor

    def _create_intent_dispatcher(self) -> Optional[Callable]:
        """Create intent dispatcher callback."""
        fast_path = getattr(self.owner, "fast_path", None)
        if fast_path is None and getattr(self.owner, "pipeline_controller", None):
            fast_path = getattr(self.owner.pipeline_controller, "fast_path", None)
        if fast_path is None:
            return None

        def intent_dispatcher(text: str) -> Dict[str, Any]:
            """Check for fast-path intents."""
            try:
                matched, intent_name, params, response = fast_path.dispatch(text)
                
                if matched:
                    if hasattr(self.owner, "_handle_fast_path_intent"):
                        try:
                            success, response = self.owner._handle_fast_path_intent(intent_name, params)
                        except TypeError:
                            success, response = self.owner._handle_fast_path_intent(intent_name, params, text)
                    elif hasattr(self.owner, "assistant_runtime"):
                        success, response = self.owner.assistant_runtime._handle_fast_path_intent(
                            intent_name, params, text
                        )
                    else:
                        success = True
                    return {
                        "matched": matched,
                        "intent": intent_name,
                        "params": params,
                        "success": success,
                        "response": response
                    }
                return {"matched": False}
            except Exception as e:
                logger.error(f"[Pipeline.IntentDispatch] Error: {e}")
                return {"matched": False}

        return intent_dispatcher

    def _create_llm_streamer(self) -> Optional[Callable]:
        """Create LLM streamer callback."""
        llm_service = getattr(self.owner, "llm_service", None)
        if llm_service is None:
            return None

        def llm_streamer(user_text: str):
            """Stream LLM response tokens."""
            try:
                stream = (
                    llm_service.stream_tokens(user_text)
                    if hasattr(llm_service, "stream_tokens")
                    else llm_service.stream_response(user_text)
                )
                for token in stream:
                    if hasattr(self.owner, '_on_llm_token'):
                        self.owner._on_llm_token(token)
                    
                    yield token
                    
            except Exception as e:
                logger.error(f"[Pipeline.LLM] Streaming error: {e}")

        return llm_streamer

    def _create_tts_synthesizer(self) -> Optional[Callable]:
        """Create TTS synthesizer callback."""
        tts_service = getattr(self.owner, "tts_service", None) or getattr(self.owner, "tts_manager", None)
        if tts_service is None:
            return None

        def tts_synthesizer(text: str):
            """Synthesize text to speech."""
            if not text.strip():
                return None

            from core.streaming_pipeline import AudioOutput
            
            try:
                duration_ms = 0
                if hasattr(tts_service, "synthesize"):
                    result = tts_service.synthesize(text)
                    if isinstance(result, tuple):
                        wav_data, duration_ms = result
                    else:
                        wav_data = result
                elif hasattr(tts_service, "speak"):
                    ok = tts_service.speak(text)
                    if not ok:
                        return None
                    wav_data = b""
                else:
                    return None
                
                return AudioOutput(
                    wav_data=wav_data or b"",
                    sample_rate=22050,
                    duration_ms=duration_ms or max(len(text) * 45, 250),
                )
            except Exception as e:
                logger.error(f"[Pipeline.TTS] Synthesis error: {e}")
                return None

        return tts_synthesizer

    def _create_audio_player(self) -> Optional[Callable]:
        """Create audio player callback."""
        player = getattr(self.owner, "audio_player", None)
        if player is None:
            return None

        def audio_player(audio_output):
            """Play audio output."""
            try:
                if hasattr(player, "play_wav_bytes"):
                    player.play_wav_bytes(audio_output.wav_data)
                elif hasattr(player, "enqueue_audio"):
                    player.enqueue_audio(audio_output.wav_data, audio_output.sample_rate)
                elif hasattr(player, "enqueue_playback"):
                    player.enqueue_playback(audio_output.wav_data)
                else:
                    return
                
                if hasattr(self.owner, '_on_playback_finished'):
                    threading.Timer(
                        audio_output.duration_ms / 1000.0,
                        self.owner._on_playback_finished
                    ).start()
                    
            except Exception as e:
                logger.error(f"[Pipeline.AudioPlayback] Error: {e}")

        return audio_player
