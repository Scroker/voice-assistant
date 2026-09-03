import os
import sys
import json
import xml.etree.ElementTree as ET
import subprocess
import unittest

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
        """Verifica cheprefs.blp sia un file Blueprint valido e compilabile."""
        blp_path = os.path.join(ROOT_DIR, "data", "ui", "prefs.blp")
        res = subprocess.run(
            ["blueprint-compiler", "compile", blp_path],
            capture_output=True, text=True
        )
        self.assertEqual(res.returncode, 0, f"Errore sintassi Blueprint UI:\n{res.stderr}")

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

    def test_mcp_enabled_is_schema_key_and_bound_in_preferences(self):
        schema_path = os.path.join(
            ROOT_DIR, "data", "schemas", "org.gnome.shell.extensions.voice-assistant.gschema.xml"
        )
        schema = ET.parse(schema_path).getroot().find("schema")
        keys = {key.attrib["name"]: key.findtext("default") for key in schema.findall("key")}
        self.assertEqual(keys["mcp-enabled"], "true")

        mcp_js_path = os.path.join(ROOT_DIR, "src", "prefs", "mcp.js")
        with open(mcp_js_path, "r", encoding="utf-8") as source_file:
            source = source_file.read()

        self.assertIn("settings.bind('mcp-enabled', mcpToggle, 'active'", source)

if __name__ == '__main__':
    unittest.main()
