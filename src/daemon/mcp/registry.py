import json
import logging
import asyncio
import urllib.request
from typing import List, Dict, Any

logger = logging.getLogger("VoiceAssistant.MCPRegistry")

DEFAULT_REGISTRY_URL = "https://registry.smithery.ai"

# Popular pre-configured MCP servers for quick discovery in offline/fallback mode
FEATURED_SERVERS = [
    {
        "name": "gnome-system",
        "title": "Native GNOME System Controls",
        "description": "Control desktop volume, dark mode, and open applications natively.",
        "command": "builtin",
        "args": [],
        "env": {},
        "category": "Desktop",
        "installed": True,
    },
    {
        "name": "fetch-web",
        "title": "Web Fetcher & Markdown Extractor",
        "description": "Fetch web page content and convert to clean markdown for LLM analysis.",
        "command": "uvx",
        "args": ["mcp-server-fetch"],
        "env": {},
        "category": "Web",
        "installed": False,
    },
    {
        "name": "filesystem",
        "title": "Local Filesystem Search",
        "description": "Search and read allowed files and documents in user directory.",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "~/Documents"],
        "env": {},
        "category": "Productivity",
        "installed": False,
    },
    {
        "name": "sqlite-db",
        "title": "SQLite Database Query Tool",
        "description": "Query local SQLite databases with safe read-only SQL execution.",
        "command": "uvx",
        "args": ["mcp-server-sqlite", "--db-path", "~/.local/share/voice-assistant/database.db"],
        "env": {},
        "category": "Data",
        "installed": False,
    },
]

class MCPRegistryClient:
    """Client for discovering and fetching MCP servers from remote registries or local marketplace index."""

    def __init__(self, registry_url: str = DEFAULT_REGISTRY_URL):
        self.registry_url = registry_url.rstrip("/")

    async def get_featured(self) -> List[Dict[str, Any]]:
        """Returns list of featured MCP servers."""
        return FEATURED_SERVERS

    async def search(self, query: str) -> List[Dict[str, Any]]:
        """Searches remote registry or falls back to local featured servers matching query."""
        if not query:
            return await self.get_featured()

        def _fetch_remote():
            url = f"{self.registry_url}/api/servers?q={urllib.parse.quote(query)}"
            req = urllib.request.Request(url, headers={"User-Agent": "VoiceAssistant/1.0"})
            try:
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        if isinstance(data, list):
                            return data
                        return data.get("servers", [])
            except Exception as e:
                logger.debug(f"Impossibile interrogare il registry remoto '{url}': {e}")
            return None

        remote_results = await asyncio.to_thread(_fetch_remote)
        if remote_results:
            return remote_results

        # Fallback local filter
        q_lower = query.lower()
        return [
            s for s in FEATURED_SERVERS
            if q_lower in s["name"].lower() or q_lower in s["description"].lower() or q_lower in s["category"].lower()
        ]
