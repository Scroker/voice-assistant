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

    @staticmethod
    def _build_pipeline_with_temp_vector_store(tmpdir, **kwargs):
        """Costruisce un PipelineController con il VectorStore dello Smart-Path
        puntato su un DB temporaneo, per evitare che i test scrivano in
        ~/.local/share/voice-assistant/rag_store.db (VectorStore è sempre creato
        da SmartPathController.__init__, anche per il fallback path)."""
        from services.rag_store import VectorStore as _RealVectorStore
        db_path = os.path.join(tmpdir, "rag_store_test.db")
        with patch(
            "core.smart_path_controller.VectorStore",
            lambda max_documents=1000: _RealVectorStore(max_documents=max_documents, db_path=db_path),
        ):
            return PipelineController(**kwargs)

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

    def test_fast_path_mail_intents(self):
        """Verifica il riconoscimento rapido di apertura e composizione email."""
        dispatcher = FastPathDispatcher()

        # 1. Apertura posta
        matched, intent, params, resp = dispatcher.dispatch("apri la posta")
        self.assertTrue(matched)
        self.assertEqual(intent, "launch_app")
        self.assertEqual(params.get("app"), "mail")

        matched, intent, params, resp = dispatcher.dispatch("controlla le mail")
        self.assertTrue(matched)
        self.assertEqual(intent, "launch_app")

        # 2. Composizione email generica
        matched, intent, params, resp = dispatcher.dispatch("scrivi una mail")
        self.assertTrue(matched)
        self.assertEqual(intent, "compose_mail")

        # 3. Composizione email con destinatario e testo
        matched, intent, params, resp = dispatcher.dispatch(
            "scrivi una mail a mario@example.com con testo ci vediamo alle 15"
        )
        self.assertTrue(matched)
        self.assertEqual(intent, "compose_mail")
        self.assertEqual(params.get("to"), "mario@example.com")
        self.assertEqual(params.get("text"), "ci vediamo alle 15")

    def test_pipeline_controller_forwards_extra_context(self):
        """Verifica che extra_context (Speaker ID) venga inoltrato a SMART PATH e fallback LLM."""
        state_machine = StateMachine()
        tts_mock = MagicMock()
        mcp_mock = MagicMock()
        smart_path_mock = MagicMock()
        smart_path_mock.execute_smart_path.return_value = (True, "Risposta SMART", None)

        pipeline = PipelineController(
            state_machine=state_machine,
            tts_engine=tts_mock,
            mcp_manager=mcp_mock,
            fast_path_enabled=False,
        )
        pipeline.smart_path = smart_path_mock

        res = pipeline.process_text_input(
            "chi sono io?",
            speak=False,
            extra_context="Parlante identificato: Giorgio.",
        )

        self.assertTrue(res.get("smart_path"))
        smart_path_mock.execute_smart_path.assert_called_once()
        _, kwargs = smart_path_mock.execute_smart_path.call_args
        self.assertEqual(kwargs.get("extra_context"), "Parlante identificato: Giorgio.")

    def test_deep_dive_explicit_request_long_response(self):
        """'spiegami nel dettaglio X' con risposta di 900 caratteri: prima frase letta, aperto chat TTS, nuova chat, segnale."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            from services.conversation_contexts import ConversationContextManager
            cm = ConversationContextManager(base_dir=tmpdir)
            state_machine = StateMachine()
            spoken = []
            owner_mock = MagicMock()

            s1 = "La fotosintesi clorofilliana è il processo biochimico primario della vita sulla Terra. "
            s2 = "Durante questo processo le piante convertono anidride carbonica e acqua in glucosio e ossigeno sfruttando l'energia solare. " * 5
            s3 = "Questa trasformazione avviene all'interno dei cloroplasti mediante complesse reazioni fotolitiche. " * 3

            def dummy_stream(p, **kw):
                yield s1
                yield s2
                yield s3

            pipeline = PipelineController(
                state_machine=state_machine,
                tts_engine=spoken.append,
                llm_streamer=dummy_stream,
                context_manager=cm,
                owner=owner_mock,
            )
            pipeline._notify_deep_dive = MagicMock()

            res = pipeline.process_text_input(
                "spiegami nel dettaglio la fotosintesi",
                speak=True,
                context_id="voice",
            )

            self.assertGreater(len(res["response"]), 400)
            # Prima frase letta e messaggio di apertura chat letto
            self.assertEqual(spoken[0], s1.strip())
            self.assertIn("aperto i dettagli", spoken[-1])
            self.assertIn("chat", spoken[-1])
            # Segnale D-Bus ConversationCreated emesso
            owner_mock.ConversationCreated.assert_called_once()
            call_args = owner_mock.ConversationCreated.call_args[0]
            new_chat_id = call_args[0]
            self.assertEqual(call_args[1], "deep_dive")
            # Nuova chat contiene la domanda e la risposta completa
            new_ctx = cm.get(new_chat_id)
            self.assertIsNotNone(new_ctx)
            msgs = new_ctx.messages
            self.assertTrue(any("spiegami nel dettaglio" in m.get("content", "") for m in msgs))
            self.assertTrue(any("fotosintesi clorofilliana" in m.get("content", "") for m in msgs))
            pipeline._notify_deep_dive.assert_called_once_with(new_chat_id)

    def test_deep_dive_without_explicit_pattern(self):
        """Stessa risposta lunga ma senza pattern esplicito: nessuna chat creata, TTS normale di tutte le frasi."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            from services.conversation_contexts import ConversationContextManager
            cm = ConversationContextManager(base_dir=tmpdir)
            state_machine = StateMachine()
            spoken = []
            owner_mock = MagicMock()

            s1 = "La fotosintesi è importante. "
            s2 = "Dettagli lunghi che superano i quattrocento caratteri di soglia impostati per il test. " * 6

            def dummy_stream(p, **kw):
                yield s1
                yield s2

            pipeline = PipelineController(
                state_machine=state_machine,
                tts_engine=spoken.append,
                llm_streamer=dummy_stream,
                context_manager=cm,
                owner=owner_mock,
            )
            pipeline._notify_deep_dive = MagicMock()

            res = pipeline.process_text_input(
                "parlami della fotosintesi",
                speak=True,
                context_id="voice",
            )

            self.assertGreater(len(res["response"]), 400)
            owner_mock.ConversationCreated.assert_not_called()
            self.assertEqual(len(cm.list_conversations()), 0)
            self.assertIn(s1.strip(), spoken)

    def test_deep_dive_explicit_request_short_response(self):
        """Richiesta esplicita con risposta corta (<400 caratteri): nessuna chat e tutte le frasi lette."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            from services.conversation_contexts import ConversationContextManager
            cm = ConversationContextManager(base_dir=tmpdir)
            state_machine = StateMachine()
            spoken = []
            owner_mock = MagicMock()

            s1 = "Ecco la spiegazione. "
            s2 = "La fotosintesi produce ossigeno ed è vitale."

            def dummy_stream(p, **kw):
                yield s1
                yield s2

            pipeline = PipelineController(
                state_machine=state_machine,
                tts_engine=spoken.append,
                llm_streamer=dummy_stream,
                context_manager=cm,
                owner=owner_mock,
            )
            pipeline._notify_deep_dive = MagicMock()

            res = pipeline.process_text_input(
                "spiegami nel dettaglio la fotosintesi",
                speak=True,
                context_id="voice",
            )

            self.assertLess(len(res["response"]), 400)
            owner_mock.ConversationCreated.assert_not_called()
            self.assertEqual(len(cm.list_conversations()), 0)
            self.assertEqual(spoken, [s1.strip(), s2.strip()])

    def test_deep_dive_gui_request_speak_false(self):
        """Richiesta da chat GUI (speak=False): nessuna chat creata."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            from services.conversation_contexts import ConversationContextManager
            cm = ConversationContextManager(base_dir=tmpdir)
            state_machine = StateMachine()
            spoken = []
            owner_mock = MagicMock()

            s1 = "Ecco la spiegazione dettagliata. " * 15

            pipeline = PipelineController(
                state_machine=state_machine,
                tts_engine=spoken.append,
                llm_streamer=lambda p, **kw: iter([s1]),
                context_manager=cm,
                owner=owner_mock,
            )
            pipeline._notify_deep_dive = MagicMock()

            res = pipeline.process_text_input(
                "spiegami nel dettaglio la fotosintesi",
                speak=False,
                context_id="gui-chat-test",
            )

            owner_mock.ConversationCreated.assert_not_called()
            self.assertEqual(len(spoken), 0)

    def test_cancel_pipeline_suppresses_tts_and_chat_after_stop_fallback_path(self):
        """Bug: dopo un cancel_pipeline() (stop) a metà streaming, il fallback LLM
        (mcp_manager=None) completava comunque il deep dive: leggeva le frasi
        accumulate via TTS e/o apriva una nuova chat con il messaggio 'ti ho aperto
        i dettagli', anche se la richiesta era stata interrotta."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            from services.conversation_contexts import ConversationContextManager
            cm = ConversationContextManager(base_dir=tmpdir)
            state_machine = StateMachine()
            spoken = []
            owner_mock = MagicMock()

            s1 = "Frase iniziale pronunciata prima dello stop. "
            s2 = ("Questa frase lunga non deve mai essere letta né aprire una chat "
                  "perché lo stop arriva subito dopo. ") * 5

            def dummy_stream(p, **kw):
                yield s1
                yield s2
                pipeline.cancel_pipeline()
                yield "Frase mai raggiunta perché lo streaming è già stato interrotto. "

            pipeline = self._build_pipeline_with_temp_vector_store(
                tmpdir,
                state_machine=state_machine,
                tts_engine=spoken.append,
                llm_streamer=dummy_stream,
                context_manager=cm,
                owner=owner_mock,
            )
            pipeline._notify_deep_dive = MagicMock()

            self.assertGreaterEqual(len(s1) + len(s2), 400)

            pipeline.process_text_input(
                "spiegami nel dettaglio la fotosintesi",
                speak=True,
                context_id="voice",
            )

            # Solo la prima frase (già pronunciata prima dello stop) può essere stata
            # letta; nulla deve essere stato detto o creato dopo la cancellazione.
            self.assertEqual(spoken, [s1.strip()])
            owner_mock.ConversationCreated.assert_not_called()
            self.assertEqual(len(cm.list_conversations()), 0)
            pipeline._notify_deep_dive.assert_not_called()

    def test_cancel_pipeline_suppresses_tts_after_stop_smart_path(self):
        """Bug: SmartPathController._consume_llm_stream non controlla alcun flag di
        cancellazione e continua a consumare token/frasi anche dopo uno stop; la
        difesa deve stare nel sentence_callback (_on_sentence_ready) lato pipeline."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            from services.conversation_contexts import ConversationContextManager
            cm = ConversationContextManager(base_dir=tmpdir)
            state_machine = StateMachine()
            spoken = []
            owner_mock = MagicMock()
            mcp_manager = MagicMock()

            s1 = "Prima frase pronunciata prima dello stop. "
            s2 = "Questa frase non deve mai raggiungere il TTS perché arriva dopo lo stop. "
            s3 = "Nemmeno questa, se la cancellazione è rispettata correttamente. "

            def dummy_llm_stream(prompt, **kw):
                yield s1
                pipeline.cancel_pipeline()
                yield s2
                yield s3

            pipeline = self._build_pipeline_with_temp_vector_store(
                tmpdir,
                state_machine=state_machine,
                tts_engine=spoken.append,
                llm_streamer=dummy_llm_stream,
                mcp_manager=mcp_manager,
                context_manager=cm,
                owner=owner_mock,
            )

            pipeline.process_text_input("che tempo fa oggi?", speak=True, context_id="voice")

            self.assertEqual(spoken, [s1.strip()])
            owner_mock.ConversationCreated.assert_not_called()
            self.assertEqual(len(cm.list_conversations()), 0)

    def test_deep_dive_chat_contains_question_and_answer_when_memory_disabled(self):
        """Bug: con memoria conversazione disabilitata la voice ctx resta vuota, quindi
        la chat di approfondimento veniva creata senza domanda né risposta."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            from services.conversation_contexts import ConversationContextManager
            cm = ConversationContextManager(base_dir=tmpdir)
            state_machine = StateMachine()
            spoken = []
            owner_mock = MagicMock()

            s1 = "La fotosintesi clorofilliana è il processo biochimico primario della vita sulla Terra. "
            s2 = "Durante questo processo le piante convertono anidride carbonica e acqua in glucosio e ossigeno sfruttando l'energia solare. " * 5

            def dummy_stream(p, **kw):
                yield s1
                yield s2

            pipeline = self._build_pipeline_with_temp_vector_store(
                tmpdir,
                state_machine=state_machine,
                tts_engine=spoken.append,
                llm_streamer=dummy_stream,
                context_manager=cm,
                owner=owner_mock,
            )
            pipeline.memory_enabled = False
            pipeline._notify_deep_dive = MagicMock()

            question = "spiegami nel dettaglio la fotosintesi"
            res = pipeline.process_text_input(question, speak=True, context_id="voice")

            self.assertGreater(len(res["response"]), 400)
            owner_mock.ConversationCreated.assert_called_once()
            new_chat_id = owner_mock.ConversationCreated.call_args[0][0]
            new_ctx = cm.get(new_chat_id)
            self.assertIsNotNone(new_ctx)
            self.assertEqual(len(new_ctx.messages), 2)
            self.assertEqual(new_ctx.messages[0]["role"], "user")
            self.assertEqual(new_ctx.messages[0]["content"], question)
            self.assertEqual(new_ctx.messages[1]["role"], "assistant")
            self.assertEqual(new_ctx.messages[1]["content"], res["response"].strip())

    def test_deep_dive_chat_no_duplicate_when_memory_enabled(self):
        """Con memoria abilitata, la domanda e la risposta correnti non devono comparire
        due volte nella nuova chat se sono già le ultime due voci della voice ctx."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            from services.conversation_contexts import ConversationContextManager
            cm = ConversationContextManager(base_dir=tmpdir)
            state_machine = StateMachine()
            spoken = []
            owner_mock = MagicMock()

            s1 = "La fotosintesi clorofilliana è il processo biochimico primario della vita sulla Terra. "
            s2 = "Durante questo processo le piante convertono anidride carbonica e acqua in glucosio e ossigeno sfruttando l'energia solare. " * 5

            def dummy_stream(p, **kw):
                yield s1
                yield s2

            pipeline = self._build_pipeline_with_temp_vector_store(
                tmpdir,
                state_machine=state_machine,
                tts_engine=spoken.append,
                llm_streamer=dummy_stream,
                context_manager=cm,
                owner=owner_mock,
            )
            pipeline._notify_deep_dive = MagicMock()

            question = "spiegami nel dettaglio la fotosintesi"
            res = pipeline.process_text_input(question, speak=True, context_id="voice")

            new_chat_id = owner_mock.ConversationCreated.call_args[0][0]
            new_ctx = cm.get(new_chat_id)
            answer = res["response"].strip()

            user_occurrences = [m for m in new_ctx.messages if m["role"] == "user" and m["content"] == question]
            assistant_occurrences = [m for m in new_ctx.messages if m["role"] == "assistant" and m["content"] == answer]
            self.assertEqual(len(user_occurrences), 1)
            self.assertEqual(len(assistant_occurrences), 1)


if __name__ == '__main__':
    unittest.main()

