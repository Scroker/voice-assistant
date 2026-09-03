import os
import sys
import subprocess
import unittest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

class TestJSSyntax(unittest.TestCase):

    def test_extension_js_syntax(self):
        """Verifica la sintassi del file src/extension.js."""
        ext_path = os.path.join(ROOT_DIR, "src", "extension.js")
        res = subprocess.run(
            ["node", "--check", ext_path],
            capture_output=True, text=True
        )
        self.assertEqual(res.returncode, 0, f"Errore sintattico in src/extension.js:\n{res.stderr}")

    def test_prefs_js_syntax(self):
        """Verifica la sintassi del file src/prefs.js."""
        prefs_path = os.path.join(ROOT_DIR, "src", "prefs.js")
        res = subprocess.run(
            ["node", "--check", prefs_path],
            capture_output=True, text=True
        )
        self.assertEqual(res.returncode, 0, f"Errore sintattico in src/prefs.js:\n{res.stderr}")

    def test_no_deprecated_initgettext(self):
        """Verifica che initGettext non sia invocato né importato in alcun file JS (gestito in automatico da GNOME Shell 45+ via metadata.json)."""
        for filename in ["extension.js", "prefs.js"]:
            filepath = os.path.join(ROOT_DIR, "src", filename)
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
                self.assertNotIn("initGettext", content,
                                 f"Uso deprecato di 'initGettext' trovato in {filename}")

    def test_quick_toggle_is_owned_by_extension(self):
        """Il toggle deve sopravvivere indipendentemente dal SystemIndicator."""
        filepath = os.path.join(ROOT_DIR, "src", "extension.js")
        with open(filepath, "r", encoding="utf-8") as source_file:
            source = source_file.read()

        self.assertNotIn("this.quickSettingsItems.push(this._toggle)", source)
        self.assertIn("this._quickToggle = new VoiceAssistantQuickToggle(this);", source)
        self.assertIn("this._quickToggle.destroy();", source)

if __name__ == '__main__':
    unittest.main()
