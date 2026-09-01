import os
import sys
import glob
import time
import unittest
from unittest.mock import MagicMock

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

    def test_pipeline_controller_fast_path_flow(self):
        """Verifica il flusso del PipelineController con esecuzione Fast-Path."""
        state_machine = StateMachine()
        tts_mock = MagicMock()

        controller = PipelineController(
            state_machine=state_machine,
            tts_engine=tts_mock
        )

        result = controller.process_text_input("alza il volume", speak=False)
        self.assertTrue(result["fast_path"])
        self.assertEqual(result["intent"], "volume_up")
        self.assertEqual(state_machine.state, AssistantState.IDLE)

        result_voice = controller.process_text_input("alza il volume", speak=True)
        self.assertTrue(result_voice["fast_path"])
        self.assertEqual(state_machine.state, AssistantState.SPEAKING)
        tts_mock.assert_called()

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

if __name__ == '__main__':
    unittest.main()
