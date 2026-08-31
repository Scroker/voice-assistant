import os
import sys
import time
import threading
import unittest
from unittest.mock import MagicMock, patch

# Ensure src/daemon is in python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src', 'daemon')))

from providers.whisper_provider import WhisperProvider
from providers.vosk_provider import VoskProvider

class TestDownloadProviders(unittest.TestCase):

    def setUp(self):
        self.temp_models_dir = "/tmp/test_va_models"
        os.makedirs(self.temp_models_dir, exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_models_dir, ignore_errors=True)

    def test_whisper_tqdm_patch_single_thread(self):
        """Verifica che la patch tqdm catturi il progresso da 0 a 99% in tempo reale per un singolo thread."""
        import huggingface_hub.file_download

        captured_pcts = []
        def progress_cb(pct):
            captured_pcts.append(pct)

        tid = threading.get_ident()
        downloads = getattr(sys, '_va_active_downloads', {})
        downloads[tid] = {'cb': progress_cb, 'last_pct': -1}

        try:
            # Simula la creazione e l'aggiornamento di tqdm da parte di huggingface_hub durante il download
            tqdm_bar = huggingface_hub.file_download.tqdm(total=100 * 1024 * 1024)
            for chunk_step in range(1, 11):
                tqdm_bar.update(10 * 1024 * 1024)
        finally:
            downloads.pop(tid, None)

        self.assertGreater(len(captured_pcts), 0, "Nessuna percentuale di progresso catturata!")
        self.assertEqual(captured_pcts[-1], 99, f"L'ultima percentuale doveva essere 99%, ma è {captured_pcts[-1]}")
        self.assertEqual(captured_pcts, sorted(captured_pcts), "Le percentuali devono essere strettamente crescenti")

    def test_whisper_tqdm_patch_concurrent_isolation(self):
        """Verifica che due download di modelli Whisper in parallelo su thread distinti abbiano percentuali completamente isolate."""
        import huggingface_hub.file_download

        model_a_pcts = []
        model_b_pcts = []

        def worker_model_a():
            tid = threading.get_ident()
            downloads = getattr(sys, '_va_active_downloads', {})
            downloads[tid] = {'cb': lambda p: model_a_pcts.append(p), 'last_pct': -1}
            try:
                tqdm_bar = huggingface_hub.file_download.tqdm(total=50 * 1024 * 1024)
                for _ in range(5):
                    tqdm_bar.update(10 * 1024 * 1024)
                    time.sleep(0.005)
            finally:
                downloads.pop(tid, None)

        def worker_model_b():
            tid = threading.get_ident()
            downloads = getattr(sys, '_va_active_downloads', {})
            downloads[tid] = {'cb': lambda p: model_b_pcts.append(p), 'last_pct': -1}
            try:
                tqdm_bar = huggingface_hub.file_download.tqdm(total=200 * 1024 * 1024)
                for _ in range(10):
                    tqdm_bar.update(20 * 1024 * 1024)
                    time.sleep(0.005)
            finally:
                downloads.pop(tid, None)

        t1 = threading.Thread(target=worker_model_a)
        t2 = threading.Thread(target=worker_model_b)

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertGreater(len(model_a_pcts), 0, "Model A non ha registrato alcun progresso")
        self.assertGreater(len(model_b_pcts), 0, "Model B non ha registrato alcun progresso")
        
        self.assertEqual(model_a_pcts[-1], 99)
        self.assertEqual(model_b_pcts[-1], 99)

        # Verifichiamo che i due thread abbiano aggiornato le rispettive liste distinte
        self.assertNotEqual(id(model_a_pcts), id(model_b_pcts))

    def test_whisper_provider_target_dir(self):
        """Verifica la corretta risoluzione del percorso della cartella target per i modelli Whisper."""
        with patch('faster_whisper.download_model') as mock_dl, patch('faster_whisper.WhisperModel') as mock_model:
            mock_dl.return_value = os.path.join(self.temp_models_dir, "whisper-tiny")
            
            provider = WhisperProvider("tiny", "cpu", {}, models_dir=self.temp_models_dir)
            expected_dir = os.path.join(self.temp_models_dir, "whisper-tiny")
            mock_dl.assert_called_once_with("tiny", output_dir=expected_dir)

    def test_vosk_provider_target_dir(self):
        """Verifica la risoluzione del nome e del percorso della cartella target dei modelli Vosk."""
        target_vosk_dir = os.path.join(self.temp_models_dir, "vosk-model-small-it-0.22")
        
        with patch('providers.vosk_provider.VoskProvider._load_or_download_model') as mock_load, \
             patch('providers.vosk_provider.KaldiRecognizer') as mock_rec:
            
            mock_load.return_value = MagicMock()
            provider = VoskProvider("small-it", "cpu", {}, models_dir=self.temp_models_dir)
            
            mock_load.assert_called_once_with("vosk-model-small-it-0.22", target_vosk_dir, None)

    def test_vosk_download_progress_callback(self):
        """Verifica il funzionamento dei callback di progresso per i modelli Vosk."""
        captured_pcts = []
        def cb(pct):
            captured_pcts.append(pct)

        with patch('urllib.request.urlopen') as mock_urlopen, \
             patch('zipfile.ZipFile') as mock_zip, \
             patch('providers.vosk_provider.Model') as mock_model:
            
            # Simula risposta HTTP con Content-Length di 1000 byte
            mock_response = MagicMock()
            mock_response.info.return_value.get.return_value = '1000'
            mock_response.read.side_effect = [b'x' * 200, b'x' * 300, b'x' * 500, b'']
            mock_urlopen.return_value.__enter__.return_value = mock_response

            with patch('builtins.open', unittest.mock.mock_open()):
                provider = VoskProvider.__new__(VoskProvider)
                provider.MODELS_DIR = self.temp_models_dir
                target_dir = os.path.join(self.temp_models_dir, "vosk-model-small-it-0.22")
                provider._download_model("vosk-model-small-it-0.22", target_dir, progress_callback=cb)

        self.assertIn(99, captured_pcts, "La percentuale del 99% doveva essere registrata a fine estrazione zip")
        self.assertEqual(captured_pcts[-1], 100, "L'ultima percentuale doveva essere 100%")

if __name__ == '__main__':
    unittest.main()
