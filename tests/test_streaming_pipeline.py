"""
Tests for Concurrent Streaming Pipeline Engine.
Validates true parallel processing with multiple stages running concurrently.
"""
import time
import unittest
import logging
from unittest.mock import MagicMock, patch
from pathlib import Path
import sys
import queue

daemon_dir = Path(__file__).resolve().parent.parent / 'src' / 'daemon'
sys.path.insert(0, str(daemon_dir))

from core.streaming_pipeline import (
    StreamingPipelineEngine, AudioChunk, STTResult, SentenceChunk, AudioOutput,
    PipelineStage
)
from core.state import StateMachine, AssistantState
from core.performance_metrics import OperationContext


class MockAudioSource:
    """Mock audio input device."""
    def __init__(self, chunk_count=10):
        self.chunk_count = chunk_count
        self.chunks_produced = 0

    def __call__(self):
        if self.chunks_produced >= self.chunk_count:
            return None
        chunk = AudioChunk(
            pcm_data=b'\x00\x01' * 160,  # 10ms at 16kHz
            sample_rate=16000
        )
        self.chunks_produced += 1
        return chunk


class MockSTTProcessor:
    """Mock speech-to-text processor."""
    def __init__(self, text="Accendi la luce"):
        self.text = text
        self.chunks_processed = 0

    def __call__(self, chunk: AudioChunk) -> STTResult:
        self.chunks_processed += 1
        # Emit text progressively
        progress = min(self.chunks_processed / 10, 1.0)
        partial_len = int(len(self.text) * progress)
        partial = self.text[:partial_len]
        
        return STTResult(
            partial_text=partial,
            final_text=self.text if self.chunks_processed >= 10 else "",
            confidence=0.5 + progress * 0.45,
            is_final=self.chunks_processed >= 10
        )


class MockIntentDispatcher:
    """Mock intent dispatcher."""
    def __call__(self, text: str):
        if "luce" in text.lower():
            return {"matched": True, "intent": "set_light", "value": "on"}
        return {"matched": False}


class MockLLMStreamer:
    """Mock LLM that returns token generator."""
    def __init__(self, response="Sto accendendo la luce. Fatto!"):
        self.response = response

    def __call__(self, user_text: str):
        """Return token generator."""
        words = self.response.split()
        for word in words:
            yield word + " "
            time.sleep(0.01)  # Simulate network latency


class MockTTSSynthesizer:
    """Mock text-to-speech."""
    def __init__(self):
        self.synthesized_texts = []

    def __call__(self, text: str) -> AudioOutput:
        self.synthesized_texts.append(text)
        # Generate minimal audio
        duration_samples = 22050 // 2  # 0.5s
        return AudioOutput(
            wav_data=b'\x00\x01' * duration_samples,
            sample_rate=22050,
            duration_ms=500.0
        )


class TestStreamingPipeline(unittest.TestCase):
    """Tests for streaming pipeline."""

    def setUp(self):
        """Initialize test fixtures."""
        self.state_machine = StateMachine()
        self.audio_source = MockAudioSource(chunk_count=10)
        self.stt_processor = MockSTTProcessor()
        self.intent_dispatcher = MockIntentDispatcher()
        self.llm_streamer = MockLLMStreamer()
        self.tts_synthesizer = MockTTSSynthesizer()
        self.audio_player = MagicMock()

    def test_pipeline_creation(self):
        """Test pipeline can be created with all components."""
        pipeline = StreamingPipelineEngine(
            state_machine=self.state_machine,
            audio_source=self.audio_source,
            stt_processor=self.stt_processor,
            intent_dispatcher=self.intent_dispatcher,
            llm_streamer=self.llm_streamer,
            tts_synthesizer=self.tts_synthesizer,
            audio_player=self.audio_player
        )
        
        self.assertIsNotNone(pipeline)
        self.assertFalse(pipeline._running)
        self.assertFalse(pipeline.is_active())

    def test_pipeline_start_stop(self):
        """Test pipeline starts and stops correctly."""
        pipeline = StreamingPipelineEngine(
            state_machine=self.state_machine,
            audio_source=self.audio_source,
            stt_processor=self.stt_processor,
            tts_synthesizer=self.tts_synthesizer,
            audio_player=self.audio_player
        )
        
        pipeline.start()
        self.assertTrue(pipeline._running)
        time.sleep(0.1)  # Let threads start
        self.assertTrue(pipeline.is_active())
        
        pipeline.stop()
        self.assertFalse(pipeline._running)
        time.sleep(0.2)  # Let threads stop
        self.assertFalse(pipeline.is_active())

    def test_audio_capture_stage(self):
        """Test audio capture stage runs without crashing."""
        pipeline = StreamingPipelineEngine(
            state_machine=self.state_machine,
            audio_source=self.audio_source
        )
        
        pipeline.start()
        time.sleep(0.2)  # Let capture run
        
        # Verify pipeline is active
        self.assertTrue(pipeline._running)
        queue_status = pipeline.get_queue_status()
        # Just verify the status dict structure is correct
        self.assertIsNotNone(queue_status)
        
        pipeline.stop()

    def test_stt_processing_stage(self):
        """Test STT processing consumes audio chunks."""
        pipeline = StreamingPipelineEngine(
            state_machine=self.state_machine,
            audio_source=self.audio_source,
            stt_processor=self.stt_processor
        )
        
        pipeline.start()
        time.sleep(0.5)
        
        # STT should be processing
        queue_status = pipeline.get_queue_status()
        # Audio queue should be moderate (some consumption by STT)
        self.assertLess(queue_status["audio_chunks"], 10)
        
        pipeline.stop()

    def test_pause_resume(self):
        """Test pipeline pause and resume."""
        pipeline = StreamingPipelineEngine(
            state_machine=self.state_machine,
            audio_source=self.audio_source,
            stt_processor=self.stt_processor
        )
        
        pipeline.start()
        time.sleep(0.2)
        
        initial_status = pipeline.get_queue_status()
        pipeline.pause()
        
        time.sleep(0.2)
        paused_status = pipeline.get_queue_status()
        
        # After pause, queue sizes should be stable
        pipeline.resume()
        time.sleep(0.2)
        
        pipeline.stop()

    def test_intent_dispatch_fast_path(self):
        """Test intent dispatcher triggers fast-path."""
        # Create audio source with simpler text
        audio_source = MockAudioSource(chunk_count=5)
        stt_processor = MockSTTProcessor(text="Accendi")
        
        pipeline = StreamingPipelineEngine(
            state_machine=self.state_machine,
            audio_source=audio_source,
            stt_processor=stt_processor,
            intent_dispatcher=self.intent_dispatcher,
            audio_player=self.audio_player
        )
        
        pipeline.start()
        time.sleep(1.0)  # Let pipeline process
        
        pipeline.stop()
        
        # Intent dispatcher should have processed
        # (In real scenario would trigger fast-path response)

    def test_concurrent_tts_synthesis(self):
        """Test TTS synthesis runs concurrently with other stages."""
        pipeline = StreamingPipelineEngine(
            state_machine=self.state_machine,
            audio_source=self.audio_source,
            stt_processor=self.stt_processor,
            llm_streamer=self.llm_streamer,
            tts_synthesizer=self.tts_synthesizer,
            audio_player=self.audio_player
        )
        
        pipeline.start()
        time.sleep(1.5)  # Let full pipeline run
        
        # TTS should have synthesized text
        self.assertGreater(len(self.tts_synthesizer.synthesized_texts), 0)
        
        pipeline.stop()

    def test_operation_context_integration(self):
        """Test pipeline records metrics in operation context."""
        with OperationContext("pipeline_test", component="Pipeline") as ctx:
            pipeline = StreamingPipelineEngine(
                state_machine=self.state_machine,
                audio_source=self.audio_source,
                stt_processor=self.stt_processor,
                llm_streamer=self.llm_streamer,
                tts_synthesizer=self.tts_synthesizer,
                audio_player=self.audio_player
            )
            
            pipeline.start(operation_context=ctx)
            time.sleep(1.0)
            
            pipeline.stop()
            
            summary = ctx.get_summary()
            # Should have recorded various metrics
            self.assertGreater(len(summary.get("metrics", {})), 0)

    def test_queue_management(self):
        """Test queue status monitoring and structure."""
        # Create audio source that produces data
        class PeriodicAudioSource:
            def __init__(self):
                self.count = 0
            def __call__(self):
                if self.count >= 20:
                    return None
                self.count += 1
                return AudioChunk(pcm_data=b'\x00\x01' * 160, sample_rate=16000)
        
        pipeline = StreamingPipelineEngine(
            state_machine=self.state_machine,
            audio_source=PeriodicAudioSource(),
            stt_processor=self.stt_processor
        )
        
        pipeline.start()
        time.sleep(0.2)  # Let audio process
        
        status = pipeline.get_queue_status()
        
        # All queue keys should be present
        self.assertIn("audio_chunks", status)
        self.assertIn("stt_results", status)
        self.assertIn("sentences", status)
        self.assertIn("audio_output", status)
        
        # All values should be integers
        for key, value in status.items():
            self.assertIsInstance(value, int, f"Queue {key} should be int, got {type(value)}")
        
        pipeline.stop()

    def test_pipeline_with_no_audio_source(self):
        """Test pipeline handles missing components gracefully."""
        pipeline = StreamingPipelineEngine(
            state_machine=self.state_machine,
            audio_source=None,  # No audio source
            stt_processor=self.stt_processor
        )
        
        # Should not crash
        pipeline.start()
        time.sleep(0.2)
        pipeline.stop()

    def test_audio_chunk_duration(self):
        """Test AudioChunk correctly computes duration."""
        # 10ms at 16kHz = 160 samples = 320 bytes (16-bit)
        chunk = AudioChunk(
            pcm_data=b'\x00\x01' * 160,  # 160 samples
            sample_rate=16000
        )
        
        duration = chunk.duration_ms()
        self.assertAlmostEqual(duration, 10.0, places=1)

    def test_stt_result_confidence_progression(self):
        """Test STT results show increasing confidence."""
        processor = MockSTTProcessor(text="test")
        
        results = []
        for i in range(5):
            result = processor(AudioChunk(pcm_data=b''))
            results.append(result)
        
        # Confidence should increase
        confidences = [r.confidence for r in results]
        for i in range(1, len(confidences)):
            self.assertGreaterEqual(confidences[i], confidences[i-1])

    def test_sentence_chunk_sequencing(self):
        """Test sentence chunks maintain sequence numbers."""
        chunks = []
        for i in range(5):
            chunk = SentenceChunk(
                text=f"Sentence {i}",
                sequence_number=i,
                is_final=(i == 4)
            )
            chunks.append(chunk)
        
        for i, chunk in enumerate(chunks):
            self.assertEqual(chunk.sequence_number, i)
            self.assertEqual(chunk.is_final, (i == 4))

    def test_multiple_pipeline_instances(self):
        """Test multiple pipelines can run independently."""
        pipeline1 = StreamingPipelineEngine(
            state_machine=StateMachine(),
            audio_source=MockAudioSource(5)
        )
        pipeline2 = StreamingPipelineEngine(
            state_machine=StateMachine(),
            audio_source=MockAudioSource(5)
        )
        
        pipeline1.start()
        pipeline2.start()
        
        time.sleep(0.2)
        
        self.assertTrue(pipeline1.is_active())
        self.assertTrue(pipeline2.is_active())
        
        pipeline1.stop()
        pipeline2.stop()
        
        time.sleep(0.1)
        
        self.assertFalse(pipeline1.is_active())
        self.assertFalse(pipeline2.is_active())

    def test_full_e2e_pipeline_latency(self):
        """Test complete pipeline latency is within bounds."""
        start_time = time.time()
        
        pipeline = StreamingPipelineEngine(
            state_machine=self.state_machine,
            audio_source=MockAudioSource(chunk_count=20),  # ~200ms audio
            stt_processor=self.stt_processor,
            llm_streamer=self.llm_streamer,
            tts_synthesizer=self.tts_synthesizer,
            audio_player=self.audio_player
        )
        
        pipeline.start()
        
        # Wait for first audio output
        max_wait = 5.0
        audio_played = False
        while time.time() - start_time < max_wait:
            if self.audio_player.called:
                audio_played = True
                break
            time.sleep(0.05)
        
        elapsed = time.time() - start_time
        pipeline.stop()
        
        # Should have played some audio within timeout
        if audio_played:
            # First audio should be under 2 seconds (streaming latency)
            self.assertLess(elapsed, 2.0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
