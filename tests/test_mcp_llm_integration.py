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
    from daemon.mcp.config import MCPConfigLoader
    from daemon.mcp.manager import MCPManager
    from daemon.services.llm_service import LLMServiceManager
except ImportError:
    from mcp.config import MCPConfigLoader
    from mcp.manager import MCPManager
    from services.llm_service import LLMServiceManager


@pytest.fixture
def temp_config_file():
    with TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "mcp_servers.json"
        yield config_path


@pytest.mark.asyncio
async def test_llm_system_prompt_tool_injection(temp_config_file):
    loader = MCPConfigLoader(temp_config_file)
    manager = MCPManager(config_loader=loader)
    await manager.initialize()

    service = LLMServiceManager(mcp_manager=manager)
    config = service.get_config()

    system_prompt = config["system_prompt"]
    assert "date_time" in system_prompt
    assert "system_volume" in system_prompt
    assert "dark_mode" in system_prompt
    assert "app_launcher" in system_prompt

    await manager.close()


@pytest.mark.asyncio
async def test_llm_tool_call_parsing():
    service = LLMServiceManager()

    # Raw JSON
    raw_json = '{"tool": "date_time", "args": {"format": "time"}}'
    parsed = service._parse_tool_call(raw_json)
    assert parsed is not None
    assert parsed["tool"] == "date_time"
    assert parsed["args"]["format"] == "time"

    # Markdown wrapped JSON
    md_json = 'Ecco la chiamata:\n```json\n{"tool": "system_volume", "args": {"action": "set", "level": 50}}\n```'
    parsed_md = service._parse_tool_call(md_json)
    assert parsed_md is not None
    assert parsed_md["tool"] == "system_volume"
    assert parsed_md["args"]["level"] == 50


@pytest.mark.asyncio
async def test_mock_llm_invocation_date_time(temp_config_file):
    loader = MCPConfigLoader(temp_config_file)
    manager = MCPManager(config_loader=loader)
    await manager.initialize()

    class MockProvider:
        def stream_tokens(self, prompt, system_prompt=""):
            yield '{"tool": "date_time", "args": {"format": "time"}}'

    service = LLMServiceManager(mcp_manager=manager)
    service.local_gguf_provider = MockProvider()

    tokens = list(service.stream_tokens("Che ore sono?"))
    combined = "".join(tokens)

    assert '{"tool": "date_time"' in combined
    assert "ore" in combined

    await manager.close()


@pytest.mark.asyncio
async def test_mock_llm_invocation_dark_mode(temp_config_file):
    loader = MCPConfigLoader(temp_config_file)
    manager = MCPManager(config_loader=loader)
    await manager.initialize()

    class MockProvider:
        def stream_tokens(self, prompt, system_prompt=""):
            yield '{"tool": "dark_mode", "args": {"mode": "get"}}'

    service = LLMServiceManager(mcp_manager=manager)
    service.local_gguf_provider = MockProvider()

    tokens = list(service.stream_tokens("Controlla il tema"))
    combined = "".join(tokens)

    assert '{"tool": "dark_mode"' in combined
    assert "tema" in combined.lower() or "gnome" in combined.lower()

    await manager.close()
