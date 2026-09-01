import sys
import json
import asyncio
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger("VoiceAssistant.MCPClient")

class ExternalMCPClient:
    """JSON-RPC 2.0 Stdio client for external Model Context Protocol (MCP) servers."""

    def __init__(self, name: str, command: str, args: List[str] = None, env: Dict[str, str] = None):
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.process: Optional[asyncio.subprocess.Process] = None
        self._request_id = 0

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def start(self) -> bool:
        """Starts the external MCP server process via stdio transport."""
        try:
            full_env = {**sys.modules["os"].environ, **self.env}
            self.process = await asyncio.create_subprocess_exec(
                self.command,
                *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=full_env
            )
            logger.info(f"Processo MCP '{self.name}' avviato (PID {self.process.pid}).")
            
            # Send initialization handshake
            init_res = await self.request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "gnome-voice-assistant", "version": "1.0.0"}
            })
            if init_res:
                await self.notify("notifications/initialized", {})
                return True
            return False
        except Exception as e:
            logger.error(f"Impossibile avviare il server MCP '{self.name}' ({self.command}): {e}")
            return False

    async def request(self, method: str, params: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
        """Sends a JSON-RPC request over stdio and waits for response."""
        if not self.process or not self.process.stdin or not self.process.stdout:
            return None

        req_id = self._next_id()
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or {}
        }

        try:
            data_bytes = (json.dumps(payload) + "\n").encode("utf-8")
            self.process.stdin.write(data_bytes)
            await self.process.stdin.drain()

            # Read single line JSON-RPC response
            line = await self.process.stdout.readline()
            if not line:
                return None
            res = json.loads(line.decode("utf-8").strip())
            if "error" in res:
                logger.error(f"Errore JSON-RPC da '{self.name}': {res['error']}")
                return None
            return res.get("result")
        except Exception as e:
            logger.error(f"Errore comunicazione request MCP '{self.name}': {e}")
            return None

    async def notify(self, method: str, params: Dict[str, Any] = None):
        """Sends a JSON-RPC notification (no response expected)."""
        if not self.process or not self.process.stdin:
            return

        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {}
        }
        try:
            data_bytes = (json.dumps(payload) + "\n").encode("utf-8")
            self.process.stdin.write(data_bytes)
            await self.process.stdin.drain()
        except Exception as e:
            logger.error(f"Errore notify MCP '{self.name}': {e}")

    async def list_tools(self) -> List[Dict[str, Any]]:
        """Queries external MCP server for available tools (tools/list)."""
        res = await self.request("tools/list")
        if res and "tools" in res:
            return res["tools"]
        return []

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """Executes a tool on the external MCP server (tools/call)."""
        res = await self.request("tools/call", {
            "name": tool_name,
            "arguments": arguments
        })
        if not res:
            return f"Nessun risultato o errore nell'esecuzione del tool '{tool_name}' su '{self.name}'."

        content = res.get("content", [])
        text_parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(item.get("text", ""))
            elif isinstance(item, str):
                text_parts.append(item)

        return "\n".join(text_parts) if text_parts else json.dumps(res)

    async def stop(self):
        """Terminates external MCP server process gracefully."""
        if self.process:
            try:
                self.process.terminate()
                await asyncio.sleep(0.1)
                if self.process.returncode is None:
                    self.process.kill()
            except Exception:
                pass
            self.process = None
