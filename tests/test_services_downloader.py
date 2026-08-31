import unittest
import sys
import time
from pathlib import Path

daemon_dir = Path(__file__).resolve().parent.parent / 'src' / 'daemon'
sys.path.insert(0, str(daemon_dir))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

try:
    from daemon.services.downloader import ModelDownloader
except ImportError:
    from services.downloader import ModelDownloader


class TestServicesDownloader(unittest.TestCase):
    def test_downloader_lifecycle(self):
        reports = []
        def progress_cb(prov, model, pct):
            reports.append((prov, model, pct))

        downloader = ModelDownloader(emit_progress_cb=progress_cb)

        def mock_download(cb):
            cb(25)
            time.sleep(0.05)
            cb(75)

        t = downloader.start_download("whisper", "tiny", mock_download)
        t.join(timeout=2.0)

        self.assertFalse(downloader.is_downloading("whisper", "tiny"))
        self.assertIn(("whisper", "tiny", 100), reports)


if __name__ == '__main__':
    unittest.main()
