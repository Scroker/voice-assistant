import unittest
from pathlib import Path
import sys
import os

ROOT_DIR = Path(__file__).resolve().parent.parent
DAEMON_DIR = ROOT_DIR / "src" / "daemon"
GUI_DIR = ROOT_DIR / "src" / "gui"

if str(DAEMON_DIR) not in sys.path:
    sys.path.insert(0, str(DAEMON_DIR))
if str(ROOT_DIR / "src") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "src"))

from core.data_loader import load_json_data, load_text_data, resolve_data_path
from mcp.manager import MCPManager, GNOME_MCP_TOOLS_DEFAULTS
from mcp.config import get_default_config, DEFAULT_CONFIG
from core.runtime_manager import _OPTIONAL_PYTHON_DEPS, _SYSTEM_DEPS
from gui.dependency_installer import detect_system_package_manager


class TestDataLoader(unittest.TestCase):

    def test_resolve_data_path(self):
        p = resolve_data_path("mcp/tools_gnome_defaults.json")
        self.assertIsNotNone(p)
        self.assertTrue(p.exists())

    def test_load_tools_gnome_defaults(self):
        tools = load_json_data("mcp/tools_gnome_defaults.json")
        self.assertIsInstance(tools, dict)
        self.assertEqual(len(tools), 10)
        self.assertIn("set_volume", tools)
        self.assertIn("quick_settings", tools)
        self.assertIn("launch_application", tools)
        self.assertIn("media_control", tools)
        self.assertIn("send_notification", tools)
        self.assertIn("open_file", tools)
        self.assertIn("set_wallpaper", tools)
        self.assertIn("take_screenshot", tools)
        self.assertIn("window_management", tools)
        self.assertIn("keyring_management", tools)

    def test_mcp_manager_default_tools(self):
        manager = MCPManager()
        self.assertIsInstance(manager.default_tools, dict)
        self.assertEqual(len(manager.default_tools), 10)
        self.assertIn("set_volume", manager.default_tools)

        # Verifica retrocompatibilità import modulo
        self.assertIsInstance(GNOME_MCP_TOOLS_DEFAULTS, dict)
        self.assertEqual(len(GNOME_MCP_TOOLS_DEFAULTS), 10)

    def test_load_default_servers(self):
        cfg = get_default_config()
        self.assertIn("mcpServers", cfg)
        self.assertIn("gnome-mcp-server", cfg["mcpServers"])
        gnome = cfg["mcpServers"]["gnome-mcp-server"]
        self.assertEqual(gnome["command"], "gnome-mcp-server")
        self.assertEqual(gnome["builder"], "cargo")
        self.assertTrue(gnome["enabled"])

    def test_load_system_deps(self):
        self.assertIsInstance(_SYSTEM_DEPS, list)
        self.assertGreaterEqual(len(_SYSTEM_DEPS), 4)
        pkg_names = [d["package"] for d in _SYSTEM_DEPS]
        self.assertIn("portaudio", pkg_names)
        self.assertIn("espeak-ng", pkg_names)

    def test_load_python_deps(self):
        self.assertIsInstance(_OPTIONAL_PYTHON_DEPS, list)
        self.assertGreaterEqual(len(_OPTIONAL_PYTHON_DEPS), 7)
        import_names = [d[0] for d in _OPTIONAL_PYTHON_DEPS]
        self.assertIn("vosk", import_names)
        self.assertIn("piper", import_names)
        self.assertIn("faster_whisper", import_names)

    def test_load_fallback_non_existent(self):
        fallback = {"safe": True}
        res = load_json_data("non_existent_folder/missing.json", fallback_default=fallback)
        self.assertEqual(res, fallback)

    def test_package_managers_loaded(self):
        pms = load_json_data("dependencies/package_managers.json", fallback_default=[])
        self.assertIsInstance(pms, list)
        self.assertGreater(len(pms), 0)
        names = [p["name"] for p in pms]
        self.assertIn("dnf", names)
        self.assertIn("apt", names)
        self.assertIn("pacman", names)


if __name__ == "__main__":
    unittest.main()
