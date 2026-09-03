import json
import logging
import asyncio
import os
import urllib.request
import urllib.parse
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path

logger = logging.getLogger("VoiceAssistant.MCPRegistry")

DEFAULT_REGISTRY_URL = "https://api.smithery.ai"

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

    def __init__(self, registry_url: str = DEFAULT_REGISTRY_URL, config_loader = None):
        self.registry_url = registry_url.rstrip("/")
        self.api_key = os.environ.get("SMITHERY_API_KEY", "")
        self.config_loader = config_loader
        if config_loader is None:
            from .config import MCPConfigLoader
            self.config_loader = MCPConfigLoader()

    async def get_featured(self) -> List[Dict[str, Any]]:
        """Returns list of featured MCP servers with installation status."""
        remote_servers = await self._fetch_servers()
        servers = [server.copy() for server in FEATURED_SERVERS]
        known_names = {server["name"] for server in servers}
        for server in remote_servers or []:
            if server.get("name") not in known_names:
                servers.append(server)
        installed_names = set(self.config_loader.get_servers().keys())
        
        for server in servers:
            server["installed"] = server.get("name") in installed_names
        
        return servers

    async def search(self, query: str) -> List[Dict[str, Any]]:
        """Searches remote registry or falls back to local featured servers matching query."""
        if not query:
            return await self.get_featured()

        def _fetch_remote():
            return self._fetch_servers_sync(query)

        remote_results = await asyncio.to_thread(_fetch_remote)
        if remote_results:
            results = remote_results
        else:
            # Fallback local filter
            q_lower = query.lower()
            results = [
                s for s in FEATURED_SERVERS
                if q_lower in s["name"].lower() or q_lower in s["description"].lower() or q_lower in s["category"].lower()
            ]
        
        # Add installation status
        installed_names = set(self.config_loader.get_servers().keys())
        for server in results:
            server["installed"] = server.get("name") in installed_names
        
        return results

    async def get_server_details(self, name: str) -> Optional[Dict[str, Any]]:
        """Fetches detailed information about a server from registry."""
        def _fetch_details():
            qualified_name = urllib.parse.quote(name, safe="")
            url = f"{self.registry_url}/servers/{qualified_name}"
            req = urllib.request.Request(url, headers=self._request_headers())
            try:
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    if resp.status == 200:
                        return self._normalize_server(json.loads(resp.read().decode("utf-8")))
            except Exception as e:
                logger.debug(f"Impossibile interrogare dettagli server '{name}': {e}")
            return None

        details = await asyncio.to_thread(_fetch_details)
        if details is None:
            # Fallback to featured if available
            for server in FEATURED_SERVERS:
                if server["name"] == name:
                    details = server
                    break
        
        if details:
            installed_names = set(self.config_loader.get_servers().keys())
            details["installed"] = details.get("name") in installed_names
        
        return details

    async def _fetch_servers(self, query: str = "") -> Optional[List[Dict[str, Any]]]:
        return await asyncio.to_thread(self._fetch_servers_sync, query)

    def _fetch_servers_sync(self, query: str = "") -> Optional[List[Dict[str, Any]]]:
        params = {"page": "1", "pageSize": "20"}
        if query:
            params["q"] = query
        url = f"{self.registry_url}/servers?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers=self._request_headers())
        try:
            with urllib.request.urlopen(req, timeout=5.0) as response:
                if response.status != 200:
                    return None
                data = json.loads(response.read().decode("utf-8"))
                servers = data.get("servers", []) if isinstance(data, dict) else []
                return [self._normalize_server(server) for server in servers]
        except Exception as error:
            logger.debug(f"Impossibile interrogare il registry Smithery '{url}': {error}")
            return None

    def _request_headers(self) -> Dict[str, str]:
        headers = {"User-Agent": "VoiceAssistant/1.0", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @staticmethod
    def _normalize_server(server: Dict[str, Any]) -> Dict[str, Any]:
        qualified_name = server.get("qualifiedName", "")
        return {
            "name": qualified_name or server.get("slug", ""),
            "title": server.get("displayName") or qualified_name or "Unnamed MCP server",
            "description": server.get("description") or "Nessuna descrizione disponibile.",
            "category": "Remote" if server.get("remote") else "Stdio",
            "source_url": server.get("homepage") or f"https://smithery.ai/servers/{qualified_name}",
            "icon_url": server.get("iconUrl"),
            "verified": server.get("verified", False),
            "remote": server.get("remote"),
            "connections": server.get("connections", []),
            "command": "",
            "args": [],
            "env": {},
        }

    async def get_categories(self) -> List[str]:
        """Returns list of available server categories."""
        featured = await self.get_featured()
        return sorted(set(s.get("category", "Other") for s in featured))

    async def filter_by_category(self, category: str) -> List[Dict[str, Any]]:
        """Returns servers in a specific category."""
        featured = await self.get_featured()
        return [s for s in featured if s.get("category") == category]
