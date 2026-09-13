import os
import sys
import unittest

daemon_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "daemon"))
if daemon_dir not in sys.path:
    sys.path.insert(0, daemon_dir)

from skills.app_slot_matcher import AppSlotMatcher

APPLICATIONS_DIR = "/usr/share/applications"


@unittest.skipUnless(
    os.path.isdir(APPLICATIONS_DIR) and os.listdir(APPLICATIONS_DIR),
    "No system .desktop entries available in this environment",
)
class TestAppSlotMatcher(unittest.TestCase):
    def setUp(self):
        self.matcher = AppSlotMatcher()

    def test_matches_italian_localized_name(self):
        result = self.matcher.match("calcolatrice")
        if result is None:
            self.skipTest("GNOME Calculator not installed in this environment")
        # AppSlotMatcher prefers the Italian Name[it] as display name when present.
        self.assertIn("calcol", result["name"].lower())

    def test_matches_settings_localized_name(self):
        result = self.matcher.match("impostazioni")
        if result is None:
            self.skipTest("GNOME Settings not installed in this environment")
        self.assertTrue(result["desktop_id"].endswith(".desktop"))

    def test_rejects_gibberish(self):
        result = self.matcher.match("xyzxyzxyz_not_an_app_qwerty")
        self.assertIsNone(result)

    def test_rejects_empty_query(self):
        self.assertIsNone(self.matcher.match(""))
        self.assertIsNone(self.matcher.match(None))

    def test_short_query_does_not_match_unrelated_long_name(self):
        # Regression test: fuzz.WRatio previously scored "file" higher against
        # "Color Profile Viewer" (which contains "file" as a substring of
        # "Profile") than against the actual "File"/"Files" app name.
        result = self.matcher.match("file")
        if result is None:
            self.skipTest("No file-manager-like app installed in this environment")
        self.assertNotIn("profile", result["name"].lower())


    def test_matches_mail_aliases(self):
        # "posta" must resolve to default mail client (e.g. Evolution) and NOT to "Pods"
        for alias in ("posta", "mail", "email", "posta elettronica"):
            result = self.matcher.match(alias)
            if result:
                self.assertNotIn("pods", result["name"].lower())
                self.assertNotIn("pods", result["desktop_id"].lower())
                self.assertEqual(result["score"], 100.0)


if __name__ == "__main__":
    unittest.main()
