import pytest
import os
import sys
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gdk

def test_gui_import():
    """Verifica che i moduli GUI dell'assistente vocale possano essere importati correttamente."""
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src/daemon")))
    from gui.assistant_window import ChatBubble, AssistantWindow
    assert ChatBubble is not None
    assert AssistantWindow is not None


def test_assistant_window_creation():
    """Verifica l'inizializzazione dei componenti della AssistantWindow e il messaggio di benvenuto."""
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src/daemon")))
    from gui.assistant_window import AssistantWindow
    
    app = Adw.Application(application_id="org.local.VoiceAssistant.TestGUI")
    win = AssistantWindow(application=app)
    assert win.chat_box is not None
    assert win.entry is not None
    assert win.send_btn is not None
    assert win.mic_btn is not None


def test_assistant_window_close_request():
    """Verifica che _on_close_request nasconda la finestra anziché distruggerla."""
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src/daemon")))
    from gui.assistant_window import AssistantWindow
    
    app = Adw.Application(application_id="org.local.VoiceAssistant.TestGUI2")
    win = AssistantWindow(application=app)
    result = win._on_close_request(win)
    assert result is True
    assert win.get_visible() is False


def test_assistant_window_esc_key():
    """Verifica che la pressione del tasto ESC nasconda la finestra."""
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src/daemon")))
    from gui.assistant_window import AssistantWindow
    
    app = Adw.Application(application_id="org.local.VoiceAssistant.TestGUI3")
    win = AssistantWindow(application=app)
    handled = win._on_key_pressed(None, Gdk.KEY_Escape, 0, 0)
    assert handled is True
    assert win.get_visible() is False


def test_assistant_window_chat_messages():
    """Verifica l'aggiunta di messaggi utente e assistente nella chat."""
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src/daemon")))
    from gui.assistant_window import AssistantWindow
    
    app = Adw.Application(application_id="org.local.VoiceAssistant.TestGUI4")
    win = AssistantWindow(application=app)
    
    win.add_user_message("Ciao assistente")
    assert win.current_assistant_bubble is None
    
    win.add_assistant_message("Ciao utente!")
    assert win.current_assistant_bubble is not None
    
    win.append_assistant_token(" Come va?")
    assert "Come va?" in win.current_assistant_bubble.label.get_text()
