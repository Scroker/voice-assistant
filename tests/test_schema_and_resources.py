import os
import sys
import json
import xml.etree.ElementTree as ET
import subprocess
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

class TestSchemaAndResources(unittest.TestCase):

    def test_gsettings_schema_validity(self):
        """Verifica che lo schema GSettings sia sintatticamente corretto e privo di errori."""
        schema_dir = os.path.join(ROOT_DIR, "data", "schemas")
        res = subprocess.run(
            ["glib-compile-schemas", "--strict", "--dry-run", schema_dir],
            capture_output=True, text=True
        )
        self.assertEqual(res.returncode, 0, f"Errore sintassi schema GSettings:\n{res.stderr}")

    def test_metadata_json_validity(self):
        """Verifica la correttezza del file metadata.json.in."""
        meta_path = os.path.join(ROOT_DIR, "data", "metadata.json.in")
        with open(meta_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        required_keys = ["uuid", "name", "description", "gettext-domain", "settings-schema", "shell-version"]
        for key in required_keys:
            self.assertIn(key, data, f"Chiave mancante in metadata.json.in: {key}")

        self.assertEqual(data["uuid"], "voice-assistant@scroker.github.io")
        self.assertEqual(data["gettext-domain"], "voice-assistant")

    def test_blueprint_ui_syntax(self):
        """Verifica che prefs.blp e assistant_window.blp siano file Blueprint validi e compilabili."""
        if not shutil.which("blueprint-compiler"):
            self.skipTest("blueprint-compiler non installato nel sistema")
        env = dict(os.environ)
        # Assicura reperibilità di blueprintcompiler anche se HOME è temporaneo nei test
        candidates = [
            "/home/giorgiodramis/.local/lib/python3.14/site-packages",
        ]
        for c in candidates:
            if os.path.isdir(c):
                curr_pp = env.get("PYTHONPATH", "")
                env["PYTHONPATH"] = f"{c}:{curr_pp}" if curr_pp else c
                break
        for blp_name in ("prefs.blp", "assistant_window.blp"):
            blp_path = os.path.join(ROOT_DIR, "data", "ui", blp_name)
            res = subprocess.run(
                ["blueprint-compiler", "compile", blp_path],
                capture_output=True, text=True, env=env,
            )
            self.assertEqual(res.returncode, 0, f"Errore sintassi Blueprint UI ({blp_name}):\n{res.stderr}")

    def test_blueprint_compiled_matches_ui_files(self):
        """Verifica che i file .ui nel repository siano esattamente allineati con i .blp compilati."""
        if not shutil.which("blueprint-compiler"):
            self.skipTest("blueprint-compiler non installato nel sistema")

        env = dict(os.environ)
        candidates = [
            "/home/giorgiodramis/.local/lib/python3.14/site-packages",
        ]
        for c in candidates:
            if os.path.isdir(c):
                curr_pp = env.get("PYTHONPATH", "")
                env["PYTHONPATH"] = f"{c}:{curr_pp}" if curr_pp else c
                break

        with tempfile.TemporaryDirectory() as tmpdir:
            for name in ("prefs", "assistant_window"):
                blp_path = os.path.join(ROOT_DIR, "data", "ui", f"{name}.blp")
                ui_repo_path = os.path.join(ROOT_DIR, "data", "ui", f"{name}.ui")
                out_path = os.path.join(tmpdir, f"{name}.ui")

                res = subprocess.run(
                    ["blueprint-compiler", "compile", "--output", out_path, blp_path],
                    capture_output=True, text=True, env=env,
                )
                self.assertEqual(res.returncode, 0, f"Compilazione {name}.blp fallita:\n{res.stderr}")

                with open(out_path, "r", encoding="utf-8") as f:
                    compiled_content = f.read().strip()
                with open(ui_repo_path, "r", encoding="utf-8") as f:
                    repo_content = f.read().strip()

                self.assertEqual(
                    compiled_content,
                    repo_content,
                    f"Il file data/ui/{name}.ui è disallineato rispetto a {name}.blp."
                )

    def test_po_file_syntax(self):
        """Verifica che il file delle traduzioni po/it.po sia sintatticamente valido."""
        po_path = os.path.join(ROOT_DIR, "po", "it.po")
        res = subprocess.run(
            ["msgfmt", "-c", "-o", "/dev/null", po_path],
            capture_output=True, text=True
        )
        self.assertEqual(res.returncode, 0, f"Errore nel file PO delle traduzioni:\n{res.stderr}")

    def test_dbus_xml_interface(self):
        """Verifica che l'interfaccia D-Bus XML sia un file XML ben formato."""
        dbus_xml = os.path.join(ROOT_DIR, "data", "dbus", "org.local.VoiceAssistant.xml")
        tree = ET.parse(dbus_xml)
        root = tree.getroot()
        self.assertEqual(root.tag, "node")
        iface = root.find("interface")
        self.assertIsNotNone(iface)
        self.assertEqual(iface.attrib.get("name"), "org.local.VoiceAssistant")
        self.assertIsNotNone(iface.find("method[@name='GetResourceMetrics']"))

    def test_model_idle_timeout_keys(self):
        schema_path = os.path.join(
            ROOT_DIR, "data", "schemas", "org.gnome.shell.extensions.voice-assistant.gschema.xml"
        )
        schema = ET.parse(schema_path).getroot().find("schema")
        keys = {key.attrib["name"]: key.findtext("default") for key in schema.findall("key")}

        self.assertEqual(keys["idle-unload-timeout"], "300")
        self.assertEqual(keys["stt-idle-unload-timeout"], "0")
        self.assertEqual(keys["llm-idle-unload-timeout"], "180")
        self.assertEqual(keys["tts-idle-unload-timeout"], "0")
        self.assertEqual(keys["mcp-registry-url"], "'https://api.smithery.ai'")

    def test_mcp_enabled_is_schema_key_and_bound_in_assistant_window(self):
        schema_path = os.path.join(
            ROOT_DIR, "data", "schemas", "org.gnome.shell.extensions.voice-assistant.gschema.xml"
        )
        schema = ET.parse(schema_path).getroot().find("schema")
        keys = {key.attrib["name"]: key.findtext("default") for key in schema.findall("key")}
        self.assertEqual(keys["mcp-enabled"], "true")

        blp_path = os.path.join(ROOT_DIR, "data", "ui", "assistant_window.blp")
        if os.path.exists(blp_path):
            with open(blp_path, "r", encoding="utf-8") as source_file:
                source = source_file.read()
            self.assertIn("mcp", source.lower())

        prefs_path = os.path.join(ROOT_DIR, "data", "ui", "prefs.blp")
        if os.path.exists(prefs_path):
            with open(prefs_path, "r", encoding="utf-8") as source_file:
                source = source_file.read()
            self.assertNotIn("mcp", source.lower())

    def test_dbus_contracts_alignment(self):
        """Verifica che D-Bus XML, extension.js e metodi/segnali pubblici di main.py abbiano gli stessi membri."""
        import re
        dbus_xml = os.path.join(ROOT_DIR, "data", "dbus", "org.local.VoiceAssistant.xml")
        tree = ET.parse(dbus_xml)
        xml_methods = {m.attrib["name"] for m in tree.findall(".//method")}
        xml_signals = {s.attrib["name"] for s in tree.findall(".//signal")}

        ext_js = os.path.join(ROOT_DIR, "src", "extension.js")
        with open(ext_js, "r", encoding="utf-8") as f:
            content = f.read()
        m = re.search(r"<node>.*?</node>", content, re.DOTALL)
        self.assertIsNotNone(m, "Frammento XML non trovato in extension.js")
        js_xml = m.group(0)
        js_tree = ET.fromstring(js_xml)
        js_methods = {meth.attrib["name"] for meth in js_tree.findall(".//method")}
        js_signals = {sig.attrib["name"] for sig in js_tree.findall(".//signal")}

        self.assertEqual(xml_methods, js_methods, "Disallineamento metodi tra D-Bus XML ed extension.js")
        self.assertEqual(xml_signals, js_signals, "Disallineamento segnali tra D-Bus XML ed extension.js")

        daemon_dir = os.path.join(ROOT_DIR, "src", "daemon")
        if daemon_dir not in sys.path:
            sys.path.insert(0, daemon_dir)
        from main import VoiceAssistant
        main_public = {attr for attr in dir(VoiceAssistant) if attr[0].isupper() and not attr.startswith("_")}
        all_xml = xml_methods | xml_signals
        self.assertEqual(main_public, all_xml, "Disallineamento membri tra D-Bus XML e VoiceAssistant in main.py")


if __name__ == '__main__':
    unittest.main()
