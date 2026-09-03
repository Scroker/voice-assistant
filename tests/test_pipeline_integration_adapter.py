import queue
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock


daemon_dir = Path(__file__).resolve().parent.parent / "src" / "daemon"
sys.path.insert(0, str(daemon_dir))

from core.pipeline_integration import StreamingPipelineController
from core.state import StateMachine


class SpeakOnlyTTSManager:
    def __init__(self):
        self.spoken_texts = []

    def speak(self, text):
        self.spoken_texts.append(text)
        return True


class DummyOwner:
    def __init__(self):
        self.q = queue.Queue()
        self.provider = MagicMock()
        self.pipeline_controller = MagicMock()
        self.pipeline_controller.fast_path.dispatch.return_value = (
            True,
            "volume_up",
            {"delta": 10},
            "Volume alzato",
        )
        self._handle_fast_path_intent = MagicMock(return_value=(True, "Volume alzato"))
        self.llm_service = MagicMock()
        self.llm_service.stream_tokens.return_value = iter(["Ciao", "."])
        self.tts_manager = SpeakOnlyTTSManager()
        self.audio_player = MagicMock()
        self._on_llm_token = MagicMock()
        self._on_playback_finished = MagicMock()


class TestStreamingPipelineAdapter(unittest.TestCase):
    def test_audio_source_uses_owner_queue(self):
        owner = DummyOwner()
        owner.q.put(b"pcm")
        controller = StreamingPipelineController(StateMachine(), owner=owner)

        source = controller._create_audio_source()
        chunk = source()

        self.assertEqual(chunk.pcm_data, b"pcm")
        self.assertIsNone(source())

    def test_stt_processor_accepts_provider_tuple_contract(self):
        owner = DummyOwner()
        owner.provider.process_chunk.return_value = ("testo finale", "testo parziale")
        controller = StreamingPipelineController(StateMachine(), owner=owner)

        processor = controller._create_stt_processor()
        result = processor(MagicMock(pcm_data=b"pcm"))

        self.assertEqual(result.final_text, "testo finale")
        self.assertEqual(result.partial_text, "testo parziale")
        self.assertTrue(result.is_final)

    def test_intent_dispatcher_uses_runtime_pipeline_fast_path(self):
        owner = DummyOwner()
        controller = StreamingPipelineController(StateMachine(), owner=owner)

        dispatcher = controller._create_intent_dispatcher()
        result = dispatcher("alza il volume")

        self.assertTrue(result["matched"])
        self.assertEqual(result["intent"], "volume_up")
        self.assertEqual(result["response"], "Volume alzato")
        owner._handle_fast_path_intent.assert_called_once_with("volume_up", {"delta": 10})

    def test_llm_streamer_uses_stream_tokens_and_forwards_tokens(self):
        owner = DummyOwner()
        controller = StreamingPipelineController(StateMachine(), owner=owner)

        streamer = controller._create_llm_streamer()
        tokens = list(streamer("ciao"))

        self.assertEqual(tokens, ["Ciao", "."])
        owner.llm_service.stream_tokens.assert_called_once_with("ciao")
        owner._on_llm_token.assert_any_call("Ciao")

    def test_tts_synthesizer_accepts_tts_manager_speak(self):
        owner = DummyOwner()
        controller = StreamingPipelineController(StateMachine(), owner=owner)

        synthesizer = controller._create_tts_synthesizer()
        audio_output = synthesizer("ciao")

        self.assertEqual(audio_output.wav_data, b"")
        self.assertGreater(audio_output.duration_ms, 0)
        self.assertEqual(owner.tts_manager.spoken_texts, ["ciao"])

    def test_audio_player_uses_existing_audio_player_api(self):
        owner = DummyOwner()
        controller = StreamingPipelineController(StateMachine(), owner=owner)
        audio_output = MagicMock(wav_data=b"wav", sample_rate=22050, duration_ms=0)

        player = controller._create_audio_player()
        player(audio_output)

        owner.audio_player.play_wav_bytes.assert_called_once_with(b"wav")


if __name__ == "__main__":
    unittest.main()
