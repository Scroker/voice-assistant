import os
import sys
import json
import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch, AsyncMock

daemon_dir = Path(__file__).resolve().parent.parent / 'src' / 'daemon'
sys.path.insert(0, str(daemon_dir))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from mcp.config import MCPConfigLoader, DEFAULT_CONFIG
from mcp.manager import MCPManager, GNOME_MCP_TOOLS_DEFAULTS
from mcp.registry import MCPRegistryClient
from mcp.installer import MCPServerInstaller
from mcp.credentials import MCPCredentialStore
from mcp.client import ExternalMCPClient


class TestMCPConfigLoader(unittest.TestCase):

    def test_mcp_config_loader_default(self):
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "mcp_servers.json"
            loader = MCPConfigLoader(config_path)
            cfg = loader.load()
            self.assertIn("mcpServers", cfg)
            self.assertIn("gnome-mcp-server", cfg["mcpServers"])
            self.assertTrue(cfg["mcpServers"]["gnome-mcp-server"]["enabled"])

            # Toggle status
            res = loader.set_server_status("gnome-mcp-server", False)
            self.assertTrue(res)
            self.assertFalse(loader.get_servers()["gnome-mcp-server"]["enabled"])

    def test_mcp_config_loader_migration_gnome_system(self):
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "mcp_servers.json"
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump({
                    "mcpServers": {
                        "gnome-system": {
                            "command": "builtin",
                            "enabled": True,
                            "description": "Legacy builtin"
                        }
                    }
                }, f)

            loader = MCPConfigLoader(config_path)
            cfg = loader.load()
            self.assertNotIn("gnome-system", cfg["mcpServers"])
            self.assertIn("gnome-mcp-server", cfg["mcpServers"])
            self.assertTrue(cfg["mcpServers"]["gnome-mcp-server"]["enabled"])


class TestExternalMCPClient(unittest.IsolatedAsyncioTestCase):

    async def test_client_initialization(self):
        client = ExternalMCPClient(
            name="test-server",
            command="python3",
            args=["-c", "import sys; sys.exit(0)"],
            env={"FOO": "BAR"},
        )
        self.assertEqual(client.name, "test-server")
        self.assertEqual(client.command, "python3")
        self.assertFalse(client.is_running())

    async def test_client_stop_when_not_started(self):
        client = ExternalMCPClient(name="dummy", command="echo")
        await client.stop()  # Should not throw
        self.assertFalse(client.is_running())

    async def test_client_handles_tracing_logs_on_stdout(self):
        # Simula un server come gnome-mcp-server che emette log tracing su stdout prima del JSON
        py_server = (
            "import sys, json\n"
            "sys.stdout.write('2026-09-06T01:28:55.816742Z  WARN gnome_mcp_server: warning log\\n')\n"
            "sys.stdout.flush()\n"
            "while True:\n"
            "    line = sys.stdin.readline()\n"
            "    if not line: break\n"
            "    req = json.loads(line)\n"
            "    if req.get('method') == 'initialize':\n"
            "        resp = {'jsonrpc': '2.0', 'id': req.get('id'), 'result': {'capabilities': {}}}\n"
            "        sys.stdout.write('2026-09-06T01:28:56Z INFO log inside\\n' + json.dumps(resp) + '\\n')\n"
            "        sys.stdout.flush()\n"
            "    elif req.get('method') == 'tools/list':\n"
            "        resp = {'jsonrpc': '2.0', 'id': req.get('id'), 'result': {'tools': [{'name': 'demo'}]}}\n"
            "        sys.stdout.write(json.dumps(resp) + '\\n')\n"
            "        sys.stdout.flush()\n"
        )
        client = ExternalMCPClient(
            name="tracing-server",
            command="python3",
            args=["-c", py_server],
        )
        started = await client.start()
        self.assertTrue(started)
        tools = await client.list_tools()
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["name"], "demo")
        await client.stop()


class TestMCPServerInstaller(unittest.IsolatedAsyncioTestCase):

    @patch.object(MCPCredentialStore, "store_environment", lambda _store, _name, env: {k: {"keyring": f"test:{k}"} for k in env})
    @patch.object(MCPCredentialStore, "resolve_environment", lambda _store, env: {k: "test-value" for k in env})
    async def test_mcp_installer_install_and_test(self):
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "mcp_servers.json"
            loader = MCPConfigLoader(config_path)
            installer = MCPServerInstaller(config_loader=loader)

            server_def = {
                'command': 'python3',
                'args': ['-c', 'print("mcp-ok")'],
                'env': {'TEST_ENV': 'ok'},
                'description': 'dummy python server'
            }

            ok, msg = await installer.install_server('demo-python', server_def)
            self.assertTrue(ok, msg)
            self.assertIn('demo-python', loader.get_servers())

            test_ok, test_msg = await installer.test_server('demo-python')
            self.assertTrue(test_ok, test_msg)

            remove_ok, remove_msg = await installer.uninstall_server('demo-python')
            self.assertTrue(remove_ok, remove_msg)

    @patch.object(MCPCredentialStore, "store_environment", lambda _store, _name, env: {k: {"keyring": f"test:{k}"} for k in env})
    async def test_mcp_installer_updates_environment(self):
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "mcp_servers.json"
            loader = MCPConfigLoader(config_path)
            config = loader.load()
            config["mcpServers"]["demo"] = {
                "command": "python3",
                "args": [],
                "env": {"API_KEY": "saved", "REGION": "eu"},
            }
            loader.save(config)

            installer = MCPServerInstaller(config_loader=loader)
            updated, _ = await installer.update_server_env(
                "demo", {"API_KEY": "replacement"}
            )
            self.assertTrue(updated)
            self.assertEqual(loader.get_servers()["demo"]["env"]["REGION"], "eu")
            self.assertEqual(loader.get_servers()["demo"]["env"]["API_KEY"], {"keyring": "test:API_KEY"})

    def test_get_install_hint(self):
        hint = MCPServerInstaller._get_install_hint("gnome-mcp-server")
        self.assertIn("cargo install", hint)
        hint_cargo = MCPServerInstaller._get_install_hint("cargo")
        self.assertIn("cargo", hint_cargo)


class TestMCPManager(unittest.IsolatedAsyncioTestCase):

    async def test_mcp_manager_initialization_defaults(self):
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "mcp_servers.json"
            loader = MCPConfigLoader(config_path)
            manager = MCPManager(config_loader=loader)
            await manager.initialize()

            schemas = manager.get_tools_schema()
            tool_names = [s["function"]["name"] for s in schemas]
            self.assertIn("set_volume", tool_names)
            self.assertIn("quick_settings", tool_names)
            self.assertIn("launch_application", tool_names)
            self.assertIn("media_control", tool_names)

            prompt = manager.format_system_prompt_tools()
            self.assertIn("set_volume", prompt)
            self.assertIn("quick_settings", prompt)
            self.assertIn("launch_application", prompt)

            # Test execution without server running -> returns informative hint or local pipewire result
            res = await manager.execute_tool("set_volume", {"volume": 50})
            self.assertTrue("gnome-mcp-server" in res or "PipeWire" in res or "Volume" in res)

            # Test unknown tool
            unknown = await manager.execute_tool("totally_unknown_tool_xyz", {})
            self.assertIn("non trovato", unknown)

            await manager.close()

    async def test_mcp_manager_backward_compatibility_adapter(self):
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "mcp_servers.json"
            loader = MCPConfigLoader(config_path)
            manager = MCPManager(config_loader=loader)
            await manager.initialize()

            # Mock external client for gnome-mcp-server
            mock_client = AsyncMock()
            mock_client.call_tool = AsyncMock(return_value="OK from mock")
            manager.external_clients["gnome-mcp-server"] = mock_client

            # 1. system_volume adapter
            res = await manager.execute_tool("system_volume", {"action": "increase", "level": 15})
            mock_client.call_tool.assert_called_with("set_volume", {"direction": "up", "relative": True, "volume": 15.0})
            self.assertEqual(res, "OK from mock")

            # 2. dark_mode adapter
            res = await manager.execute_tool("dark_mode", {"mode": "dark"})
            mock_client.call_tool.assert_called_with("quick_settings", {"setting": "dark_style", "enabled": True})

            # 3. app_launcher adapter
            res = await manager.execute_tool("app_launcher", {"app_name": "firefox"})
            mock_client.call_tool.assert_called_with("launch_application", {"app_name": "firefox"})

            # 4. system_media adapter
            res = await manager.execute_tool("system_media", {"action": "play"})
            mock_client.call_tool.assert_called_with("media_control", {"action": "play"})

            await manager.close()

    def test_mcp_manager_switches_registry_url(self):
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "mcp_servers.json"
            manager = MCPManager(
                config_loader=MCPConfigLoader(config_path),
                registry_url="https://registry.example",
            )
            self.assertEqual(manager.registry_client.registry_url, "https://registry.example")
            manager.set_registry_url("https://api.smithery.ai/")
            self.assertEqual(manager.registry_client.registry_url, "https://api.smithery.ai")

    async def test_mcp_manager_is_server_installed(self):
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "mcp_servers.json"
            loader = MCPConfigLoader(config_path)
            manager = MCPManager(config_loader=loader)

            with patch("shutil.which", return_value=None), \
                 patch("os.path.isfile", return_value=False):
                self.assertFalse(manager.is_server_installed("gnome-mcp-server"))

            with patch("shutil.which", return_value="/usr/bin/gnome-mcp-server"):
                self.assertTrue(manager.is_server_installed("gnome-mcp-server"))

    async def test_mcp_manager_start_server(self):
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "mcp_servers.json"
            loader = MCPConfigLoader(config_path)
            manager = MCPManager(config_loader=loader)

            with patch("mcp.manager.ExternalMCPClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.start = AsyncMock(return_value=True)
                mock_client.list_tools = AsyncMock(return_value=[{"name": "test_tool", "description": "desc"}])
                mock_client_cls.return_value = mock_client

                ok, msg = await manager.start_server("gnome-mcp-server")
                self.assertTrue(ok)
                self.assertIn("gnome-mcp-server", manager.external_clients)
                self.assertIn("test_tool", manager.external_tools_schemas)


class TestMCPRegistryClient(unittest.IsolatedAsyncioTestCase):

    async def test_registry_client_featured(self):
        client = MCPRegistryClient()
        featured = await client.get_featured()
        self.assertIsInstance(featured, list)
        self.assertTrue(any(s.get("name") == "gnome-mcp-server" for s in featured))

    async def test_registry_client_search(self):
        client = MCPRegistryClient()
        results = await client.search("gnome")
        self.assertIsInstance(results, list)
        self.assertTrue(any("gnome" in s.get("name", "").lower() or "gnome" in s.get("description", "").lower() for s in results))


class TestVoiceAssistantFastPathHandler(unittest.TestCase):

    def test_voice_assistant_fast_path_handler_forwards_text_argument(self):
        from main import VoiceAssistant

        assistant = VoiceAssistant.__new__(VoiceAssistant)
        assistant.assistant_runtime = type("Runtime", (), {})()
        calls = []

        def handle(intent, params, text=""):
            calls.append((intent, params, text))
            return True, "ok"

        assistant.assistant_runtime._handle_fast_path_intent = handle

        self.assertEqual(
            assistant._handle_fast_path_intent("volume_up", {"direction": "up"}, "alza il volume"),
            (True, "ok")
        )
        self.assertEqual(calls, [("volume_up", {"direction": "up"}, "alza il volume")])

    def test_voice_assistant_resolves_mcp_marketplace_coroutines(self):
        from main import VoiceAssistant

        class DummyMCP:
            async def get_marketplace_featured(self):
                return json.dumps([{"name": "demo"}])

            async def search_marketplace(self, query):
                return json.dumps([{"name": query}])

            async def get_installed_servers(self):
                return json.dumps([{"name": "demo", "enabled": True}])

            async def get_server_details(self, name):
                return json.dumps({"name": name})

            async def get_marketplace_categories(self):
                return json.dumps(["Desktop"])

            async def filter_marketplace_by_category(self, category):
                return json.dumps([{"category": category}])

            async def start_server(self, name):
                return True, f"Server {name} avviato"

            async def install_mcp_server(self, name, config, env):
                return True, name

            async def uninstall_mcp_server(self, name):
                return True, name

            async def test_mcp_server(self, name):
                return True, name

            async def update_server_config(self, name, env, enabled):
                return enabled, name

        assistant = VoiceAssistant.__new__(VoiceAssistant)
        assistant.mcp_manager = DummyMCP()

        self.assertTrue(hasattr(assistant, "get_marketplace_featured"))
        self.assertTrue(hasattr(assistant, "search_marketplace"))
        self.assertTrue(hasattr(assistant, "get_installed_servers"))
        self.assertTrue(hasattr(assistant, "start_mcp_server"))
        self.assertTrue(hasattr(assistant, "StartMCPServer"))

        featured = assistant.get_marketplace_featured()
        self.assertEqual(json.loads(featured)[0]["name"], "demo")
        self.assertEqual(json.loads(assistant.search_marketplace("query"))[0]["name"], "query")
        self.assertEqual(json.loads(assistant.get_server_details("server"))["name"], "server")
        self.assertEqual(json.loads(assistant.get_marketplace_categories()), ["Desktop"])
        self.assertEqual(json.loads(assistant.filter_marketplace_by_category("Web"))[0]["category"], "Web")
        self.assertEqual(assistant.install_mcp_server("server", "{}"), (True, "server"))
        self.assertEqual(assistant.start_mcp_server("server"), (True, "Server server avviato"))
        self.assertEqual(assistant.StartMCPServer("server"), (True, "Server server avviato"))
        self.assertEqual(assistant.uninstall_mcp_server("server"), (True, "server"))
        self.assertEqual(assistant.test_mcp_server("server"), (True, "server"))
        self.assertEqual(assistant.update_server_config("server", "{}", True), (True, "server"))
        self.assertEqual(json.loads(assistant.get_installed_servers())[0]["name"], "demo")


if __name__ == "__main__":
    unittest.main()
