import os
import sys

# Isola l'ambiente di test da dconf reale dell'utente
os.environ["GSETTINGS_BACKEND"] = "memory"

import unittest
from unittest.mock import patch
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gdk, Gio

try:
    Gtk.init_check()
    _has_display = bool(Gdk.Display.get_default() is not None)
    if _has_display:
        Adw.init()
except Exception:
    _has_display = False


class TestGUI(unittest.TestCase):

    @unittest.skipIf(not _has_display, "No display available (headless environment)")
    def test_gui_import(self):
        """Verifica che i moduli GUI dell'assistente vocale possano essere importati correttamente."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.assistant_window import ChatBubble, AssistantWindow
        self.assertIsNotNone(ChatBubble)
        self.assertIsNotNone(AssistantWindow)

    @unittest.skipIf(not _has_display, "No display available (headless environment)")
    def test_assistant_window_creation(self):
        """Verifica l'inizializzazione dei componenti della AssistantWindow e il messaggio di benvenuto."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.assistant_window import AssistantWindow
        
        app = Adw.Application(application_id="org.local.VoiceAssistant.TestGUI")
        win = AssistantWindow(application=app)
        self.assertIsNotNone(win.chat_box)
        self.assertIsNotNone(win.entry)
        self.assertIsNotNone(win.send_btn)
        self.assertIsNotNone(win.mic_btn)
        self.assertIsNotNone(win.settings_btn)
        self.assertIsNotNone(win.info_btn)
        self.assertIsNotNone(win.user_avatar)
        self.assertIsNotNone(win.user_title)

    @unittest.skipIf(not _has_display, "No display available (headless environment)")
    def test_assistant_window_close_request(self):
        """Verifica che _on_close_request proceda con la chiusura (restituendo False) e pulisca il client."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.assistant_window import AssistantWindow
        
        app = Adw.Application(application_id="org.local.VoiceAssistant.TestGUI2")
        win = AssistantWindow(application=app)
        result = win._on_close_request(win)
        self.assertFalse(result, "_on_close_request deve restituire False per consentire la distruzione della finestra")
        self.assertIsNone(win.daemon_client._proxy)

    @unittest.skipIf(not _has_display, "No display available (headless environment)")
    def test_assistant_window_esc_key(self):
        """Verifica che la pressione del tasto ESC gestisca la chiusura."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.assistant_window import AssistantWindow
        
        app = Adw.Application(application_id="org.local.VoiceAssistant.TestGUI3")
        win = AssistantWindow(application=app)
        handled = win._on_key_pressed(None, Gdk.KEY_Escape, 0, 0)
        self.assertTrue(handled)

    @unittest.skipIf(not _has_display, "No display available (headless environment)")
    def test_assistant_window_icon(self):
        """Verifica che AssistantWindow sia configurata con l'icona vocal-assistant-icon."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.assistant_window import AssistantWindow

        app = Adw.Application(application_id="org.local.VoiceAssistant.TestGUI4")
        win = AssistantWindow(application=app)
        self.assertEqual(win.get_icon_name(), "vocal-assistant-icon")

    @unittest.skipIf(not _has_display, "No display available (headless environment)")
    def test_assistant_window_chat_messages(self):
        """Verifica l'aggiunta di messaggi utente e assistente nella chat."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.assistant_window import AssistantWindow
        
        app = Adw.Application(application_id="org.local.VoiceAssistant.TestGUI4")
        win = AssistantWindow(application=app)
        
        win.add_user_message("Ciao assistente")
        self.assertIsNone(win.current_assistant_bubble)
        
        win.add_assistant_message("Ciao utente!")
        self.assertIsNotNone(win.current_assistant_bubble)
        
        win.append_assistant_token(" Come va?")
        self.assertIn("Come va?", win.current_assistant_bubble.label.get_text())

    @unittest.skipIf(not _has_display, "No display available (headless environment)")
    def test_no_duplicate_sent_messages(self):
        """Verifica che un messaggio inviato via UI non venga duplicato se arriva un segnale TranscriptReceived di eco."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.assistant_window import AssistantWindow

        app = Adw.Application(application_id="org.local.VoiceAssistant.TestGUI_NoDup")
        win = AssistantWindow(application=app)

        # Conta i figli iniziali (messaggio di benvenuto)
        initial_count = 0
        child = win.chat_box.get_first_child()
        while child:
            initial_count += 1
            child = child.get_next_sibling()

        # Invia messaggio tramite entry
        win.entry.set_text("Messaggio test duplicato")
        win._on_send_text(win.entry)

        count_after_send = 0
        child = win.chat_box.get_first_child()
        while child:
            count_after_send += 1
            child = child.get_next_sibling()

        self.assertEqual(count_after_send, initial_count + 1)

        # Simula eco TranscriptReceived immediato con lo stesso testo
        win._on_transcript_received("Messaggio test duplicato", True)

        count_after_echo = 0
        child = win.chat_box.get_first_child()
        while child:
            count_after_echo += 1
            child = child.get_next_sibling()

        # Non deve essere duplicato!
        self.assertEqual(count_after_echo, count_after_send)

    @unittest.skipIf(not _has_display, "No display available (headless environment)")
    def test_reusable_chat_bubble_component(self):
        """Verifica il componente ChatBubble isolato."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.components.chat import ChatBubble

        user_bubble = ChatBubble("Messaggio utente", is_user=True)
        self.assertTrue(user_bubble.is_user)
        self.assertEqual(user_bubble.label.get_text(), "Messaggio utente")

        asst_bubble = ChatBubble("Risposta iniziale", is_user=False)
        self.assertFalse(asst_bubble.is_user)
        asst_bubble.append_text(" e token successivo")
        self.assertEqual(asst_bubble.label.get_text(), "Risposta iniziale e token successivo")

    @unittest.skipIf(not _has_display, "No display available (headless environment)")
    def test_reusable_chat_view_component(self):
        """Verifica il componente ChatView isolato con container Gtk.Box."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.components.chat import ChatView

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        chat_view = ChatView(chat_box=box)

        # Aggiunta messaggio utente
        u_bubble = chat_view.add_user_message("Ciao da utente")
        self.assertEqual(u_bubble.label.get_text(), "Ciao da utente")
        self.assertIsNone(chat_view.current_assistant_bubble)
        self.assertFalse(chat_view.streaming_active)

        # Aggiunta messaggio assistente e streaming
        a_bubble = chat_view.add_assistant_message("Risposta")
        self.assertEqual(chat_view.current_assistant_bubble, a_bubble)

        chat_view.append_assistant_token(" continua...")
        self.assertEqual(a_bubble.label.get_text(), "Risposta continua...")

        chat_view.close_current_bubble()
        self.assertIsNone(chat_view.current_assistant_bubble)

        # Pulizia chat
        chat_view.clear()
        self.assertIsNone(box.get_first_child())

    def test_models_settings_utilities(self):
        """Verifica le utilità di calcolo dimensione e formattazione di ModelsStorageManager."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.components.settings.models import fmt_size, active_match

        self.assertEqual(fmt_size(500), "500.0 B")
        self.assertEqual(fmt_size(1024), "1.0 KB")
        self.assertEqual(fmt_size(1024 * 1024 * 5), "5.0 MB")
        self.assertEqual(fmt_size(1024 * 1024 * 1024 * 2), "2.0 GB")

        self.assertTrue(active_match("vosk-model-small-it-0.22", "vosk-model-small-it"))
        self.assertTrue(active_match("piper-voice-it", "piper-voice-it"))
        self.assertFalse(active_match("vosk-model-en", "vosk-model-it"))
        self.assertFalse(active_match("vosk-model-en", ""))

    def test_general_settings_language_labels(self):
        """Verifica le etichette delle lingue supportate nel componente LanguageSelector."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.components.settings.general import get_language_label, SUPPORTED_LANGUAGES

        self.assertTrue(len(SUPPORTED_LANGUAGES) > 0)
        self.assertIn("Italiano (it)", get_language_label("it"))
        self.assertIn("English (en)", get_language_label("en"))
        self.assertEqual(get_language_label("xyz"), "XYZ (xyz)")

    def test_daemon_client_signal_dispatch(self):
        """Verifica il dispatch dei segnali D-Bus in DaemonClient."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.components.daemon_client import DaemonClient
        from gi.repository import GLib

        received = {}

        def _on_transcript(text, is_final):
            received["transcript"] = (text, is_final)

        def _on_token(token, is_complete):
            received["token"] = (token, is_complete)

        def _on_state(state):
            received["state"] = state

        def _on_dep():
            received["dep"] = True

        client = DaemonClient(
            on_transcript=_on_transcript,
            on_token=_on_token,
            on_state_changed=_on_state,
            on_dependency_required=_on_dep,
        )

        client._on_dbus_signal(None, "test", "TranscriptReceived", GLib.Variant("(sb)", ("Hello", True)))
        self.assertEqual(received.get("transcript"), ("Hello", True))

        client._on_dbus_signal(None, "test", "ResponseTokenStreamed", GLib.Variant("(sb)", ("tok", False)))
        self.assertEqual(received.get("token"), ("tok", False))

        client._on_dbus_signal(None, "test", "StateChanged", GLib.Variant("(s)", ("listening",)))
        self.assertEqual(received.get("state"), "listening")

        client._on_dbus_signal(None, "test", "DependencyRequired", None)
        self.assertTrue(received.get("dep"))

    @unittest.skipIf(not _has_display, "No display available (headless environment)")
    def test_settings_window_creation(self):
        """Verifica la creazione della finestra delle impostazioni e l'inizializzazione dei componenti."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.settings_window import _SettingsWindow
        win = _SettingsWindow()
        self.assertIsNotNone(win.general_settings)
        self.assertIsNotNone(win.wakeword_settings)
        self.assertIsNotNone(win.stt_settings)
        self.assertIsNotNone(win.llm_settings)
        self.assertIsNotNone(win.tts_settings)
        self.assertIsNotNone(win.models_manager)
        self.assertIsNotNone(win.bugreport_settings)
        self.assertIsNotNone(win.about_settings)

    @unittest.skipIf(not _has_display, "No display available (headless environment)")
    def test_bugreport_button_row(self):
        """Verifica che la riga di test connessione in bug report sia un Adw.ButtonRow."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.settings_window import _SettingsWindow

        win = _SettingsWindow()
        test_btn = win._b.get_object("test_bugreport_btn")
        self.assertIsNotNone(test_btn)
        self.assertIsInstance(test_btn, Adw.ButtonRow)
        self.assertIsNotNone(win.bugreport_settings)

    @unittest.skipIf(not _has_display, "No display available (headless environment)")
    def test_stt_settings_mode_toggle_and_visibility(self):
        """Verifica che le checkbox Locale e Cloud in STTSettings commutino la visibilità dei gruppi."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.components.settings.stt import STTSettings

        builder = Gtk.Builder()
        ui_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../data/ui/prefs.ui"))
        builder.add_from_file(ui_path)

        stt_settings = STTSettings(builder, settings=None)

        local_radio = builder.get_object("stt_mode_local_radio")
        cloud_radio = builder.get_object("stt_mode_cloud_radio")
        local_grp = builder.get_object("stt_local_group")
        hw_grp = builder.get_object("whisper_hardware_group")
        cloud_grp = builder.get_object("stt_cloud_group")
        cloud_cfg_grp = builder.get_object("stt_cloud_config_group")

        vosk_radio = builder.get_object("stt_engine_vosk_radio")
        whisper_radio = builder.get_object("stt_engine_whisper_radio")

        # Attiva Locale con Vosk
        local_radio.set_active(True)
        if vosk_radio:
            vosk_radio.set_active(True)
        self.assertTrue(local_grp.get_visible())
        self.assertFalse(hw_grp.get_visible())  # Nascosto quando Vosk è attivo
        self.assertFalse(cloud_grp.get_visible())
        if cloud_cfg_grp:
            self.assertFalse(cloud_cfg_grp.get_visible())

        # Attiva Whisper in modalità Locale
        if whisper_radio:
            whisper_radio.set_active(True)
            self.assertTrue(hw_grp.get_visible())  # Visibile per Whisper

        # Attiva Cloud
        cloud_radio.set_active(True)
        self.assertFalse(local_grp.get_visible())
        self.assertFalse(hw_grp.get_visible())
        self.assertTrue(cloud_grp.get_visible())
        if cloud_cfg_grp:
            self.assertTrue(cloud_cfg_grp.get_visible())

    @unittest.skipIf(not _has_display, "No display available (headless environment)")
    def test_llm_settings_mode_toggle_and_visibility(self):
        """Verifica che le checkbox Locale e Cloud in LLMSettings commutino la visibilità dei gruppi."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.components.settings.llm import LLMSettings

        builder = Gtk.Builder()
        ui_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../data/ui/prefs.ui"))
        builder.add_from_file(ui_path)

        llm_settings = LLMSettings(builder, settings=None)

        local_radio = builder.get_object("llm_mode_local_radio")
        cloud_radio = builder.get_object("llm_mode_cloud_radio")

        local_eng = builder.get_object("llm_local_engine_group")
        local_grp = builder.get_object("llm_local_group")
        cloud_eng = builder.get_object("llm_cloud_engine_group")
        cloud_cfg = builder.get_object("llm_cloud_config_group")

        # Attiva Locale
        local_radio.set_active(True)
        self.assertTrue(local_eng.get_visible())
        self.assertTrue(local_grp.get_visible())
        self.assertFalse(cloud_eng.get_visible())
        self.assertFalse(cloud_cfg.get_visible())

        # Attiva Cloud
        cloud_radio.set_active(True)
        self.assertFalse(local_eng.get_visible())
        self.assertFalse(local_grp.get_visible())
        self.assertTrue(cloud_eng.get_visible())
        self.assertTrue(cloud_cfg.get_visible())

    @unittest.skipIf(not _has_display, "No display available (headless environment)")
    def test_tts_settings_mode_toggle_and_visibility(self):
        """Verifica che le checkbox Locale e Cloud in TTSSettings commutino la visibilità dei gruppi."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.components.settings.tts import TTSSettings

        builder = Gtk.Builder()
        ui_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../data/ui/prefs.ui"))
        builder.add_from_file(ui_path)

        tts_settings = TTSSettings(builder, settings=None)

        local_radio = builder.get_object("tts_mode_local_radio")
        cloud_radio = builder.get_object("tts_mode_cloud_radio")
        local_eng = builder.get_object("tts_local_engine_group")
        cloud_eng = builder.get_object("tts_cloud_engine_group")
        cloud_cfg = builder.get_object("tts_cloud_config_group")
        openai_radio = builder.get_object("tts_engine_openai_radio")

        # Attiva Locale
        local_radio.set_active(True)
        self.assertTrue(local_eng.get_visible())
        self.assertFalse(cloud_eng.get_visible())
        self.assertFalse(cloud_cfg.get_visible())

        # Attiva Cloud
        cloud_radio.set_active(True)
        self.assertFalse(local_eng.get_visible())
        self.assertTrue(cloud_eng.get_visible())
        self.assertTrue(cloud_cfg.get_visible())
        self.assertTrue(openai_radio.get_active())

    def test_audio_settings_rows_exist_and_track_stage_state(self):
        """Verifica che la sottopagina Filtri Audio esista e disabiliti i parametri degli stadi spenti."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.components.settings.audio import AudioSettings

        builder = Gtk.Builder()
        ui_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../data/ui/prefs.ui"))
        builder.add_from_file(ui_path)

        for widget_id in (
            "audio_subpage", "audio_subpage_row", "audio_aec_row",
            "audio_highpass_row", "audio_highpass_cutoff_row",
            "audio_agc_row", "audio_agc_target_row", "audio_agc_max_gain_row",
            "audio_gate_row", "audio_gate_threshold_row", "audio_gate_attenuation_row",
            "audio_reset_btn",
        ):
            self.assertIsNotNone(builder.get_object(widget_id), f"Widget mancante in prefs.ui: {widget_id}")

        settings = Gio.Settings.new("org.gnome.shell.extensions.voice-assistant")
        AudioSettings(builder, settings)

        highpass_switch = builder.get_object("audio_highpass_row")
        cutoff_row = builder.get_object("audio_highpass_cutoff_row")
        gate_switch = builder.get_object("audio_gate_row")
        attenuation_row = builder.get_object("audio_gate_attenuation_row")

        # Spegnere uno stadio rende insensibili i suoi parametri, riaccenderlo li riabilita.
        highpass_switch.set_active(False)
        self.assertFalse(cutoff_row.get_sensitive())
        highpass_switch.set_active(True)
        self.assertTrue(cutoff_row.get_sensitive())

        gate_switch.set_active(False)
        self.assertFalse(attenuation_row.get_sensitive())
        gate_switch.set_active(True)
        self.assertTrue(attenuation_row.get_sensitive())

        # I widget scrivono davvero nelle chiavi GSettings corrispondenti.
        cutoff_row.set_value(150.0)
        self.assertAlmostEqual(settings.get_double("audio-highpass-cutoff"), 150.0, places=3)
        attenuation_row.set_value(0.5)
        self.assertAlmostEqual(settings.get_double("audio-noise-gate-attenuation"), 0.5, places=3)

    def test_download_progress_matches_equivalent_provider_names(self):
        """Un download indicizzato come 'llm' deve agganciarsi alla riga del provider 'gguf'."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.components.settings.model_selector import ModelSelectorController

        same = ModelSelectorController._same_provider_family
        self.assertTrue(same("llm", "gguf"), "llm e gguf indicano lo stesso motore locale")
        self.assertTrue(same("gguf", "llm"))
        self.assertTrue(same("piper", "tts"))
        self.assertTrue(same("sherpa-onnx", "sherpa_onnx"))
        self.assertTrue(same("vosk", "vosk"))
        self.assertFalse(same("vosk", "whisper"))
        self.assertFalse(same("llm", "piper"))

    def test_dispatch_settings_rows_bind_to_gsettings(self):
        """Verifica che la sottopagina Dispatch dei Comandi esista e scriva nelle chiavi GSettings."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.components.settings.dispatch import DispatchSettings

        builder = Gtk.Builder()
        ui_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../data/ui/prefs.ui"))
        builder.add_from_file(ui_path)

        for widget_id in ("dispatch_subpage", "dispatch_subpage_row", "dispatch_fast_path_row", "dispatch_medium_path_row"):
            self.assertIsNotNone(builder.get_object(widget_id), f"Widget mancante in prefs.ui: {widget_id}")

        settings = Gio.Settings.new("org.gnome.shell.extensions.voice-assistant")
        DispatchSettings(builder, settings)

        fast_row = builder.get_object("dispatch_fast_path_row")
        medium_row = builder.get_object("dispatch_medium_path_row")

        fast_row.set_active(True)
        self.assertTrue(settings.get_boolean("fast-path-enabled"))
        fast_row.set_active(False)
        self.assertFalse(settings.get_boolean("fast-path-enabled"))

        medium_row.set_active(False)
        self.assertFalse(settings.get_boolean("medium-path-enabled"))
        medium_row.set_active(True)
        self.assertTrue(settings.get_boolean("medium-path-enabled"))

    @unittest.skipIf(not _has_display, "No display available (headless environment)")
    def test_tts_settings_engine_and_voice_change_in_gui(self):
        """Verifica la commutazione dei motori TTS e della voce locale Piper da interfaccia utente."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.settings_window import _SettingsWindow

        win = _SettingsWindow()
        settings = win._settings
        self.assertIsNotNone(settings)
        local_radio = win._b.get_object("tts_mode_local_radio")
        local_radio.set_active(True)

        piper_radio = win._b.get_object("tts_engine_piper_radio")
        espeak_radio = win._b.get_object("tts_engine_espeak_radio")
        system_radio = win._b.get_object("tts_engine_system_radio")
        local_grp = win._b.get_object("tts_local_group")
        current_row = win._b.get_object("current_tts_model_row")

        # 1. Attiva Piper
        piper_radio.set_active(True)
        self.assertEqual(settings.get_string("tts-provider"), "piper")
        self.assertEqual(settings.get_string("tts-engine"), "piper")
        self.assertTrue(local_grp.get_visible())

        # 2. Attiva eSpeak
        espeak_radio.set_active(True)
        self.assertEqual(settings.get_string("tts-provider"), "espeak")
        self.assertEqual(settings.get_string("tts-engine"), "espeak")
        self.assertFalse(local_grp.get_visible())

        # 3. Attiva System
        system_radio.set_active(True)
        self.assertEqual(settings.get_string("tts-provider"), "system")
        self.assertEqual(settings.get_string("tts-engine"), "system")
        self.assertFalse(local_grp.get_visible())

        # 4. Torna a Piper
        piper_radio.set_active(True)
        self.assertEqual(settings.get_string("tts-provider"), "piper")
        self.assertTrue(local_grp.get_visible())

        # 5. Cambia voce Piper
        settings.set_string("tts-voice", "it_IT-riccardo-x_low")
        from gi.repository import GLib
        while GLib.MainContext.default().iteration(False):
            pass
        self.assertIn("it_IT-riccardo-x_low", current_row.get_subtitle())

        # 6. Attiva Cloud mode
        cloud_radio = win._b.get_object("tts_mode_cloud_radio")
        openai_radio = win._b.get_object("tts_engine_openai_radio")
        cloud_cfg = win._b.get_object("tts_cloud_config_group")
        cloud_radio.set_active(True)
        while GLib.MainContext.default().iteration(False):
            pass
        self.assertEqual(settings.get_string("tts-provider"), "openai")
        self.assertEqual(settings.get_string("tts-engine"), "openai")
        self.assertTrue(openai_radio.get_active())
        self.assertFalse(local_grp.get_visible())
        self.assertTrue(cloud_cfg.get_visible())

        # Ripristina stato iniziale per i test successivi
        local_radio.set_active(True)
        piper_radio.set_active(True)
        settings.set_string("tts-voice", "it_IT-paola-medium")

    @unittest.skipIf(not _has_display, "No display available (headless environment)")
    def test_mcp_settings_components(self):
        """Verifica l'inizializzazione del componente MCPSettings."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.components.settings.mcp import MCPSettings

        builder = Gtk.Builder()
        ui_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../data/ui/prefs.ui"))
        builder.add_from_file(ui_path)

        mcp_settings = MCPSettings(builder, settings=None)
        enable_row = builder.get_object("mcp_enable_row")
        reg_row = builder.get_object("mcp_registry_url_row")
        gnome_row = builder.get_object("mcp_server_gnome_row")

        self.assertIsNotNone(enable_row)
        self.assertIsNotNone(reg_row)
        self.assertIsNotNone(gnome_row)

    @unittest.skipIf(not _has_display, "No display available (headless environment)")
    def test_model_selector_controller(self):
        """Verifica che il controller ModelSelector popoli le righe e gestisca la ricerca."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.components.settings.model_selector import ModelSelectorController

        builder = Gtk.Builder()
        ui_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../data/ui/prefs.ui"))
        builder.add_from_file(ui_path)

        ctrl = ModelSelectorController(builder, settings=None)
        self.assertIsNotNone(ctrl.page)
        self.assertIsNotNone(ctrl.group_all)

        catalog = ctrl._load_local_catalog("stt", "vosk")
        self.assertTrue(len(catalog) > 0)
        self.assertTrue(any("vosk" in m.get("id", "") for m in catalog))

        ctrl._populate_ui(catalog, {})
        self.assertTrue(len(ctrl._all_rows) > 0)

        # Verifica filtro di ricerca
        entry = builder.get_object("search_entry")
        if entry:
            entry.set_text("italian")
            ctrl._on_search_changed(entry)
            visible_rows = [r for r in ctrl._all_rows if r.get_visible()]
            self.assertTrue(len(visible_rows) > 0)
            self.assertTrue(all("it" in r._search_key for r in visible_rows))

    @unittest.skipIf(not _has_display, "No display available (headless environment)")
    def test_settings_window_non_modal(self):
        """Verifica che SettingsWindow sia non-modale di default senza parent o con modal=False."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.settings_window import _SettingsWindow

        win_standalone = _SettingsWindow()
        self.assertFalse(win_standalone.get_modal())

        parent = Gtk.Window()
        win = _SettingsWindow(transient_for=parent, modal=False)
        self.assertFalse(win.get_modal())
        self.assertEqual(win.get_transient_for(), parent)
        self.assertIsNotNone(win.stt_settings)
        self.assertIsNotNone(win.llm_settings)
        self.assertIsNotNone(win.tts_settings)
        self.assertIsNotNone(win.mcp_settings)
        self.assertIsNotNone(win.model_selector)

    @unittest.skipIf(not _has_display, "No display available (headless environment)")
    def test_assistant_window_user_profile_and_about(self):
        """Verifica la configurazione del profilo utente e l'esistenza del pulsante Info."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.assistant_window import AssistantWindow
        from gi.repository import GLib

        app = Adw.Application(application_id="org.local.VoiceAssistant.TestGUIProfile")
        win = AssistantWindow(application=app)
        expected_name = GLib.get_real_name() or GLib.get_user_name()
        if expected_name and expected_name.strip() and expected_name != "Unknown":
            self.assertEqual(win.user_title.get_title(), expected_name)
        self.assertTrue(hasattr(win, "_on_open_about"))

    @unittest.skipIf(not _has_display, "No display available (headless environment)")
    def test_open_settings_transient_behavior(self):
        """Verifica che open_settings_window leghi parent solo quando passato esplicitamente."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.settings_window import open_settings_window, _SettingsWindow

        app = Adw.Application(application_id="org.local.VoiceAssistant.TestGUISettings")
        # 1. Chiamata con parent=None: non deve avere transient_for
        open_settings_window(parent=None, application=app)
        settings_wins = [w for w in app.get_windows() if isinstance(w, _SettingsWindow)]
        self.assertEqual(len(settings_wins), 1)
        self.assertIsNone(settings_wins[0].get_transient_for())

        # 2. Chiamata con parent: deve legare il parent
        parent_win = Gtk.Window(application=app)
        open_settings_window(parent=parent_win, application=app)
        self.assertEqual(settings_wins[0].get_transient_for(), parent_win)

    @unittest.skipIf(not _has_display, "No display available (headless environment)")
    def test_model_selectors_open_and_work(self):
        """Verifica che i pulsanti e le righe di selezione modello per STT, LLM e TTS aprano la subpage senza eccezioni."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.settings_window import _SettingsWindow

        win = _SettingsWindow()

        # 1. Test STT model selector opening via row activation
        stt_row = win._b.get_object("current_model_row")
        self.assertIsNotNone(stt_row)
        stt_row.emit("activated")
        self.assertEqual(win.model_selector.current_service, "stt")
        self.assertIn("STT", win.model_selector.page.get_title())

        # Test pop back
        win.pop_subpage()

        # 2. Test LLM model selector opening via row
        llm_row = win._b.get_object("current_llm_model_row")
        self.assertIsNotNone(llm_row)
        llm_row.emit("activated")
        self.assertEqual(win.model_selector.current_service, "llm")
        self.assertIn("LLM", win.model_selector.page.get_title())

        # Test pop back
        win.pop_subpage()

        # 3. Test TTS voice selector opening via row activation
        tts_row = win._b.get_object("current_tts_model_row")
        self.assertIsNotNone(tts_row)
        tts_row.emit("activated")
        self.assertEqual(win.model_selector.current_service, "tts")
        self.assertIn("TTS", win.model_selector.page.get_title())

        # Test row activation in selector
        catalog = win.model_selector._load_local_catalog("tts", "piper")
        self.assertTrue(len(catalog) > 0)
        win.model_selector._populate_ui(catalog, {})
        self.assertTrue(len(win.model_selector._all_rows) > 0)

        first_row = win.model_selector._all_rows[0]
        first_row.emit("activated")

    @unittest.skipIf(not _has_display, "No display available (headless environment)")
    def test_model_selector_download_cancel_and_delete(self):
        """Verifica lo stato del download live, il tasto di annullamento e il tasto di eliminazione nelle schede Tutti, Installati e In download."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.settings_window import _SettingsWindow
        from unittest.mock import patch

        win = _SettingsWindow()
        selector = win.model_selector
        selector.current_service = "tts"
        selector.current_provider = "piper"

        models = [
            {"id": "it_IT-paola-medium", "name": "Paola", "size_text": "60 MB", "lang": "it"},
            {"id": "it_IT-riccardo-x_low", "name": "Riccardo", "size_text": "20 MB", "lang": "it"},
        ]

        # 1. Simula Paola come installata e Riccardo come disponibile
        with patch.object(selector, "_get_installed_models_set", return_value={"it_it-paola-medium"}):
            selector._populate_ui(models, {})

        self.assertEqual(len(selector._all_rows), 2)
        # Paola deve essere in group_installed
        self.assertEqual(len(selector._installed_rows), 1)
        self.assertEqual(len(selector._downloading_rows), 0)

        paola_item = selector._items["it_IT-paola-medium"]
        riccardo_item = selector._items["it_IT-riccardo-x_low"]

        self.assertEqual(paola_item["state"], "installed")
        self.assertEqual(riccardo_item["state"], "available")

        # 2. Avvia download di Riccardo
        with patch("gi.repository.Gio.bus_get_sync"):
            selector._on_download_clicked("it_IT-riccardo-x_low")

        self.assertEqual(riccardo_item["state"], "downloading")
        # Deve comparire nella scheda In download
        self.assertEqual(len(selector._downloading_rows), 1)
        self.assertIsNotNone(riccardo_item["row_downloading"])

        # 3. Ricezione progresso al 45%
        selector._handle_download_progress("piper", "it_IT-riccardo-x_low", 45)
        self.assertEqual(riccardo_item["percent"], 45)

        # 4. Annullamento download
        with patch("gi.repository.Gio.bus_get_sync"):
            selector._on_cancel_clicked("it_IT-riccardo-x_low")

        self.assertEqual(riccardo_item["state"], "available")
        # Non deve più essere in download
        self.assertEqual(len(selector._downloading_rows), 0)
        self.assertIsNone(riccardo_item["row_downloading"])

        # 5. Riavvia download e completa al 100%
        selector._on_download_clicked("it_IT-riccardo-x_low")
        selector._handle_download_progress("piper", "it_IT-riccardo-x_low", 100)

        self.assertEqual(riccardo_item["state"], "installed")
        self.assertEqual(len(selector._downloading_rows), 0)
        # Ora Riccardo deve comparire nella scheda Installati insieme a Paola
        self.assertEqual(len(selector._installed_rows), 2)

        # 6. Eliminazione modello Riccardo
        with patch.object(selector, "_delete_model_files") as mock_del:
            with patch("gi.repository.Gio.bus_get_sync"):
                with patch("gi.repository.Adw.AlertDialog.present", lambda self, parent: self.emit("response", "delete")):
                    selector._on_delete_clicked("it_IT-riccardo-x_low")

        self.assertEqual(riccardo_item["state"], "available")
        # Rimosso dalla scheda Installati
        self.assertEqual(len(selector._installed_rows), 1)
        mock_del.assert_called_once()

    @unittest.skipIf(not _has_display, "No display available (headless environment)")
    def test_wakeword_model_selection_independence_and_sherpa(self):
        """Verifica la selezione del modello Vosk per Wake Word indipendente da STT, e il supporto Sherpa-ONNX."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.settings_window import _SettingsWindow
        from unittest.mock import patch

        win = _SettingsWindow()
        settings = win._settings
        selector = win.model_selector

        if not settings:
            self.skipTest("GSettings not available")

        # 1. Impostiamo uno stato iniziale noto
        settings.set_string("stt-model", "vosk-model-small-it-0.22")
        settings.set_string("vosk-ww-model", "vosk-model-small-it-0.22")
        settings.set_string("sherpa-model", "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01")

        # 2. Apertura selettore per Vosk Wakeword tramite attivazione riga
        vosk_ww_row = win._b.get_object("current_vosk_ww_model_row")
        self.assertIsNotNone(vosk_ww_row)
        vosk_ww_row.emit("activated")
        self.assertEqual(selector.current_service, "wakeword")
        self.assertEqual(selector.current_provider, "vosk")
        self.assertIn("Wake Word", selector.page.get_title())

        # Simula attivazione riga di un modello diverso (es. vosk-model-en-us-0.22)
        models = [
            {"id": "vosk-model-small-it-0.22", "name": "Vosk IT", "size_text": "47 MB"},
            {"id": "vosk-model-en-us-0.22", "name": "Vosk EN", "size_text": "1.8 GB"},
        ]
        with patch.object(selector, "_get_installed_models_set", return_value={"vosk-model-small-it-0.22", "vosk-model-en-us-0.22"}):
            selector._populate_ui(models, {})
            en_row = [r for r in selector._all_rows if getattr(r, "_model_id", "") == "vosk-model-en-us-0.22"][0]
            en_row.emit("activated")

        # Verifica: vosk-ww-model è stato aggiornato, ma stt-model è rimasto intatto!
        self.assertEqual(settings.get_string("vosk-ww-model"), "vosk-model-en-us-0.22")
        self.assertEqual(settings.get_string("stt-model"), "vosk-model-small-it-0.22")

        # 3. Apertura selettore per Sherpa Wakeword tramite attivazione riga
        sherpa_ww_row = win._b.get_object("current_sherpa_model_row")
        self.assertIsNotNone(sherpa_ww_row)
        sherpa_ww_row.emit("activated")
        self.assertEqual(selector.current_service, "wakeword")
        self.assertEqual(selector.current_provider, "sherpa-onnx")
        self.assertIn("Sherpa", selector.page.get_title())

        sherpa_models = [
            {"id": "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01", "name": "Sherpa Giga", "size_text": "15 MB"},
            {"id": "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01", "name": "Sherpa Wenet", "size_text": "15 MB"},
        ]
        with patch.object(selector, "_get_installed_models_set", return_value={"sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01", "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01"}):
            selector._populate_ui(sherpa_models, {})
            wenet_row = [r for r in selector._all_rows if getattr(r, "_model_id", "") == "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01"][0]
            wenet_row.emit("activated")

        # Verifica: sherpa-model è stato aggiornato!
        self.assertEqual(settings.get_string("sherpa-model"), "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01")

        # 4. Visibilità condizionale widget motore
        vosk_radio = win._b.get_object("ww_engine_vosk_radio")
        sherpa_radio = win._b.get_object("ww_engine_sherpa_radio")
        oww_radio = win._b.get_object("ww_engine_oww_radio")
        vosk_row = win._b.get_object("current_vosk_ww_model_row")
        sherpa_row = win._b.get_object("current_sherpa_model_row")
        config_grp = win._b.get_object("wakeword_config_group")
        oww_grp = win._b.get_object("oww_keyword_group")

        vosk_radio.set_active(True)
        win.wakeword_settings.apply_engine_visibility()
        self.assertTrue(config_grp.get_visible())
        self.assertTrue(vosk_row.get_visible())
        self.assertFalse(sherpa_row.get_visible())
        self.assertFalse(oww_grp.get_visible())

        sherpa_radio.set_active(True)
        win.wakeword_settings.apply_engine_visibility()
        self.assertTrue(config_grp.get_visible())
        self.assertFalse(vosk_row.get_visible())
        self.assertTrue(sherpa_row.get_visible())
        self.assertFalse(oww_grp.get_visible())

        oww_radio.set_active(True)
        win.wakeword_settings.apply_engine_visibility()
        self.assertFalse(config_grp.get_visible())
        self.assertTrue(oww_grp.get_visible())

    @unittest.skipIf(not _has_display, "No display available (headless environment)")
    def test_model_selector_view_stack_non_homogeneous_and_scroll(self):
        """Verifica che selector_view_stack non sia vhomogeneous e che il cambio scheda resetti lo scroll."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from unittest.mock import patch
        from gui.settings_window import _SettingsWindow

        win = _SettingsWindow()
        selector = win.model_selector

        # Verifica vhomogeneous e hhomogeneous disattivati
        self.assertFalse(selector.view_stack.get_vhomogeneous())
        self.assertFalse(selector.view_stack.get_hhomogeneous())
        self.assertIsNotNone(selector.scrolled_window)

        # Inizialmente senza modelli: i placeholder devono essere presenti se popolata vuota
        selector._populate_ui([], {})
        self.assertIsNotNone(selector._installed_placeholder)
        self.assertIsNotNone(selector._downloading_placeholder)
        self.assertEqual(len(selector._installed_rows), 0)
        self.assertEqual(len(selector._downloading_rows), 0)

        # Popola con un modello installato
        models = [{"id": "test-model", "name": "Test Model", "size_text": "10 MB"}]
        with patch.object(selector, "_get_installed_models_set", return_value={"test-model"}):
            selector._populate_ui(models, {})

        # Placeholder installato rimosso perché c'è un modello installato
        self.assertIsNone(selector._installed_placeholder)
        self.assertEqual(len(selector._installed_rows), 1)
        # Placeholder download presente perché non ci sono download
        self.assertIsNotNone(selector._downloading_placeholder)

        # Simula scorrimento verso il basso
        adj = selector.scrolled_window.get_vadjustment()
        adj.set_upper(1000)
        adj.set_value(150)
        self.assertEqual(adj.get_value(), 150)

        # Notifica cambio scheda visibile -> deve resettare a 0
        selector._on_visible_child_changed(selector.view_stack, None)
        self.assertEqual(adj.get_value(), 0)

    def test_blueprint_assembler(self):
        """Verifica l'assemblaggio corretto dei moduli Blueprint tramite assemble_blueprints."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../scripts")))
        from assemble_blueprints import assemble
        result = assemble()
        self.assertIn("stt_mode_local_radio", result)
        self.assertIn("stt_mode_cloud_radio", result)
        self.assertIn("llm_mode_local_radio", result)
        self.assertIn("llm_mode_cloud_radio", result)
        self.assertIn("tts_mode_local_radio", result)
        self.assertIn("tts_mode_cloud_radio", result)

    @unittest.skipIf(not _has_display, "No display available (headless environment)")
    def test_no_false_positive_installed_models_in_selector(self):
        """Verifica che sottostringhe come 'graph' o 'tts' non causino falsi positivi nei modelli installati."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.settings_window import _SettingsWindow
        from unittest.mock import patch

        win = _SettingsWindow()
        selector = win.model_selector

        catalog = [
            {"id": "vosk-model-small-it-0.22", "name": "Vosk IT", "size_text": "47 MB"},
            {"id": "vosk-model-uk-v3-lgraph", "name": "Vosk UK (lgraph)", "size_text": "300 MB"},
            {"id": "vosk-model-tts-ru-0.9-multi", "name": "Vosk TTS", "size_text": "700 MB"},
        ]

        with patch.object(selector, "_get_installed_models_set", return_value={"vosk-model-small-it-0.22"}):
            selector.current_service = "stt"
            selector.current_provider = "vosk"
            selector._populate_ui(catalog, {})

            self.assertEqual(selector._items["vosk-model-small-it-0.22"]["state"], "installed")
            self.assertEqual(selector._items["vosk-model-uk-v3-lgraph"]["state"], "available")
            self.assertEqual(selector._items["vosk-model-tts-ru-0.9-multi"]["state"], "available")

            self.assertEqual(len(selector._installed_rows), 1)
            self.assertEqual(selector._installed_rows[0]._model_id, "vosk-model-small-it-0.22")

    @unittest.skipIf(not _has_display, "No display available (headless environment)")
    def test_llm_model_selector_gguf_and_ollama(self):
        """Verifica che il selettore modelli per LLM mostri correttamente sia i modelli GGUF (inclusi quelli installati) sia quelli Ollama."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.settings_window import _SettingsWindow
        from unittest.mock import patch

        win = _SettingsWindow()
        selector = win.model_selector

        # 1. Catalogo locale GGUF
        gguf_catalog = selector._load_local_catalog("llm", "gguf")
        self.assertTrue(len(gguf_catalog) >= 5)
        self.assertTrue(any("llama-3.2-1b" in m["id"].lower() for m in gguf_catalog))

        # 2. Catalogo locale Ollama
        ollama_catalog = selector._load_local_catalog("llm", "ollama")
        self.assertTrue(len(ollama_catalog) >= 8)
        self.assertTrue(any(m["id"] == "llama3.2:1b" for m in ollama_catalog))

        # 3. Verifica riconoscimento modello GGUF installato su disco con nome file colon-separated
        installed_set = {"llama-3.2-1b-instruct-q4_k_m.gguf", "llama-3.2-1b-instruct-q4_k_m"}
        with patch.object(selector, "_get_installed_models_set", return_value=installed_set):
            selector.current_service = "llm"
            selector.current_provider = "gguf"
            selector._populate_ui(gguf_catalog, {})

            llama_item = selector._items["bartowski/Llama-3.2-1B-Instruct-GGUF:Llama-3.2-1B-Instruct-Q4_K_M.gguf"]
            self.assertEqual(llama_item["state"], "installed")
            self.assertEqual(len(selector._installed_rows), 1)

            # E con Ollama (nessun modello locale)
            selector.current_provider = "ollama"
            selector._populate_ui(ollama_catalog, {})
            self.assertTrue(len(selector._all_rows) >= 8)

    @unittest.skipIf(not _has_display, "No display available (headless environment)")
    def test_llm_settings_deepseek_ollama_cloud_and_endpoint_sync(self):
        """Verifica la presenza di DeepSeek e Ollama Cloud e la sincronizzazione automatica dell'endpoint URL al cambio provider."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.settings_window import _SettingsWindow

        win = _SettingsWindow()
        if win._settings:
            win._settings.reset("llm-mode")
            win._settings.reset("llm-model")
            win._settings.reset("llm-endpoint")
        if hasattr(win, "llm_settings") and hasattr(win.llm_settings, "cloud_config"):
            win.llm_settings.cloud_config.set_provider_config("llm", "ollama_cloud", {"model": "llama3.3"})
        llm_settings = win.llm_settings

        deepseek_radio = win._b.get_object("llm_cloud_deepseek_radio")
        ollama_cloud_radio = win._b.get_object("llm_cloud_ollama_radio")
        openai_radio = win._b.get_object("llm_cloud_openai_radio")
        url_row = win._b.get_object("llm_url_row")
        model_row = win._b.get_object("llm_cloud_model_row")

        self.assertIsNotNone(deepseek_radio)
        self.assertIsNotNone(ollama_cloud_radio)
        self.assertIsNotNone(openai_radio)
        self.assertIsNotNone(url_row)
        self.assertIsNotNone(model_row)

        # 1. Attivazione DeepSeek
        deepseek_radio.set_active(True)
        self.assertEqual(win._settings.get_string("llm-mode"), "deepseek")
        self.assertEqual(win._settings.get_string("llm-endpoint"), "https://api.deepseek.com/v1/chat/completions")
        self.assertEqual(url_row.get_text(), "https://api.deepseek.com/v1/chat/completions")
        self.assertEqual(win._settings.get_string("llm-model"), "deepseek-chat")
        self.assertEqual(model_row.get_subtitle(), "deepseek-chat")

        # 2. Attivazione Ollama Cloud
        ollama_cloud_radio.set_active(True)
        self.assertEqual(win._settings.get_string("llm-mode"), "ollama_cloud")
        self.assertEqual(win._settings.get_string("llm-endpoint"), "https://ollama.com/v1/chat/completions")
        self.assertEqual(url_row.get_text(), "https://ollama.com/v1/chat/completions")
        self.assertEqual(win._settings.get_string("llm-model"), "llama3.3")
        self.assertEqual(model_row.get_subtitle(), "llama3.3")

        # 3. Ritorno a OpenAI: l'endpoint non deve restare su localhost:11434 o sul precedente
        openai_radio.set_active(True)
        self.assertEqual(win._settings.get_string("llm-mode"), "openai")
        self.assertEqual(win._settings.get_string("llm-endpoint"), "https://api.openai.com/v1/chat/completions")
        self.assertEqual(url_row.get_text(), "https://api.openai.com/v1/chat/completions")
        self.assertEqual(win._settings.get_string("llm-model"), "gpt-4o-mini")
        self.assertEqual(model_row.get_subtitle(), "gpt-4o-mini")

        # 4. Cataloghi selettore modelli per deepseek e ollama_cloud
        deepseek_cat = win.model_selector._load_local_catalog("llm", "deepseek")
        self.assertTrue(len(deepseek_cat) >= 2)
        self.assertTrue(any(m["id"] == "deepseek-chat" for m in deepseek_cat))

        ollama_cloud_cat = win.model_selector._load_local_catalog("llm", "ollama_cloud")
        self.assertTrue(len(ollama_cloud_cat) >= 3)
        self.assertTrue(any(m["id"] == "llama3.3" for m in ollama_cloud_cat))

    @unittest.skipIf(not _has_display, "No display available (headless environment)")
    def test_cloud_model_selector_simplified_view_and_activation(self):
        """Verifica la vista semplificata e la selezione immediata dei modelli cloud."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.settings_window import _SettingsWindow
        win = _SettingsWindow()
        selector = win.model_selector

        selector.open_selector("llm", "ollama_cloud")
        self.assertFalse(selector.view_switcher_bar.get_visible())
        self.assertFalse(selector.hf_custom_group.get_visible())
        if selector.header_bar:
            self.assertEqual(selector.header_bar.get_title_widget(), selector.window_title)
            self.assertEqual(selector.window_title.get_title(), "Modelli LLM")
            self.assertEqual(selector.window_title.get_subtitle(), "Ollama Cloud")

        # Verifica ritorno a provider locale
        selector.open_selector("llm", "gguf")
        self.assertTrue(selector.view_switcher_bar.get_visible())
        self.assertFalse(selector.view_switcher_bar.get_reveal())
        if selector.header_bar and selector.view_switcher:
            self.assertEqual(selector.header_bar.get_title_widget(), selector.view_switcher)

        # Riapri cloud per verifica attivazione immediata senza download
        selector.open_selector("llm", "ollama_cloud")
        with patch.object(win, "pop_subpage") as mock_pop:
            selector._on_cloud_row_activated(None, "qwen2.5")
            self.assertEqual(win._settings.get_string("llm-model"), "qwen2.5")
            mock_pop.assert_called_once()

    @unittest.skipIf(not _has_display, "No display available (headless environment)")
    def test_tts_cloud_openai_radio_not_deselectable(self):
        """Verifica che il radio button OpenAI Cloud TTS non sia deselezionabile quando attivo."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.settings_window import _SettingsWindow
        win = _SettingsWindow()

        cloud_radio = win._b.get_object("tts_mode_cloud_radio")
        openai_radio = win._b.get_object("tts_engine_openai_radio")
        self.assertIsNotNone(cloud_radio)
        self.assertIsNotNone(openai_radio)

        cloud_radio.set_active(True)
        self.assertTrue(openai_radio.get_active())

        # Tentativo di deselezionare il checkbutton
        openai_radio.set_active(False)
        self.assertTrue(openai_radio.get_active(), "OpenAI radio non deve essere deselezionabile in modalità cloud")

    @unittest.skipIf(not _has_display, "No display available (headless environment)")
    def test_tts_cloud_config_persistence(self):
        """Verifica che la configurazione TTS Cloud (API key, endpoint, model, voice) sia sincronizzata con cloud_providers.json."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.settings_window import _SettingsWindow
        from daemon.core.cloud_config import get_cloud_config
        cloud_cfg = get_cloud_config()
        win = _SettingsWindow()

        api_key_row = win._b.get_object("tts_cloud_api_key_row")
        endpoint_row = win._b.get_object("tts_cloud_endpoint_row")
        model_row = win._b.get_object("tts_cloud_model_row")
        voice_row = win._b.get_object("tts_cloud_voice_row")

        self.assertIsNotNone(api_key_row)
        self.assertIsNotNone(endpoint_row)
        self.assertIsNotNone(model_row)
        self.assertIsNotNone(voice_row)

        endpoint_row.set_text("https://custom-tts.ai/v1/audio/speech")
        self.assertEqual(cloud_cfg.get_endpoint("tts", "openai"), "https://custom-tts.ai/v1/audio/speech")

        api_key_row.set_text("sk-tts-secret")
        self.assertEqual(cloud_cfg.get_api_key("tts", "openai"), "sk-tts-secret")

        model_row.set_text("tts-1-hd")
        self.assertEqual(cloud_cfg.get_model("tts", "openai"), "tts-1-hd")

        voice_row.set_text("shimmer")
        self.assertEqual(cloud_cfg.get_provider_config("tts", "openai").get("voice"), "shimmer")

    @unittest.skipIf(not _has_display, "No display available (headless environment)")
    def test_subpages_navigation_from_general_and_llm(self):
        """Verifica che Storage, Bug Reporting e MCP siano subpage aperte dalle rispettive schede."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.settings_window import _SettingsWindow
        win = _SettingsWindow()

        models_row = win._b.get_object("models_subpage_row")
        models_subpage = win._b.get_object("models_subpage")
        bugreport_row = win._b.get_object("bugreport_subpage_row")
        bugreport_subpage = win._b.get_object("bugreport_subpage")
        mcp_row = win._b.get_object("mcp_subpage_row")
        mcp_subpage = win._b.get_object("mcp_subpage")

        self.assertIsNotNone(models_row)
        self.assertIsNotNone(models_subpage)
        self.assertIsNotNone(bugreport_row)
        self.assertIsNotNone(bugreport_subpage)
        self.assertIsNotNone(mcp_row)
        self.assertIsNotNone(mcp_subpage)

        # Simula apertura subpage models
        with patch.object(win, "push_subpage") as mock_push:
            models_row.emit("activated")
            mock_push.assert_called_with(models_subpage)

        # Simula apertura subpage bugreport
        with patch.object(win, "push_subpage") as mock_push:
            bugreport_row.emit("activated")
            mock_push.assert_called_with(bugreport_subpage)

        # Simula apertura subpage mcp
        with patch.object(win, "push_subpage") as mock_push:
            mcp_row.emit("activated")
            mock_push.assert_called_with(mcp_subpage)

    @unittest.skipIf(not _has_display, "No display available (headless environment)")
    def test_model_selector_hf_import_visibility(self):
        """Verifica che Hugging Face repository download appaia solo per modelli LLM locali."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.settings_window import _SettingsWindow
        win = _SettingsWindow()
        selector = win.model_selector

        self.assertIsNotNone(selector.hf_custom_group)
        self.assertIsNotNone(selector.hf_dialog_row)
        self.assertIsNotNone(selector.hf_header_btn)

        # 1. LLM GGUF (locale) -> visibile
        selector.open_selector("llm", "gguf")
        self.assertTrue(selector.hf_custom_group.get_visible())
        self.assertTrue(selector.hf_header_btn.get_visible())

        # 2. LLM Ollama (locale) -> visibile
        selector.open_selector("llm", "ollama")
        self.assertTrue(selector.hf_custom_group.get_visible())
        self.assertTrue(selector.hf_header_btn.get_visible())

        # 3. LLM OpenAI (cloud) -> nascosto
        selector.open_selector("llm", "openai")
        self.assertFalse(selector.hf_custom_group.get_visible())
        self.assertFalse(selector.hf_header_btn.get_visible())

        # 4. STT Vosk -> nascosto
        selector.open_selector("stt", "vosk")
        self.assertFalse(selector.hf_custom_group.get_visible())
        self.assertFalse(selector.hf_header_btn.get_visible())

        # 5. TTS Piper -> nascosto
        selector.open_selector("tts", "piper")
        self.assertFalse(selector.hf_custom_group.get_visible())
        self.assertFalse(selector.hf_header_btn.get_visible())

        # 6. Wake Word Sherpa -> nascosto
        selector.open_selector("wakeword", "sherpa-onnx")
        self.assertFalse(selector.hf_custom_group.get_visible())
        self.assertFalse(selector.hf_header_btn.get_visible())

    @unittest.skipIf(not _has_display, "No display available (headless environment)")
    def test_model_selector_hf_download_dialog(self):
        """Verifica l'apertura e l'interazione con il dialog di download Hugging Face."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.settings_window import _SettingsWindow
        win = _SettingsWindow()
        selector = win.model_selector

        with patch("gi.repository.Adw.AlertDialog.present") as mock_present:
            selector._show_hf_download_dialog()
            self.assertTrue(mock_present.called)

    @unittest.skipIf(not _has_display, "No display available (headless environment)")
    def test_cloud_model_selector_custom_dialog(self):
        """Verifica che il dialog del modello cloud personalizzato venga presentato."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.settings_window import _SettingsWindow
        win = _SettingsWindow()
        selector = win.model_selector

        selector.open_selector("llm", "openai")
        with patch("gi.repository.Adw.AlertDialog.present") as mock_present:
            selector._show_custom_model_dialog()
            self.assertTrue(mock_present.called)



    @unittest.skipIf(not _has_display, "No display available (headless environment)")
    def test_storage_and_models_total_size_and_ww_installed_only(self):
        """Verifica che Storage e Models mostri lo spazio occupato e solo i modelli scaricati in Wake Word."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.settings_window import _SettingsWindow
        win = _SettingsWindow()

        total_size_row = win._b.get_object("models_total_size_row")
        self.assertIsNotNone(total_size_row)
        self.assertTrue(len(total_size_row.get_subtitle() or "") > 0)

        ww_group = win._b.get_object("ww_models_group")
        clean_btn = win._b.get_object("clean_unused_btn")
        self.assertIsNotNone(ww_group)
        self.assertIsNotNone(clean_btn)

        # Verifica che non ci siano righe di metadati come "Engine" o "Keyword"
        current_rows = getattr(ww_group, "_current_rows", [])
        for row in current_rows:
            title = row.get_title() or ""
            self.assertNotIn(title.lower(), ["engine", "keyword", "modello wakeword"])

    @unittest.skipIf(not _has_display, "No display available (headless environment)")
    def test_ollama_local_model_selection_and_persistence(self):
        """Verifica la corretta selezione, marcatura attivo e persistenza del modello Ollama locale."""
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
        from gui.settings_window import _SettingsWindow
        win = _SettingsWindow()
        selector = win.model_selector
        llm = win.llm_settings

        if win._settings:
            win._settings.set_string("llm-mode", "ollama")
            win._settings.set_string("llm-model", "qwen2.5-coder:1.5b")

        # Mock della risposta modelli Ollama
        mock_models = [
            {"id": "llama3.1:8b", "name": "llama3.1:8b", "subtitle": "Installato", "size_text": "4.6 GB", "installed": True, "provider": "ollama"},
            {"id": "qwen2.5-coder:1.5b", "name": "qwen2.5-coder:1.5b", "subtitle": "Installato", "size_text": "940 MB", "installed": True, "provider": "ollama"},
            {"id": "llama3.2:1b", "name": "Llama 3.2 1B", "subtitle": "Meta", "size_text": "~1.3 GB", "installed": False, "provider": "ollama"},
            {"id": "llama3.2", "name": "Llama 3.2 3B", "subtitle": "Meta", "size_text": "~2.0 GB", "installed": False, "provider": "ollama"},
        ]

        selector.current_service = "llm"
        selector.current_provider = "ollama"
        selector._populate_ui(mock_models, {})

        # Solo qwen2.5-coder:1.5b deve risultare attivo
        self.assertTrue(selector._items["qwen2.5-coder:1.5b"]["is_active"])
        self.assertFalse(selector._items["llama3.1:8b"]["is_active"])
        self.assertFalse(selector._items["llama3.2:1b"]["is_active"])
        self.assertFalse(selector._items["llama3.2"]["is_active"])

        # Selezione di llama3.1:8b
        row = selector._items["llama3.1:8b"]["row_all"]
        selector._on_row_activated(row, "llama3.1:8b")
        self.assertEqual(win._settings.get_string("llm-model"), "llama3.1:8b")

        # Verifica che _sync_default_model NON sovrascriva il modello selezionato
        llm._sync_default_model("ollama")
        self.assertEqual(win._settings.get_string("llm-model"), "llama3.1:8b")

        # Verifica sottotitolo riga modello locale
        local_row = win._b.get_object("current_llm_model_row")
        self.assertIn("llama3.1:8b", local_row.get_subtitle())


if __name__ == "__main__":
    unittest.main()



