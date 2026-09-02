import os
import sys
import unittest

daemon_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "daemon"))
if daemon_dir not in sys.path:
    sys.path.insert(0, daemon_dir)

from core.pipeline import FastPathDispatcher
from skills.skill_registry import SkillRegistry
from skills.vector_intent_matcher import VectorIntentMatcher


class TestSemanticDispatch(unittest.TestCase):
    def test_vector_intent_matcher_matches_colloquial_phrases(self):
        registry = SkillRegistry.from_default_directory()
        matcher = VectorIntentMatcher(registry)

        result = matcher.match("metti più forte il volume")
        self.assertIsNotNone(result)
        self.assertEqual(result["intent"], "volume_up")
        self.assertGreater(result["score"], 0.35)

    def test_vector_intent_matcher_matches_theme_variants(self):
        registry = SkillRegistry.from_default_directory()
        matcher = VectorIntentMatcher(registry)

        result = matcher.match("voglio il tema scuro")
        self.assertIsNotNone(result)
        self.assertEqual(result["intent"], "set_theme_dark")
        self.assertGreater(result["score"], 0.35)

    def test_fast_path_dispatch_falls_back_to_semantic_matching(self):
        dispatcher = FastPathDispatcher()

        matched, intent, params, response = dispatcher.dispatch("ammonta un po' il volume")
        self.assertTrue(matched)
        self.assertEqual(intent, "volume_up")
        self.assertIn("volume", params)

    def test_skill_registry_loads_default_skills(self):
        registry = SkillRegistry.from_default_directory()
        self.assertGreater(len(registry.skills), 0)
        self.assertIn("volume_up", {skill["intent"] for skill in registry.skills})


if __name__ == "__main__":
    unittest.main()
