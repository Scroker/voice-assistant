import unittest
import sys
import os
from pathlib import Path

daemon_dir = Path(__file__).resolve().parent.parent / 'src' / 'daemon'
sys.path.insert(0, str(daemon_dir))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

try:
    from daemon.core.logger import ErrorCollector, setup_logger, ERROR_REPORTS_DIR
except ImportError:
    from core.logger import ErrorCollector, setup_logger, ERROR_REPORTS_DIR


class TestLogger(unittest.TestCase):
    def test_setup_logger(self):
        logger = setup_logger("TestLogger")
        self.assertIsNotNone(logger)
        logger.info("Test log line")

    def test_record_error(self):
        try:
            raise ValueError("Test error exception")
        except ValueError as e:
            exc_type, exc_val, tb = sys.exc_info()
            path = ErrorCollector.record_error(exc_type, exc_val, tb, {"test": True})
            self.assertTrue(os.path.exists(path))
            reports = ErrorCollector.list_reports()
            self.assertGreater(len(reports), 0)


if __name__ == '__main__':
    unittest.main()
