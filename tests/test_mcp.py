import importlib.util
import os
import sys
import json
import pytest
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

daemon_dir = Path(__file__).resolve().parent.parent / 'src' / 'daemon'
sys.path.insert(0, str(daemon_dir))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

try:
    from daemon.mcp.config import MCPConfigLoader, DEFAULT_CONFIG
    from daemon.mcp.tools import (
        SystemVolumeTool,
        DarkModeTool,
        AppLauncherTool,
        DateTimeTool,
        SystemMediaTool,
        ScreenBrightnessTool,
        SystemPowerTool,
        ClipboardTool,
    )
    from daemon.mcp.manager import MCPManager
    from daemon.mcp.registry import MCPRegistryClient
    from daemon.mcp.installer import MCPServerInstaller
    from daemon.mcp.credentials import MCPCredentialStore
except ImportError:
    from mcp.config import MCPConfigLoader, DEFAULT_CONFIG
    from mcp.tools import (
        SystemVolumeTool,
        DarkModeTool,
        AppLauncherTool,
        DateTimeTool,
        SystemMediaTool,
        ScreenBrightnessTool,
        SystemPowerTool,
        ClipboardTool,
    )
    from mcp.manager import MCPManager
    from mcp.registry import MCPRegistryClient
    from mcp.installer import MCPServerInstaller
    from mcp.credentials import MCPCredentialStore


@pytest.fixture
def temp_config_file():
    with TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "mcp_servers.json"
        yield config_path


@pytest.mark.asyncio
async def test_mcp_config_loader(temp_config_file):
    loader = MCPConfigLoader(temp_config_file)
    cfg = loader.load()
    assert "mcpServers" in cfg
    assert "gnome-system" in cfg["mcpServers"]

    # Toggle status
    res = loader.set_server_status("gnome-system", False)
    assert res is True
    assert loader.get_servers()["gnome-system"]["enabled"] is False


@pytest.mark.asyncio
async def test_system_volume_tool():
    tool = SystemVolumeTool()
    assert tool.name == "system_volume"
    assert "action" in tool.parameters["properties"]
    schema = tool.to_schema()
    assert schema["function"]["name"] == "system_volume"

    # Test get action
    result = await tool.execute({"action": "get"})
    assert isinstance(result, str)
    assert len(result) > 0


@pytest.mark.asyncio
async def test_dark_mode_tool():
    tool = DarkModeTool()
    assert tool.name == "dark_mode"
    assert "mode" in tool.parameters["properties"]

    # Test get action
    result = await tool.execute({"mode": "get"})
    assert isinstance(result, str)
    assert "tema" in result.lower() or "gnome" in result.lower()


@pytest.mark.asyncio
async def test_app_launcher_tool():
    tool = AppLauncherTool()
    assert tool.name == "app_launcher"

    # Test empty app name
    res = await tool.execute({"app_name": ""})
    assert "Specificare" in res

    # Test non-existent app
    res = await tool.execute({"app_name": "non_existent_fake_app_xyz"})
    assert "Impossibile trovare" in res or "avviata" in res


@pytest.mark.asyncio
async def test_date_time_tool():
    tool = DateTimeTool()
    assert tool.name == "date_time"

    res_time = await tool.execute({"format": "time"})
    assert "ore" in res_time

    res_date = await tool.execute({"format": "date"})
    assert "Oggi è" in res_date


@pytest.mark.asyncio
async def test_system_media_tool():
    tool = SystemMediaTool()
    assert tool.name == "system_media"
    res = await tool.execute({"action": "play-pause"})
    assert isinstance(res, str)


@pytest.mark.asyncio
async def test_screen_brightness_tool():
    tool = ScreenBrightnessTool()
    assert tool.name == "screen_brightness"
    res = await tool.execute({"action": "get"})
    assert isinstance(res, str)


@pytest.mark.asyncio
async def test_system_power_tool():
    tool = SystemPowerTool()
    assert tool.name == "system_power"
    # Execute get/info or check name
    assert tool.parameters["properties"]["action"]["enum"] == ["lock", "suspend", "logout", "restart", "shutdown"]


@pytest.mark.asyncio
async def test_clipboard_tool():
    tool = ClipboardTool()
    assert tool.name == "clipboard"
    res = await tool.execute({"action": "get"})
    assert isinstance(res, str)


@pytest.mark.asyncio
async def test_backend_dependencies_available():
    assert importlib.util.find_spec('urllib') is not None
    assert importlib.util.find_spec('urllib.request') is not None
    assert importlib.util.find_spec('subprocess') is not None


@pytest.mark.asyncio
async def test_mcp_installer_install_and_test(temp_config_file, monkeypatch):
    monkeypatch.setattr(
        MCPCredentialStore,
        "store_environment",
        lambda _store, _name, env: {key: {"keyring": f"test:{key}"} for key in env},
    )
    monkeypatch.setattr(
        MCPCredentialStore,
        "resolve_environment",
        lambda _store, env: {key: "test-value" for key in env},
    )
    loader = MCPConfigLoader(temp_config_file)
    installer = __import__('daemon.mcp.installer', fromlist=['MCPServerInstaller']).MCPServerInstaller(config_loader=loader)

    server_def = {
        'command': 'python3',
        'args': ['-c', 'print("mcp-ok")'],
        'env': {'TEST_ENV': 'ok'},
        'description': 'dummy python server'
    }

    ok, msg = await installer.install_server('demo-python', server_def)
    assert ok is True, msg
    assert 'demo-python' in loader.get_servers()

    test_ok, test_msg = await installer.test_server('demo-python')
    assert test_ok is True, test_msg

    remove_ok, remove_msg = await installer.uninstall_server('demo-python')
    assert remove_ok is True, remove_msg


@pytest.mark.asyncio
async def test_mcp_installer_updates_environment_without_erasing_existing_keys(temp_config_file, monkeypatch):
    monkeypatch.setattr(
        MCPCredentialStore,
        "store_environment",
        lambda _store, _name, env: {key: {"keyring": f"test:{key}"} for key in env},
    )
    loader = MCPConfigLoader(temp_config_file)
    config = loader.load()
    config["mcpServers"]["demo"] = {
        "command": "python3",
        "args": [],
        "env": {"API_KEY": "saved", "REGION": "eu"},
    }
    loader.save(config)

    updated, _ = await MCPServerInstaller(config_loader=loader).update_server_env(
        "demo", {"API_KEY": "replacement"}
    )

    assert updated is True
    assert loader.get_servers()["demo"]["env"] == {
        "API_KEY": {"keyring": "test:API_KEY"},
        "REGION": "eu",
    }


@pytest.mark.asyncio
async def test_mcp_manager_initialization(temp_config_file):
    loader = MCPConfigLoader(temp_config_file)
    manager = MCPManager(config_loader=loader)
    await manager.initialize()

    assert "system_volume" in manager.native_tools
    assert "dark_mode" in manager.native_tools
    assert "app_launcher" in manager.native_tools

    schemas = manager.get_tools_schema()
    assert len(schemas) >= 3

    prompt = manager.format_system_prompt_tools()
    assert "system_volume" in prompt
    assert "dark_mode" in prompt

    res = await manager.execute_tool("dark_mode", {"mode": "get"})
    assert isinstance(res, str)

    unknown_res = await manager.execute_tool("unknown_tool", {})
    assert "non trovato" in unknown_res

    featured = json.loads(await manager.get_marketplace_featured())
    assert isinstance(featured, list)
    assert any(server.get("name") == "gnome-system" for server in featured)

    await manager.close()


@pytest.mark.asyncio
async def test_mcp_registry_client():
    client = MCPRegistryClient()
    featured = await client.get_featured()
    assert len(featured) > 0
    assert any(s["name"] == "gnome-system" for s in featured)

    search_res = await client.search("gnome")
    assert len(search_res) > 0

    details = await client.get_server_details('gnome-system')
    assert details is not None
    assert details['name'] == 'gnome-system'


def test_mcp_manager_switches_registry_url(temp_config_file):
    manager = MCPManager(
        config_loader=MCPConfigLoader(temp_config_file),
        registry_url="https://registry.example",
    )

    assert manager.registry_client.registry_url == "https://registry.example"
    manager.set_registry_url("https://api.smithery.ai/")
    assert manager.registry_client.registry_url == "https://api.smithery.ai"


def test_voice_assistant_resolves_mcp_marketplace_coroutines():
    try:
        from daemon.main import VoiceAssistant
    except ImportError:
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
            return json.dumps(["Development"])

        async def filter_marketplace_by_category(self, category):
            return json.dumps([{"category": category}])

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

    assert hasattr(assistant, "get_marketplace_featured")
    assert hasattr(assistant, "search_marketplace")
    assert hasattr(assistant, "get_installed_servers")

    featured = assistant.get_marketplace_featured()
    assert json.loads(featured)[0]["name"] == "demo"
    assert json.loads(assistant.search_marketplace("query"))[0]["name"] == "query"
    assert json.loads(assistant.get_server_details("server"))["name"] == "server"
    assert json.loads(assistant.get_marketplace_categories()) == ["Development"]
    assert json.loads(assistant.filter_marketplace_by_category("Web"))[0]["category"] == "Web"
    assert assistant.install_mcp_server("server", "{}") == (True, "server")
    assert assistant.uninstall_mcp_server("server") == (True, "server")
    assert assistant.test_mcp_server("server") == (True, "server")
    assert assistant.update_server_config("server", "{}", True) == (True, "server")
    assert json.loads(assistant.get_installed_servers())[0]["name"] == "demo"


def test_voice_assistant_fast_path_handler_forwards_text_argument():
    try:
        from daemon.main import VoiceAssistant
    except ImportError:
        from main import VoiceAssistant

    assistant = VoiceAssistant.__new__(VoiceAssistant)
    assistant.assistant_runtime = type("Runtime", (), {})()
    calls = []

    def handle(intent, params, text=""):
        calls.append((intent, params, text))
        return True, "ok"

    assistant.assistant_runtime._handle_fast_path_intent = handle

    assert assistant._handle_fast_path_intent("volume_up", {"delta": 10}, "alza il volume") == (True, "ok")
    assert calls == [("volume_up", {"delta": 10}, "alza il volume")]
