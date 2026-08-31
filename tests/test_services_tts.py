import os
import sys
import glob
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

from services.tts_service import TTSServiceManager, EspeakTTSProvider, PiperTTSProvider

class TestServicesTTS(unittest.TestCase):

    def test_espeak_provider_fallback(self):
        """Verifica la sintesi tramite espeak-ng se installato nel sistema."""
        provider = EspeakTTSProvider()
        # Se espeak-ng è installato sul sistema Linux
        wav_bytes = provider.synthesize("Test vocale", voice="it", speed=1.0)
        if wav_bytes:
            self.assertTrue(len(wav_bytes) > 44, "Il file WAV generato deve contenere intestazione e dati PCM")

    def test_tts_service_manager_routing(self):
        """Verifica che TTSServiceManager route correttamente le chiamate e gestisca le impostazioni disabilitate."""
        audio_player_mock = MagicMock()
        settings_mock = {"tts-enabled": False, "tts-provider": "espeak", "tts-voice": "it", "tts-speed": 1.0}
        
        settings_observer = MagicMock()
        settings_observer.get.side_effect = lambda k, default=None: settings_mock.get(k, default)

        manager = TTSServiceManager(
            audio_player=audio_player_mock,
            settings_observer=settings_observer
        )

        # Se disabilitato nelle impostazioni, speak() restituisce False e non chiama l'audio player
        success = manager.speak("Messaggio di prova")
        self.assertFalse(success)
        audio_player_mock.play_wav_bytes.assert_not_called()

        # Abilita sintesi nelle impostazioni
        settings_mock["tts-enabled"] = True
        
        # Mokka il provider espeak
        espeak_mock = MagicMock()
        espeak_mock.synthesize.return_value = b"RIFF....WAVEfmt...."
        manager.providers["espeak"] = espeak_mock

        success = manager.speak("Messaggio di prova", provider_name="espeak")
        self.assertTrue(success)
        espeak_mock.synthesize.assert_called_once()
        audio_player_mock.play_wav_bytes.assert_called_once_with(b"RIFF....WAVEfmt....")

if __name__ == '__main__':
    unittest.main()
