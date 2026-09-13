import os
import sys
import shutil
import tempfile
import unittest
import numpy as np

# Add src/daemon to import path
daemon_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "daemon"))
if daemon_dir not in sys.path:
    sys.path.insert(0, daemon_dir)

from services.speaker_id.profiles import (
    SpeakerProfileStore,
    sanitize_profile_id,
)


class TestSpeakerProfiles(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="va_test_profiles_")
        self.store = SpeakerProfileStore(self.test_dir, active_backend="resemblyzer")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_sanitize_profile_id(self):
        self.assertEqual(sanitize_profile_id("Mario Rossi"), "mario-rossi")
        self.assertEqual(sanitize_profile_id("Éléonore"), "eleonore")
        self.assertEqual(sanitize_profile_id("User #123 (Main)"), "user-123-main")
        self.assertEqual(sanitize_profile_id("---Special___Chars---"), "special_chars")
        self.assertEqual(sanitize_profile_id(""), "speaker")

    def test_save_and_load_profile(self):
        dim = 256
        rng = np.random.RandomState(42)
        anchor = rng.randn(dim).astype(np.float32)

        pid = self.store.save_enrollment("Test User", anchor)
        self.assertEqual(pid, "test-user")

        profile = self.store.get_profile(pid)
        self.assertIsNotNone(profile)
        self.assertEqual(profile["id"], "test-user")
        self.assertEqual(profile["meta"]["display_name"], "Test User")
        self.assertEqual(profile["meta"]["backend"], "resemblyzer")
        self.assertEqual(profile["history"].shape, (0, dim))

        # Check normalization
        norm = np.linalg.norm(profile["anchor"])
        self.assertAlmostEqual(norm, 1.0, places=5)

    def test_reference_embedding_calculation(self):
        dim = 4
        anchor = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
        pid = self.store.save_enrollment("Ref User", anchor)

        # Without history, ref_emb equals anchor
        ref0 = self.store.get_reference_embedding(pid)
        self.assertIsNotNone(ref0)
        np.testing.assert_allclose(ref0, anchor, atol=1e-5)

        # Add single history vector
        h1 = np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)
        self.store.add_history(pid, h1)

        # With 1 history vector: 0.5 * anchor + 0.5 * h1 = [0.5, 0.5, 0, 0], normalized
        ref1 = self.store.get_reference_embedding(pid)
        expected = np.array([0.5, 0.5, 0.0, 0.0], dtype=np.float32)
        expected = expected / np.linalg.norm(expected)
        np.testing.assert_allclose(ref1, expected, atol=1e-5)

        # Add second history vector: linspace(0.4, 1.0, 2) = [0.4, 1.0] -> sum=1.4
        h2 = np.array([0.0, 0.0, 1.0, 0.0], dtype=np.float32)
        self.store.add_history(pid, h2)

        ref2 = self.store.get_reference_embedding(pid)
        # weights = [0.4/1.4, 1.0/1.4]
        # history_mean = [0, 0.4/1.4, 1.0/1.4, 0]
        # h_norm = np.linalg.norm(history_mean)
        # combined = 0.5 * anchor + 0.5 * (history_mean / h_norm)
        weights = np.linspace(0.4, 1.0, 2)
        h_mean = np.average(np.array([h1, h2]), axis=0, weights=weights / weights.sum())
        combined = 0.5 * anchor + 0.5 * (h_mean / np.linalg.norm(h_mean))
        expected2 = combined / np.linalg.norm(combined)
        np.testing.assert_allclose(ref2, expected2, atol=1e-5)

    def test_max_history_sliding_window(self):
        dim = 4
        anchor = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
        pid = self.store.save_enrollment("Max History User", anchor)

        # Store supports up to 19 history items
        for i in range(25):
            vec = np.array([0.0, float(i), 0.0, 0.0], dtype=np.float32)
            self.store.add_history(pid, vec)

        profile = self.store.get_profile(pid)
        self.assertEqual(len(profile["history"]), 19)

    def test_needs_reenroll_check(self):
        anchor = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
        pid = self.store.save_enrollment("Old Backend User", anchor, backend_name="old_model")

        profiles = self.store.list_profiles()
        self.assertEqual(len(profiles), 1)
        self.assertTrue(profiles[0]["needs_reenroll"])

    def test_delete_profile(self):
        anchor = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
        pid = self.store.save_enrollment("To Delete", anchor)
        self.assertTrue(os.path.exists(os.path.join(self.test_dir, f"{pid}.npz")))

        deleted = self.store.delete_profile(pid)
        self.assertTrue(deleted)
        self.assertFalse(os.path.exists(os.path.join(self.test_dir, f"{pid}.npz")))
        self.assertIsNone(self.store.get_profile(pid))

    def test_gnome_session_user_binding(self):
        from services.speaker_id.profiles import get_gnome_session_user, get_gnome_session_uid

        current_user = get_gnome_session_user()
        current_uid = get_gnome_session_uid()
        self.assertTrue(bool(current_user))

        dim = 8
        anchor1 = np.ones(dim, dtype=np.float32)
        pid1 = self.store.save_enrollment("Session User", anchor1)

        # Retrieve current user profile
        prof1 = self.store.get_current_user_profile()
        self.assertIsNotNone(prof1)
        self.assertEqual(prof1["meta"]["username"], current_user)
        self.assertEqual(prof1["meta"]["gnome_session_user"], current_user)
        if current_uid is not None:
            self.assertEqual(prof1["meta"]["uid"], current_uid)

        # Save profile for a different specific user
        anchor2 = np.ones(dim, dtype=np.float32)
        pid2 = self.store.save_enrollment(
            "Other Account",
            anchor2,
            username="test_other_user",
            uid=9999,
            profile_id="other-account",
        )

        other_prof = self.store.get_user_profile("test_other_user")
        self.assertIsNotNone(other_prof)
        self.assertEqual(other_prof["meta"]["username"], "test_other_user")
        self.assertEqual(other_prof["meta"]["uid"], 9999)

        # Check list_profiles is_current_user flag
        listed = {p["id"]: p for p in self.store.list_profiles()}
        self.assertIn(pid1, listed)
        self.assertIn(pid2, listed)
        self.assertTrue(listed[pid1]["is_current_user"])
        self.assertFalse(listed[pid2]["is_current_user"])


if __name__ == "__main__":
    unittest.main()
