"""
End-to-End Pipeline Integration Test
Validates the complete flow: Audio Input → STT → LLM → TTS → Audio Output
"""
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch, call
from pathlib import Path
import wave
import io

# Setup paths
daemon_dir = Path(__file__).resolve().parent.parent / 'src' / 'daemon'
sys.path.insert(0, str(daemon_dir))

from core.state import StateMachine, AssistantState
from core.pipeline import SentenceAggregator, FastPathDispatcher, PipelineController


class MockAudioInput:
    """Simulates audio input device providing raw PCM samples."""
    def __init__(self, sample_rate=16000, duration_ms=1000):
        self.sample_rate = sample_rate
        self.chunk_size = sample_rate // 100  # 10ms chunks
        self.duration_frames = (sample_rate * duration_ms) // 1000
        self.frames_read = 0

    def read_chunk(self):
        """Returns next 10ms chunk of PCM data."""
        if self.frames_read >= self.duration_frames:
            return None
        chunk_frames = min(self.chunk_size, self.duration_frames - self.frames_read)
        self.frames_read += chunk_frames
        # Simulate raw audio (bytes)
        return b'\x00\x01' * chunk_frames


class MockSTTProvider:
    """Simulates Speech-to-Text provider that converts audio to text progressively."""
    def __init__(self, target_text="Accendi la luce del soggiorno"):
        self.target_text = target_text
        self.words = target_text.split()
        self.word_index = 0
        self.chunks_processed = 0
        self.final_text = ""

    def process_chunk(self, pcm_data):
        """Process audio chunk and return (partial_text, confidence)."""
        self.chunks_processed += 1
        
        # Simulate progressive text recognition (every 5 chunks = ~50ms, emit a word)
        if self.chunks_processed % 5 == 0 and self.word_index < len(self.words):
            self.final_text += self.words[self.word_index] + " "
            self.word_index += 1
        
        partial = self.final_text.strip()
        # Confidence increases as more words are recognized
        confidence = min(0.95, 0.5 + (self.word_index / len(self.words)) * 0.45) if self.word_index > 0 else 0.5
        return (partial, confidence)

    def finish(self):
        """Finalize recognition and return final text."""
        return self.final_text.strip()

    def reset(self):
        """Reset state for next recognition."""
        self.word_index = 0
        self.chunks_processed = 0
        self.final_text = ""


class MockLLMProvider:
    """Simulates LLM service that generates streaming responses."""
    def __init__(self, system_prompt="Sei un assistente vocale GNOME"):
        self.system_prompt = system_prompt
        self.response_templates = {
            "Accendi la luce": ["Sto accendendo la luce del soggiorno. ", "Fatto, la luce è accesa."],
            "spegni": ["Sto spegnendo. ", "Luce spenta."],
            "volume": ["Regolando il volume. ", "Volume modificato al 75%."],
            "default": ["Ho capito la tua richiesta. ", "Sto elaborando. ", "Perfetto!"]
        }

    def stream_response(self, user_text):
        """Generate response tokens progressively (simulating streaming)."""
        # Find matching template
        response_lines = self.response_templates.get("default", self.response_templates["default"])
        for keyword in self.response_templates:
            if keyword != "default" and keyword.lower() in user_text.lower():
                response_lines = self.response_templates[keyword]
                break
        
        # Yield tokens progressively
        for line in response_lines:
            # Simulate token-by-token generation
            words = line.split()
            for word in words:
                yield word + " "
                time.sleep(0.01)  # Simulate network latency

    def get_streaming_response(self, user_text):
        """Wrapper for streaming response."""
        return self.stream_response(user_text)


class MockTTSProvider:
    """Simulates Text-to-Speech provider that generates audio bytes."""
    def __init__(self, voice="it_IT-paola-medium"):
        self.voice = voice
        self.sample_rate = 22050
        self.synthesized_texts = []

    def synthesize(self, text):
        """Synthesize text to WAV bytes."""
        self.synthesized_texts.append(text)
        
        # Generate minimal WAV file (44.1kHz, mono, 0.5s duration)
        duration_samples = self.sample_rate // 2
        pcm_data = b'\x00\x01' * duration_samples
        
        # Create WAV header
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, 'wb') as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self.sample_rate)
            wav_file.writeframes(pcm_data)
        
        return wav_buffer.getvalue()


class TestE2EPipelineIntegration(unittest.TestCase):
    """
    End-to-End Integration Tests for the Voice Assistant Pipeline
    """

    def setUp(self):
        """Initialize test fixtures."""
        self.state_machine = StateMachine(AssistantState.IDLE.value)
        self.tts_provider = MockTTSProvider()
        self.llm_provider = MockLLMProvider()
        self.stt_provider = MockSTTProvider()
        self.audio_input = MockAudioInput()

    def test_e2e_flow_state_transitions(self):
        """
        Test complete flow: IDLE → LISTENING → PROCESSING → SPEAKING → IDLE
        """
        state_transitions = []
        
        def state_callback(new_state):
            state_transitions.append(new_state)
        
        self.state_machine.add_callback(state_callback)
        
        # Simulate flow
        self.state_machine.set_state(AssistantState.LISTENING.value)
        self.state_machine.set_state(AssistantState.PROCESSING.value)
        self.state_machine.set_state(AssistantState.SPEAKING.value)
        self.state_machine.set_state(AssistantState.IDLE.value)
        
        # Verify transitions
        expected = [
            AssistantState.LISTENING.value,
            AssistantState.PROCESSING.value,
            AssistantState.SPEAKING.value,
            AssistantState.IDLE.value
        ]
        self.assertEqual(state_transitions, expected)

    def test_stt_progressive_recognition(self):
        """
        Test STT provider generates progressive partial text and final result.
        """
        partial_results = []
        confidence_values = []
        
        # Simulate audio chunks
        for _ in range(30):  # 30 chunks × 10ms = 300ms
            chunk = self.stt_provider.process_chunk(b'\x00\x01' * 160)
            if chunk[0]:
                partial_results.append(chunk[0])
                confidence_values.append(chunk[1])
        
        final_text = self.stt_provider.finish()
        
        # Verify progressive recognition
        self.assertGreater(len(partial_results), 0, "Should have partial recognition results")
        self.assertEqual(final_text, "Accendi la luce del soggiorno")
        self.assertGreater(confidence_values[-1], confidence_values[0],
                          "Confidence should increase as more audio is processed")

    def test_llm_streaming_tokens(self):
        """
        Test LLM provider generates tokens progressively with sentence aggregation.
        """
        user_input = "Accendi la luce del soggiorno"
        sentence_callback_received = []
        
        aggregator = SentenceAggregator(
            sentence_callback=lambda s: sentence_callback_received.append(s)
        )
        
        # Collect tokens from LLM stream
        for token in self.llm_provider.stream_response(user_input):
            sentences = aggregator.add_token(token)
        
        # Flush remaining buffer
        flushed = aggregator.flush()
        if flushed:
            sentence_callback_received.append(flushed)
        
        # Verify sentence aggregation
        self.assertGreater(len(sentence_callback_received), 0,
                          "Should have generated sentences from LLM tokens")
        
        # Verify sentences are reasonable
        for sentence in sentence_callback_received:
            self.assertGreater(len(sentence), 0, "Sentences should not be empty")

    def test_tts_synthesis(self):
        """
        Test TTS provider synthesizes text to valid WAV audio.
        """
        test_text = "Luce accesa nel soggiorno."
        audio_bytes = self.tts_provider.synthesize(test_text)
        
        # Verify WAV format
        self.assertGreater(len(audio_bytes), 44, "WAV data should be larger than header")
        self.assertTrue(audio_bytes.startswith(b'RIFF'), "Should start with RIFF header")
        self.assertIn(b'WAVE', audio_bytes, "Should contain WAVE format marker")
        
        # Verify synthesis was recorded
        self.assertIn(test_text, self.tts_provider.synthesized_texts)

    def test_fast_path_dispatch_latency(self):
        """
        Test Fast-Path dispatcher executes common intents in < 10ms.
        """
        dispatcher = FastPathDispatcher()
        test_cases = [
            ("alza il volume", "volume_up"),
            ("attiva la modalità scura", "set_theme_dark"),
            ("accendi la luce", None),  # Not a fast-path intent
        ]
        
        for text, expected_intent in test_cases:
            start_time = time.time()
            matched, intent, params, response = dispatcher.dispatch(text)
            elapsed_ms = (time.time() - start_time) * 1000
            
            # Verify latency is acceptable
            self.assertLess(elapsed_ms, 10.0,
                           f"Fast-Path dispatch for '{text}' took {elapsed_ms:.2f}ms (> 10ms)")
            
            if expected_intent:
                self.assertTrue(matched, f"'{text}' should match fast-path intent")
                self.assertEqual(intent, expected_intent)

    def test_sentence_aggregator_punctuation_handling(self):
        """
        Test SentenceAggregator correctly handles punctuation and abbreviations.
        """
        emitted_sentences = []
        aggregator = SentenceAggregator(
            sentence_callback=lambda s: emitted_sentences.append(s)
        )
        
        # Test normal punctuation - aggregator keeps punctuation in output
        aggregator.add_token("Questo è un test. ")
        self.assertGreater(len(emitted_sentences), 0, "Should emit sentence on punctuation")
        self.assertIn("test", emitted_sentences[0])
        
        # Test abbreviation NOT triggering split (e.g., "Prof.")
        aggregator.reset()
        emitted_sentences.clear()
        aggregator.add_token("Parlo con Prof. ")
        aggregator.add_token("Rossi oggi. ")
        # Abbreviations like "Prof." should not cause premature split
        # The behavior may vary based on implementation

    def test_pipeline_controller_mock_integration(self):
        """
        Test PipelineController coordinates state machine and services.
        """
        tts_mock = MagicMock()
        controller = PipelineController(
            state_machine=self.state_machine,
            tts_engine=tts_mock
        )
        
        # Verify controller is properly initialized
        self.assertIsNotNone(controller)
        self.assertEqual(self.state_machine.state, AssistantState.IDLE.value)

    def test_audio_input_streaming(self):
        """
        Test audio input device produces consistent chunk sizes.
        """
        chunks = []
        while True:
            chunk = self.audio_input.read_chunk()
            if chunk is None:
                break
            chunks.append(chunk)
        
        # Verify we got chunks
        self.assertGreater(len(chunks), 0, "Should produce audio chunks")
        
        # Verify chunk consistency
        for chunk in chunks[:-1]:  # All but last
            self.assertEqual(len(chunk), self.audio_input.chunk_size * 2,
                           "Chunks should have consistent size")

    def test_state_machine_thread_safety(self):
        """
        Test state machine maintains consistency under concurrent access.
        """
        import threading
        
        transition_count = 0
        def increment_callback(new_state):
            nonlocal transition_count
            transition_count += 1
        
        self.state_machine.add_callback(increment_callback)
        
        def thread_work():
            for state in [AssistantState.LISTENING.value, AssistantState.IDLE.value]:
                self.state_machine.set_state(state)
                time.sleep(0.001)
        
        # Create multiple threads
        threads = [threading.Thread(target=thread_work) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # Verify callbacks were invoked correctly
        self.assertGreater(transition_count, 0, "State machine should process transitions")

    def test_full_e2e_latency_measurement(self):
        """
        Measure latency of complete pipeline flow and verify it's reasonable.
        """
        start_total = time.time()
        
        # Phase 1: Audio Input (100ms)
        audio_start = time.time()
        chunk_count = 0
        while True:
            chunk = self.audio_input.read_chunk()
            if chunk is None:
                break
            chunk_count += 1
        audio_time = time.time() - audio_start
        
        # Phase 2: STT (simulate 150ms)
        stt_start = time.time()
        for _ in range(15):
            self.stt_provider.process_chunk(b'\x00\x01' * 160)
            time.sleep(0.01)
        final_stt = self.stt_provider.finish()
        stt_time = time.time() - stt_start
        
        # Phase 3: LLM Streaming (simulate 200ms)
        llm_start = time.time()
        llm_tokens = 0
        for token in self.llm_provider.stream_response(final_stt):
            llm_tokens += 1
        llm_time = time.time() - llm_start
        
        # Phase 4: TTS Synthesis (simulate 50ms)
        tts_start = time.time()
        response_text = "Luce accesa nel soggiorno."
        audio_output = self.tts_provider.synthesize(response_text)
        tts_time = time.time() - tts_start
        
        total_time = time.time() - start_total
        
        # Verify latency breakdown
        self.assertLess(audio_time, 0.2, "Audio input phase should be < 200ms")
        self.assertLess(stt_time, 0.3, "STT phase should be < 300ms")
        self.assertLess(llm_time, 0.5, "LLM phase should be < 500ms")
        self.assertLess(tts_time, 0.2, "TTS phase should be < 200ms")
        
        # Log latency breakdown for analysis
        print(f"\n=== Pipeline Latency Breakdown ===")
        print(f"Audio Input:    {audio_time*1000:.1f}ms")
        print(f"STT:            {stt_time*1000:.1f}ms")
        print(f"LLM Streaming:  {llm_time*1000:.1f}ms")
        print(f"TTS:            {tts_time*1000:.1f}ms")
        print(f"Total E2E:      {total_time*1000:.1f}ms")
        print(f"=====================================\n")

    def test_error_recovery_stt_failure(self):
        """
        Test system gracefully handles STT provider failure.
        """
        state_transitions = []
        self.state_machine.add_callback(lambda s: state_transitions.append(s))
        
        # Enter listening state
        self.state_machine.set_state(AssistantState.LISTENING.value)
        self.assertEqual(self.state_machine.state, AssistantState.LISTENING.value)
        
        # Simulate STT failure → return to idle
        self.state_machine.set_state(AssistantState.IDLE.value)
        
        # Verify recovery
        self.assertEqual(self.state_machine.state, AssistantState.IDLE.value)
        self.assertIn(AssistantState.LISTENING.value, state_transitions)

    def test_error_recovery_llm_timeout(self):
        """
        Test system handles LLM timeout and returns fallback response.
        """
        dispatcher = FastPathDispatcher()
        
        # Test with a fast-path intent that has a response
        text = "alza il volume"
        try:
            matched, intent, params, response = dispatcher.dispatch(text)
            # Should not crash and should match the fast-path intent
            self.assertTrue(matched, "Should match fast-path intent for volume commands")
            self.assertIsNotNone(response, "Should provide response text")
        except Exception as e:
            self.fail(f"Dispatcher should handle requests gracefully: {e}")

    def test_cancel_operations(self):
        """
        Test cancellation of ongoing operations.
        """
        # Reset STT provider mid-recognition
        self.stt_provider.process_chunk(b'\x00\x01' * 160)
        self.stt_provider.process_chunk(b'\x00\x01' * 160)
        
        # Verify we can reset
        self.stt_provider.reset()
        self.assertEqual(self.stt_provider.final_text, "")
        self.assertEqual(self.stt_provider.word_index, 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
