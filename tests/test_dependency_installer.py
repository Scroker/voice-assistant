import unittest
from unittest.mock import MagicMock, patch
import os
import sys

# Aggiungi percorsi src/gui e src/daemon
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
GUI_DIR = os.path.join(ROOT_DIR, "src", "gui")
DAEMON_DIR = os.path.join(ROOT_DIR, "src", "daemon")
if GUI_DIR not in sys.path:
    sys.path.insert(0, GUI_DIR)
if DAEMON_DIR not in sys.path:
    sys.path.insert(0, DAEMON_DIR)

from dependency_installer import (
    detect_system_package_manager,
    _resolve_system_package,
    _install_system_packages,
    _install_pip_packages,
    _install_mcp_packages,
    _start_mcp_servers,
    show_missing_deps_dialog,
)


class TestDependencyInstaller(unittest.TestCase):

    def test_detect_system_package_manager_dnf5(self):
        with patch("shutil.which", side_effect=lambda x: "/usr/bin/" + x if x == "dnf5" else None):
            pm_name, pm_cmd = detect_system_package_manager()
            self.assertEqual(pm_name, "dnf5")
            self.assertEqual(pm_cmd, ["pkexec", "dnf5", "install", "-y"])

    def test_detect_system_package_manager_dnf(self):
        with patch("shutil.which", side_effect=lambda x: "/usr/bin/" + x if x == "dnf" else None):
            pm_name, pm_cmd = detect_system_package_manager()
            self.assertEqual(pm_name, "dnf")
            self.assertEqual(pm_cmd, ["pkexec", "dnf", "install", "-y"])

    def test_detect_system_package_manager_apt(self):
        with patch("shutil.which", side_effect=lambda x: "/usr/bin/" + x if x == "apt-get" else None):
            pm_name, pm_cmd = detect_system_package_manager()
            self.assertEqual(pm_name, "apt")
            self.assertEqual(pm_cmd, ["pkexec", "apt-get", "install", "-y"])

    def test_detect_system_package_manager_pacman(self):
        with patch("shutil.which", side_effect=lambda x: "/usr/bin/" + x if x == "pacman" else None):
            pm_name, pm_cmd = detect_system_package_manager()
            self.assertEqual(pm_name, "pacman")
            self.assertEqual(pm_cmd, ["pkexec", "pacman", "-S", "--noconfirm"])

    def test_resolve_system_package(self):
        dep = {
            "package": "portaudio",
            "system_packages": {
                "dnf": "portaudio",
                "apt": "libportaudio2",
                "pacman": "portaudio",
            }
        }
        self.assertEqual(_resolve_system_package(dep, "dnf"), "portaudio")
        self.assertEqual(_resolve_system_package(dep, "dnf5"), "portaudio")
        self.assertEqual(_resolve_system_package(dep, "apt"), "libportaudio2")
        self.assertEqual(_resolve_system_package(dep, "pacman"), "portaudio")
        self.assertEqual(_resolve_system_package(dep, "unknown"), "portaudio")

    def test_install_system_packages_pkexec(self):
        system_deps = [
            {
                "package": "portaudio",
                "type": "system",
                "system_packages": {"dnf": "portaudio"}
            }
        ]
        with patch("dependency_installer.detect_system_package_manager", return_value=("dnf", ["pkexec", "dnf", "install", "-y"])), \
             patch("shutil.which", return_value="/usr/bin/pkexec"), \
             patch("subprocess.run") as mock_run:

            mock_res = MagicMock()
            mock_res.returncode = 0
            mock_run.return_value = mock_res

            success, msg = _install_system_packages(system_deps)
            self.assertTrue(success)
            mock_run.assert_called_once_with(["pkexec", "dnf", "install", "-y", "portaudio"], capture_output=True, text=True)

    def test_install_system_packages_pkexec_cancelled(self):
        system_deps = [{"package": "cargo", "type": "system", "system_packages": {"dnf5": "cargo"}}]
        with patch("dependency_installer.detect_system_package_manager", return_value=("dnf5", ["pkexec", "dnf5", "install", "-y"])), \
             patch("shutil.which", return_value="/usr/bin/pkexec"), \
             patch("subprocess.run") as mock_run:

            mock_res = MagicMock()
            mock_res.returncode = 126
            mock_run.return_value = mock_res

            success, msg = _install_system_packages(system_deps)
            self.assertFalse(success)
            self.assertIn("annullata", msg.lower())

    def test_install_system_packages_no_pkexec(self):
        system_deps = [{"package": "cargo", "type": "system"}]
        with patch("dependency_installer.detect_system_package_manager", return_value=("dnf5", ["pkexec", "dnf5", "install", "-y"])), \
             patch("shutil.which", return_value=None):

            success, msg = _install_system_packages(system_deps)
            self.assertFalse(success)
            self.assertIn("sudo", msg)

    def test_install_pip_packages(self):
        pip_deps = [{"package": "sherpa-onnx", "type": "pip"}]
        with patch("subprocess.run") as mock_run:
            mock_res = MagicMock()
            mock_res.returncode = 0
            mock_run.return_value = mock_res

            success, msg = _install_pip_packages(pip_deps)
            self.assertTrue(success)
            mock_run.assert_called_once()
            cmd_run = mock_run.call_args[0][0]
            self.assertIn("sherpa-onnx", cmd_run)
            self.assertIn("--prefer-binary", cmd_run)

    def test_install_mcp_packages_cargo(self):
        mcp_deps = [{"package": "gnome-mcp-server", "type": "mcp"}]
        with patch("shutil.which", return_value="/usr/bin/cargo"), \
             patch("subprocess.run") as mock_run:
            mock_res = MagicMock()
            mock_res.returncode = 0
            mock_run.return_value = mock_res

            success, msg = _install_mcp_packages(mcp_deps)
            self.assertTrue(success)
            mock_run.assert_called_once()
            cmd_run = mock_run.call_args[0][0]
            self.assertIn("cargo", cmd_run[0])
            self.assertIn("install", cmd_run)
            self.assertIn("https://github.com/bilelmoussaoui/gnome-mcp-server", cmd_run)

    def test_install_mcp_packages_no_cargo(self):
        mcp_deps = [{"package": "gnome-mcp-server", "type": "mcp"}]
        with patch("shutil.which", return_value=None), \
             patch("os.path.isfile", return_value=False):

            success, msg = _install_mcp_packages(mcp_deps)
            self.assertFalse(success)
            self.assertIn("cargo non trovato", msg.lower())

    def test_start_mcp_servers_dbus(self):
        mcp_deps = [{"package": "gnome-mcp-server", "type": "mcp"}]
        with patch("gi.repository.Gio.bus_get_sync") as mock_bus_get:
            mock_bus = MagicMock()
            mock_bus_get.return_value = mock_bus
            mock_res = MagicMock()
            mock_res.unpack.return_value = (True, "Server avviato")
            mock_bus.call_sync.return_value = mock_res

            success, msg = _start_mcp_servers(mcp_deps)
            self.assertTrue(success)
            mock_bus.call_sync.assert_called_once()
            self.assertEqual(mock_bus.call_sync.call_args[0][3], "StartMCPServer")

    def test_show_missing_deps_dialog_auto_start(self):
        deps = [
            {"package": "cargo", "type": "system", "system_packages": {"dnf": "cargo"}, "mcp_server": "gnome-mcp-server"},
            {"package": "gnome-mcp-server", "type": "mcp"},
        ]
        mock_parent = MagicMock()
        with patch("dependency_installer._run_install") as mock_run_install:
            show_missing_deps_dialog(mock_parent, deps, auto_start=True)
            mock_run_install.assert_called_once()
            args = mock_run_install.call_args[0]
            kwargs = mock_run_install.call_args[1]
            self.assertEqual(args[0], [deps[0]])  # system_deps
            self.assertEqual(args[1], [])          # pip_deps
            self.assertEqual(args[2], mock_parent)
            self.assertEqual(kwargs.get("mcp_deps"), [deps[1]])


if __name__ == '__main__':
    unittest.main()
