import threading
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
    from daemon.core.logger import (
        ErrorCollector, EnvironmentSnapshot, DiagnosticBundler, setup_logger,
        ERROR_REPORTS_DIR, glib_safe, make_asyncio_exception_handler,
        install_global_exception_hooks,
    )
except ImportError:
    from core.logger import (
        ErrorCollector, EnvironmentSnapshot, DiagnosticBundler, setup_logger,
        ERROR_REPORTS_DIR, glib_safe, make_asyncio_exception_handler,
        install_global_exception_hooks,
    )


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


class TestGlibSafe(unittest.TestCase):
    def test_normal_call_passes_through(self):
        results = []
        wrapped = glib_safe(lambda x: results.append(x), "test")
        wrapped(42)
        self.assertEqual(results, [42])

    def test_exception_is_caught_and_returns_false(self):
        def boom(x):
            raise RuntimeError("intentional")

        wrapped = glib_safe(boom, "test_boom")
        ret = wrapped(1)
        self.assertIs(ret, False)

    def test_preserves_function_name(self):
        def my_callback():
            pass

        wrapped = glib_safe(my_callback)
        self.assertEqual(wrapped.__name__, "my_callback")

    def test_exception_recorded(self):
        before = len(ErrorCollector.list_reports())

        def crasher():
            raise ValueError("glib_safe test crash")

        glib_safe(crasher, "test_crasher")()
        after = len(ErrorCollector.list_reports())
        self.assertGreater(after, before)


class TestAsyncioExceptionHandler(unittest.TestCase):
    def test_returns_callable(self):
        handler = make_asyncio_exception_handler("test_component")
        self.assertTrue(callable(handler))

    def test_handler_records_exception(self):
        import asyncio
        handler = make_asyncio_exception_handler("test_asyncio")
        loop = asyncio.new_event_loop()
        before = len(ErrorCollector.list_reports())
        try:
            exc = RuntimeError("asyncio handler test")
            handler(loop, {"exception": exc, "message": "test message"})
        finally:
            loop.close()
        after = len(ErrorCollector.list_reports())
        self.assertGreater(after, before)

    def test_handler_logs_message_only(self):
        import asyncio
        handler = make_asyncio_exception_handler("test_asyncio_msg")
        loop = asyncio.new_event_loop()
        try:
            # Should not raise even with no exception in context
            handler(loop, {"message": "context without exception"})
        finally:
            loop.close()


class TestInstallGlobalHooks(unittest.TestCase):
    def test_installs_sys_excepthook(self):
        original = sys.excepthook
        try:
            install_global_exception_hooks()
            self.assertIsNot(sys.excepthook, original)
        finally:
            sys.excepthook = original

    def test_installs_threading_excepthook(self):
        original = threading.excepthook
        try:
            install_global_exception_hooks()
            self.assertIsNot(threading.excepthook, original)
        finally:
            threading.excepthook = original


if __name__ == '__main__':
    unittest.main()
