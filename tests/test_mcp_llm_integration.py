import os
import sys
import json
import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, AsyncMock

daemon_dir = Path(__file__).resolve().parent.parent / 'src' / 'daemon'
sys.path.insert(0, str(daemon_dir))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from mcp.config import MCPConfigLoader
from mcp.manager import MCPManager
from services.llm_service import LLMServiceManager


class TestMCPLLMIntegration(unittest.IsolatedAsyncioTestCase):

    async def test_llm_system_prompt_tool_injection(self):
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "mcp_servers.json"
            loader = MCPConfigLoader(config_path)
            manager = MCPManager(config_loader=loader)
            await manager.initialize()

            service = LLMServiceManager(mcp_manager=manager)
            config = service.get_config()

            system_prompt = config["system_prompt"]
            self.assertIn("set_volume", system_prompt)
            self.assertIn("quick_settings", system_prompt)
            self.assertIn("launch_application", system_prompt)
            self.assertIn("media_control", system_prompt)

            await manager.close()

    def test_llm_tool_call_parsing(self):
        service = LLMServiceManager()

        # Raw JSON with gnome-mcp-server tool
        raw_json = '{"tool": "set_volume", "args": {"volume": 50.0}}'
        parsed = service._parse_tool_call(raw_json)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["tool"], "set_volume")
        self.assertEqual(parsed["args"]["volume"], 50.0)

        # Markdown wrapped JSON
        md_json = 'Ecco la chiamata:\n```json\n{"tool": "quick_settings", "args": {"setting": "dark_style", "enabled": true}}\n```'
        parsed_md = service._parse_tool_call(md_json)
        self.assertIsNotNone(parsed_md)
        self.assertEqual(parsed_md["tool"], "quick_settings")
        self.assertEqual(parsed_md["args"]["setting"], "dark_style")
        self.assertTrue(parsed_md["args"]["enabled"])

        # Legacy tool call parsing (backward compatibility)
        legacy_json = '{"tool": "system_volume", "args": {"action": "increase", "level": 10}}'
        parsed_legacy = service._parse_tool_call(legacy_json)
        self.assertIsNotNone(parsed_legacy)
        self.assertEqual(parsed_legacy["tool"], "system_volume")

    async def test_mock_llm_invocation_gnome_mcp_tool(self):
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "mcp_servers.json"
            loader = MCPConfigLoader(config_path)
            manager = MCPManager(config_loader=loader)
            await manager.initialize()

            # Mock external client for gnome-mcp-server
            mock_client = AsyncMock()
            mock_client.call_tool = AsyncMock(return_value="Volume impostato a 75%")
            manager.external_clients["gnome-mcp-server"] = mock_client

            class MockProvider:
                def stream_tokens(self, prompt, system_prompt=""):
                    yield '{"tool": "set_volume", "args": {"volume": 75.0}}'

            service = LLMServiceManager(mcp_manager=manager)
            service.local_gguf_provider = MockProvider()

            tokens = list(service.stream_tokens("Imposta il volume al 75 percento"))
            combined = "".join(tokens)

            self.assertIn('{"tool": "set_volume"', combined)
            self.assertIn("Volume impostato a 75%", combined)

            await manager.close()

    async def test_mock_llm_invocation_legacy_tool_call_adapter(self):
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "mcp_servers.json"
            loader = MCPConfigLoader(config_path)
            manager = MCPManager(config_loader=loader)
            await manager.initialize()

            # Mock external client for gnome-mcp-server
            mock_client = AsyncMock()
            mock_client.call_tool = AsyncMock(return_value="Tema scuro impostato")
            manager.external_clients["gnome-mcp-server"] = mock_client

            class MockProvider:
                def stream_tokens(self, prompt, system_prompt=""):
                    # LLM generates legacy dark_mode tool call
                    yield '{"tool": "dark_mode", "args": {"mode": "dark"}}'

            service = LLMServiceManager(mcp_manager=manager)
            service.local_gguf_provider = MockProvider()

            tokens = list(service.stream_tokens("Attiva il tema scuro"))
            combined = "".join(tokens)

            self.assertIn('{"tool": "dark_mode"', combined)
            # Should have executed quick_settings via compatibility adapter
            mock_client.call_tool.assert_called_with("quick_settings", {"setting": "dark_style", "enabled": True})
            self.assertIn("Tema scuro impostato", combined)

            await manager.close()


if __name__ == "__main__":
    unittest.main()
