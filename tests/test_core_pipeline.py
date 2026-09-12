import os
import sys
import glob
import time
import unittest
from unittest.mock import MagicMock, patch

# Aggiunge venv site-packages se presente
venv_sites = glob.glob(os.path.expanduser("~/.local/share/gnome-shell/extensions/voice-assistant@scroker.github.io/daemon/venv/lib/python*/site-packages"))
if venv_sites:
    sys.path.insert(0, venv_sites[0])

# Add src/daemon to import path
daemon_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "daemon"))
if daemon_dir not in sys.path:
    sys.path.insert(0, daemon_dir)

from core.state import StateMachine, AssistantState
from core.pipeline import SentenceAggregator, FastPathDispatcher, PipelineController

class TestCorePipeline(unittest.TestCase):

    def test_sentence_aggregator_streaming(self):
        """Verifica la segmentazione in tempo reale dei token dello stream LLM in frasi complete."""
        emitted_sentences = []
        aggregator = SentenceAggregator(sentence_callback=lambda s: emitted_sentences.append(s))

        # Simula l'arrivo progressivo di token
        aggregator.add_token("Ciao, ")
        aggregator.add_token("come stai? ")
        self.assertEqual(emitted_sentences, ["Ciao, come stai?"])

        aggregator.add_token("Oggi fa ")
        aggregator.add_token("bel tempo! ")
        self.assertEqual(emitted_sentences, ["Ciao, come stai?", "Oggi fa bel tempo!"])

        # Invia un token senza punteggiatura terminale e fai flush
        aggregator.add_token("Speriamo continui così")
        self.assertEqual(len(emitted_sentences), 2)
        flushed = aggregator.flush()
        self.assertEqual(flushed, "Speriamo continui così")
        self.assertEqual(len(emitted_sentences), 3)

    def test_fast_path_dispatcher_intents(self):
        """Verifica che gli intenti Fast-Path vengano rilevati ed eseguiti in <10ms."""
        dispatcher = FastPathDispatcher()

        start_time = time.time()
        matched, intent, params, resp = dispatcher.dispatch("imposta volume al 75%")
        duration_ms = (time.time() - start_time) * 1000

        self.assertTrue(matched)
        self.assertEqual(intent, "set_volume")
        self.assertEqual(params.get("volume"), 75)
        self.assertIn("75%", resp)
        self.assertLess(duration_ms, 10.0, "Latenza Fast-Path superiore a 10ms!")

        # Test intent tema scuro
        matched, intent, params, resp = dispatcher.dispatch("attiva la modalità scura")
        self.assertTrue(matched)
        self.assertEqual(intent, "set_theme_dark")

        # Test intent non Fast-Path (passaggio all'LLM)
        matched, intent, params, resp = dispatcher.dispatch("Spiegami la teoria della relatività generale")
        self.assertFalse(matched)

    def test_fast_path_dispatcher_passes_text_to_intent_handler(self):
        calls = []

        def handler(intent, params, text):
            calls.append((intent, params, text))
            return True, "Volume aggiornato via MCP"

        dispatcher = FastPathDispatcher(intent_handler=handler)

        matched, intent, params, response = dispatcher.dispatch("Alza il volume")

        self.assertTrue(matched)
        self.assertEqual(intent, "volume_up")
        self.assertEqual(response, "Volume aggiornato via MCP")
        self.assertEqual(calls[0][0], "volume_up")
        self.assertEqual(calls[0][2], "alza il volume")

    def test_fast_path_dispatcher_enabled_flag(self):
        """Verifica che il flag enabled su FastPathDispatcher disabiliti il dispatch."""
        dispatcher_disabled = FastPathDispatcher(enabled=False)
        matched, intent, params, resp = dispatcher_disabled.dispatch("alza il volume")
        self.assertFalse(matched)
        self.assertIsNone(intent)

        dispatcher_disabled.enabled = True
        matched, intent, params, resp = dispatcher_disabled.dispatch("alza il volume")
        self.assertTrue(matched)
        self.assertEqual(intent, "volume_up")

    def test_pipeline_controller_fast_path_flow(self):
        """Verifica il flusso del PipelineController con esecuzione Fast-Path quando abilitato."""
        state_machine = StateMachine()
        tts_mock = MagicMock()

        controller = PipelineController(
            state_machine=state_machine,
            tts_engine=tts_mock,
            fast_path_enabled=True,
        )

        result = controller.process_text_input("alza il volume", speak=False)
        self.assertTrue(result["fast_path"])
        self.assertEqual(result["intent"], "volume_up")
        self.assertEqual(state_machine.state, AssistantState.IDLE)

        result_voice = controller.process_text_input("alza il volume", speak=True)
        self.assertTrue(result_voice["fast_path"])
        self.assertEqual(state_machine.state, AssistantState.SPEAKING)
        tts_mock.assert_called()

    def test_pipeline_controller_fast_path_disabled_by_default(self):
        """Verifica che di default il Fast-Path sia disabilitato nel PipelineController."""
        state_machine = StateMachine()
        tts_mock = MagicMock()

        controller = PipelineController(
            state_machine=state_machine,
            tts_engine=tts_mock,
        )
        self.assertFalse(controller.fast_path_enabled)
        self.assertFalse(controller.fast_path.enabled)

        # Non deve catturare l'intento nel fast-path
        result = controller.process_text_input("alza il volume", speak=False)
        self.assertFalse(result["fast_path"])

        # Abilitazione dinamica a runtime
        controller.fast_path_enabled = True
        self.assertTrue(controller.fast_path.enabled)
        result_enabled = controller.process_text_input("alza il volume", speak=False)
        self.assertTrue(result_enabled["fast_path"])

    def test_pipeline_controller_direct_to_smart_path(self):
        """Verifica che le richieste non gestite da Fast-Path procedano direttamente verso Smart-Path."""
        state_machine = StateMachine()

        controller = PipelineController(
            state_machine=state_machine,
            tts_engine=MagicMock(),
            llm_streamer=lambda prompt: iter(["irrilevante"]),
            mcp_manager=MagicMock(),
        )

        with patch.object(controller.smart_path, "execute_smart_path", return_value=(True, "Risposta Smart Path", None)) as mock_smart:
            result = controller.process_text_input("chi era leonardo da vinci?", speak=False)
            mock_smart.assert_called_once()
            self.assertEqual(result.get("response"), "Risposta Smart Path")
            self.assertFalse(result.get("fast_path"))

    def test_pipeline_controller_llm_streaming_flow(self):
        """Verifica il flusso di streaming LLM e transizioni di stato nel PipelineController."""
        state_machine = StateMachine()
        sentences_spoken = []

        def dummy_llm_stream(prompt):
            tokens = ["Questa è ", "una risposta ", "di prova. ", "Spero sia ", "chiara."]
            for t in tokens:
                yield t

        controller = PipelineController(
            state_machine=state_machine,
            llm_streamer=dummy_llm_stream,
            tts_engine=lambda s: sentences_spoken.append(s)
        )

        result = controller.process_text_input("Dimmi qualcosa")
        self.assertFalse(result["fast_path"])
        self.assertEqual(result["response"], "Questa è una risposta di prova. Spero sia chiara.")
        self.assertEqual(sentences_spoken, ["Questa è una risposta di prova.", "Spero sia chiara."])
        self.assertEqual(state_machine.state, AssistantState.IDLE)

    def test_pipeline_controller_smart_path_streams_to_gui_and_tts(self):
        state_machine = StateMachine()
        tokens = []
        spoken = []

        def dummy_llm_stream(prompt):
            yield "Uso un tool. "
            yield '{"tool": "set_volume", "args": {"direction": "up", "volume": 10.0}}'

        mcp_manager = MagicMock()
        mcp_manager.execute_tool.return_value = "Volume alzato."

        controller = PipelineController(
            state_machine=state_machine,
            llm_streamer=dummy_llm_stream,
            tts_engine=spoken.append,
            mcp_manager=mcp_manager,
        )
        controller.on_token_callback = tokens.append

        result = controller.process_text_input("gestisci questo comando complesso", speak=True)

        visible = "".join(tokens)
        self.assertTrue(result["smart_path"])
        self.assertIn("Uso un tool.", result["response"])
        self.assertIn("Volume alzato.", visible)
        self.assertNotIn('"tool"', visible)
        self.assertEqual(spoken, ["Uso un tool.", "Volume alzato."])
        mcp_manager.execute_tool.assert_called_once_with(
            "set_volume", {"direction": "up", "volume": 10.0}
        )

if __name__ == '__main__':
    unittest.main()
