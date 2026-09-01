import unittest
import sys
import os
import tarfile
import json
from pathlib import Path

daemon_dir = Path(__file__).resolve().parent.parent / 'src' / 'daemon'
sys.path.insert(0, str(daemon_dir))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

try:
    from daemon.core.logger import ErrorCollector, EnvironmentSnapshot, DiagnosticBundler, setup_logger, ERROR_REPORTS_DIR
except ImportError:
    from core.logger import ErrorCollector, EnvironmentSnapshot, DiagnosticBundler, setup_logger, ERROR_REPORTS_DIR


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

    def test_environment_snapshot(self):
        env = EnvironmentSnapshot.collect()
        self.assertIn("os", env)
        self.assertIn("python_version", env)
        self.assertIn("ram_total_mb", env)

    def test_diagnostic_bundler(self):
        # Genera report per assicurarsi che ci sia qualcosa da inserire nel bundle
        try:
            raise RuntimeError("Bundle test exception")
        except RuntimeError:
            ErrorCollector.record_error(*sys.exc_info(), component="TestBundle")

        bundle_path = DiagnosticBundler.generate(state="test_idle")
        self.assertTrue(os.path.exists(bundle_path))
        self.assertTrue(bundle_path.endswith(".tar.gz"))

        # Verifica contenuto del tarball
        with tarfile.open(bundle_path, "r:gz") as tar:
            members = [m.name for m in tar.getmembers()]
            self.assertTrue(any(m.endswith("environment.json") for m in members))
            self.assertTrue(any(m.endswith("voice-assistant.log") for m in members))


if __name__ == '__main__':
    unittest.main()
