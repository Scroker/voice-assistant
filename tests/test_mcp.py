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
async def test_mcp_manager_initialization(temp_config_file):
    loader = MCPConfigLoader(temp_config_file)
    manager = MCPManager(config_loader=loader)
    await manager.initialize()

    assert "system_volume" in manager.native_tools
    assert "dark_mode" in manager.native_tools
    assert "app_launcher" in manager.native_tools

    schemas = manager.get_tools_schema()
    assert len(schemas) >= 3

    # Format system prompt
    prompt = manager.format_system_prompt_tools()
    assert "system_volume" in prompt
    assert "dark_mode" in prompt

    # Test executing native tool via manager
    res = await manager.execute_tool("dark_mode", {"mode": "get"})
    assert isinstance(res, str)

    # Test unknown tool execution
    unknown_res = await manager.execute_tool("unknown_tool", {})
    assert "non trovato" in unknown_res

    await manager.close()


@pytest.mark.asyncio
async def test_mcp_registry_client():
    client = MCPRegistryClient()
    featured = await client.get_featured()
    assert len(featured) > 0
    assert any(s["name"] == "gnome-system" for s in featured)

    search_res = await client.search("gnome")
    assert len(search_res) > 0
