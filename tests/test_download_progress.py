import os
import sys
import unittest
import threading
import time

# Aggiunge src/daemon al path di importazione
daemon_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "daemon"))
if daemon_dir not in sys.path:
    sys.path.insert(0, daemon_dir)

from providers.whisper_provider import WhisperProvider, setup_tqdm_patch

class TestDownloadProgress(unittest.TestCase):

    def setUp(self):
        setup_tqdm_patch()

    def test_single_whisper_download_progress_mock(self):
        """Verifica che un singolo download aggiorni la percentuale progressivamente."""
        import faster_whisper.utils

        received_pcts = []
        tid = threading.get_ident()
        sys._va_active_downloads[tid] = {'cb': lambda p: received_pcts.append(p), 'last_pct': -1}

        try:
            # Simula download di un file da 100 MB
            bar = faster_whisper.utils.disabled_tqdm(total=100 * 1024 * 1024)
            for _ in range(10):
                bar.update(10 * 1024 * 1024)
        finally:
            sys._va_active_downloads.pop(tid, None)

        expected = [10, 20, 30, 40, 50, 60, 70, 80, 90, 99]
        self.assertEqual(received_pcts, expected, f"Progresso atteso {expected}, ottenuto {received_pcts}")

    def test_concurrent_whisper_download_progress_isolation(self):
        """Verifica l'isolamento totale del progresso tra due thread di download concorrenti."""
        import faster_whisper.utils

        pcts_thread1 = []
        pcts_thread2 = []

        def worker1():
            tid = threading.get_ident()
            sys._va_active_downloads[tid] = {'cb': lambda p: pcts_thread1.append(p), 'last_pct': -1}
            try:
                # Simula download di 100 MB per Thread 1 (step da 10 MB = +10%)
                bar = faster_whisper.utils.disabled_tqdm(total=100 * 1024 * 1024)
                for _ in range(10):
                    bar.update(10 * 1024 * 1024)
                    time.sleep(0.01)
            finally:
                sys._va_active_downloads.pop(tid, None)

        def worker2():
            tid = threading.get_ident()
            sys._va_active_downloads[tid] = {'cb': lambda p: pcts_thread2.append(p), 'last_pct': -1}
            try:
                # Simula download di 50 MB per Thread 2 (step da 10 MB = +20%)
                bar = faster_whisper.utils.disabled_tqdm(total=50 * 1024 * 1024)
                for _ in range(5):
                    bar.update(10 * 1024 * 1024)
                    time.sleep(0.01)
            finally:
                sys._va_active_downloads.pop(tid, None)

        t1 = threading.Thread(target=worker1)
        t2 = threading.Thread(target=worker2)

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(pcts_thread1, [10, 20, 30, 40, 50, 60, 70, 80, 90, 99])
        self.assertEqual(pcts_thread2, [20, 40, 60, 80, 99])

    def test_ignore_small_metadata_files(self):
        """Verifica che file metadati piccoli (< 5MB) vengano ignorati per evitare falsi 100%."""
        import faster_whisper.utils

        received_pcts = []
        tid = threading.get_ident()
        sys._va_active_downloads[tid] = {'cb': lambda p: received_pcts.append(p), 'last_pct': -1}

        try:
            # Simula file piccolo (es. config.json da 2 KB)
            bar = faster_whisper.utils.disabled_tqdm(total=2 * 1024)
            bar.update(2 * 1024)
        finally:
            sys._va_active_downloads.pop(tid, None)

        self.assertEqual(len(received_pcts), 0, "I file di metadati < 5MB non devono generare callback di progresso")

    def test_active_downloads_cleanup(self):
        """Verifica che la mappa dei download attivi venga pulita correttamente."""
        tid = threading.get_ident()
        sys._va_active_downloads[tid] = {'cb': lambda p: None, 'last_pct': -1}
        self.assertIn(tid, sys._va_active_downloads)
        
        sys._va_active_downloads.pop(tid, None)
        self.assertNotIn(tid, sys._va_active_downloads)

if __name__ == '__main__':
    unittest.main()
