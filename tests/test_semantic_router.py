import os
import sys
import time
import unittest

daemon_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "daemon"))
if daemon_dir not in sys.path:
    sys.path.insert(0, daemon_dir)

from skills.semantic_router import SemanticIntentRouter


def _make_router():
    # auto_download=True so the test can populate the cache on first run when a
    # network is available; if it still isn't available afterwards, the tests
    # below skip rather than fail (this is a model-download availability
    # concern, not a router correctness one).
    return SemanticIntentRouter(auto_download=True)


class TestSemanticIntentRouter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.router = _make_router()
        if not cls.router.available:
            raise unittest.SkipTest(
                "ONNX embedding model unavailable (offline and not cached) — "
                "skipping semantic router tests, bag-of-words fallback is covered by test_semantic_dispatch.py"
            )

    def test_matches_colloquial_volume_phrase(self):
        result = self.router.match("alza un po' il volume")
        self.assertIsNotNone(result)
        self.assertEqual(result["intent"], "volume_up")

    def test_matches_stt_garbled_launch_app(self):
        result = self.router.match("o aprì calendario")
        self.assertIsNotNone(result)
        self.assertEqual(result["intent"], "launch_app")

    def test_matches_colloquial_media_play(self):
        result = self.router.match("fammi ascoltare un po' di musica")
        self.assertIsNotNone(result)
        self.assertEqual(result["intent"], "media_play")

    def test_rejects_unrelated_conversational_phrase(self):
        self.assertIsNone(self.router.match("spiegami la relatività"))
        self.assertIsNone(self.router.match("chi era napoleone"))

    def test_rejects_empty_text(self):
        self.assertIsNone(self.router.match(""))
        self.assertIsNone(self.router.match(None))

    def test_latency_under_target(self):
        # Warm up (first call pays one-off numpy/onnxruntime setup cost).
        self.router.match("alza il volume")

        samples = 20
        start = time.perf_counter()
        for _ in range(samples):
            self.router.match("metti in pausa la musica")
        elapsed_ms = (time.perf_counter() - start) * 1000 / samples

        self.assertLess(elapsed_ms, 15.0, f"Average match() latency {elapsed_ms:.2f}ms exceeds 15ms target")


class TestSemanticIntentRouterFallback(unittest.TestCase):
    def test_falls_back_to_bag_of_words_when_model_missing(self):
        router = SemanticIntentRouter(model_dir="/nonexistent/path/for/testing", auto_download=False)
        self.assertFalse(router.available)

        # The bag-of-words fallback (VectorIntentMatcher) should still work for
        # an exact trigger phrase, so a missing/undownloaded model degrades
        # gracefully instead of breaking the Fast-Path entirely.
        result = router.match("alza il volume")
        self.assertIsNotNone(result)
        self.assertEqual(result["intent"], "volume_up")


if __name__ == "__main__":
    unittest.main()
