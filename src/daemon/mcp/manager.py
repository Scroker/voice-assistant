import os
import shutil
import json
import logging
import asyncio
from typing import Dict, Any, List, Optional, Union, Tuple

from .config import MCPConfigLoader
from .registry import MCPRegistryClient
from .installer import MCPServerInstaller
from .client import ExternalMCPClient
from core.data_loader import load_json_data

logger = logging.getLogger("VoiceAssistant.MCPManager")


def __getattr__(name: str) -> Any:
    if name == "GNOME_MCP_TOOLS_DEFAULTS":
        return load_json_data("mcp/tools_gnome_defaults.json", fallback_default={})
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class MCPManager:
    """Central MCP execution engine integrated with gnome-mcp-server & external servers."""

    def __init__(self, config_loader: Optional[MCPConfigLoader] = None,
                 registry_url: Optional[str] = None):
        self.config_loader = config_loader or MCPConfigLoader()
        self.registry_client = MCPRegistryClient(
            registry_url=registry_url or "https://api.smithery.ai",
            config_loader=self.config_loader,
        )
        self.installer = MCPServerInstaller(config_loader=self.config_loader)
        self.default_tools: Dict[str, Any] = load_json_data(
            "mcp/tools_gnome_defaults.json", fallback_default={}
        )
        self.native_tools: Dict[str, Any] = {}
        self.external_clients: Dict[str, ExternalMCPClient] = {}
        self.external_tools_map: Dict[str, str] = {}  # tool_name -> server_name
        self.external_tools_schemas: Dict[str, Dict[str, Any]] = {}  # tool_name -> schema
        self._enabled = True

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        self._enabled = bool(value)

    async def initialize(self):
        """Connects to configured external MCP servers (default: gnome-mcp-server)."""
        config_data = self.config_loader.load()
        servers = config_data.get("mcpServers", {})

        for name, cfg in servers.items():
            if not cfg.get("enabled", True):
                continue
            cmd = cfg.get("command", "")
            if cmd == "builtin":
                continue

            args = cfg.get("args", [])
            env = cfg.get("env", {})
            client = ExternalMCPClient(name=name, command=cmd, args=args, env=env)
            if await client.start():
                self.external_clients[name] = client
                tools = await client.list_tools()
                for tool in tools:
                    t_name = tool.get("name")
                    if t_name:
                        self.external_tools_map[t_name] = name
                        self.external_tools_schemas[t_name] = {
                            "type": "function",
                            "function": {
                                "name": t_name,
                                "description": tool.get("description", ""),
                                "parameters": tool.get("inputSchema", {"type": "object", "properties": {}})
                            }
                        }

        # If gnome-mcp-server is configured and enabled, ensure its tool schemas are exposed
        if "gnome-mcp-server" in servers and servers["gnome-mcp-server"].get("enabled", True):
            for t_name, schema in self.default_tools.items():
                if t_name not in self.external_tools_schemas:
                    self.external_tools_schemas[t_name] = schema
                    self.external_tools_map[t_name] = "gnome-mcp-server"

        logger.info(f"MCPManager inizializzato. Server attivi: {len(self.external_clients)}, Tool registrati: {len(self.external_tools_schemas)}.")

    def is_server_installed(self, name: str) -> bool:
        """Verifica se l'eseguibile del server MCP è presente nel PATH o in ~/.cargo/bin."""
        config_data = self.config_loader.load()
        servers = config_data.get("mcpServers", {})
        if name not in servers:
            return False
        cfg = servers[name]
        cmd = cfg.get("command", "")
        if cmd == "builtin":
            return True
        if cmd == "gnome-mcp-server":
            cargo_candidate = os.path.expanduser("~/.cargo/bin/gnome-mcp-server")
            if os.path.isfile(cargo_candidate) and os.access(cargo_candidate, os.X_OK):
                return True
            import shutil
            return shutil.which("gnome-mcp-server") is not None
        import shutil
        return shutil.which(cmd) is not None

    async def start_server(self, name: str) -> Tuple[bool, str]:
        """Avvia un server MCP esterno configurato e ne registra i tool."""
        if not self._enabled:
            return False, "MCP disabilitato nelle impostazioni"

        config_data = self.config_loader.load()
        servers = config_data.get("mcpServers", {})
        if name not in servers:
            return False, f"Server '{name}' non presente nella configurazione"

        cfg = servers[name]
        cmd = cfg.get("command", "")
        if cmd == "builtin":
            return True, "Server built-in pronto all'uso"

        if name in self.external_clients:
            try:
                await self.external_clients[name].stop()
            except Exception:
                pass
            del self.external_clients[name]

        args = cfg.get("args", [])
        env = cfg.get("env", {})
        client = ExternalMCPClient(name=name, command=cmd, args=args, env=env)
        if await client.start():
            self.external_clients[name] = client
            tools = await client.list_tools()
            for tool in tools:
                t_name = tool.get("name")
                if t_name:
                    self.external_tools_map[t_name] = name
                    self.external_tools_schemas[t_name] = {
                        "type": "function",
                        "function": {
                            "name": t_name,
                            "description": tool.get("description", ""),
                            "parameters": tool.get("inputSchema", {"type": "object", "properties": {}})
                        }
                    }

            if name == "gnome-mcp-server":
                for t_name, schema in self.default_tools.items():
                    if t_name not in self.external_tools_schemas:
                        self.external_tools_schemas[t_name] = schema
                        self.external_tools_map[t_name] = "gnome-mcp-server"

            logger.info(f"Server MCP '{name}' avviato e registrato con {len(tools)} tool.")
            return True, f"Server MCP '{name}' avviato con successo"
        else:
            return False, f"Impossibile avviare il processo del server MCP '{name}'"

    def register_native_tool(self, tool: Any):
        """Registers a tool instance for testing or local extension."""
        self.native_tools[tool.name] = tool

    def set_registry_url(self, registry_url: str):
        """Switch the marketplace source without changing installed servers."""
        self.registry_client.registry_url = registry_url.rstrip("/")

    def get_tools_schema(self) -> List[Dict[str, Any]]:
        """Returns list of schemas for all active native and external tools."""
        if not self._enabled:
            return []

        schemas = []
        for tool in self.native_tools.values():
            if hasattr(tool, "to_schema"):
                schemas.append(tool.to_schema())

        for schema in self.external_tools_schemas.values():
            schemas.append(schema)

        return schemas

    def format_system_prompt_tools(self) -> str:
        """Formats active tool schemas into a readable system prompt string for LLMs."""
        schemas = self.get_tools_schema()
        if not schemas:
            return ""

        lines = [
            "### Strumenti e Tool Disponibili (gnome-mcp-server):",
            "Se l'utente richiede un'azione o un comando del sistema, rispondi ESCLUSIVAMENTE con l'oggetto JSON del tool da eseguire, SENZA ALCUN TESTO INTRODUTTIVO O SPIEGAZIONE.",
            'Formato obbligatorio: {"tool": "nome_tool", "args": {"arg1": "valore1"}}',
            "",
            "Elenco Tool:"
        ]
        for s in schemas:
            func = s.get("function", {})
            lines.append(f"- **{func.get('name')}**: {func.get('description')}")
            lines.append(f"  Parametri: {json.dumps(func.get('parameters', {}))}")
        lines.append("")
        return "\n".join(lines)

    async def execute_tool(self, tool_name: str, args: Dict[str, Any]) -> str:
        """Executes a tool by name (gnome-mcp-server, external, or via compatibility adapter)."""
        if not self._enabled:
            return "Integrazione MCP disabilitata nelle impostazioni."

        # 1. Backwards-compatibility adapter for legacy custom tool names
        actual_tool = tool_name
        actual_args = dict(args or {})

        if tool_name == "system_volume":
            actual_tool = "set_volume"
            action = str(actual_args.get("action", "")).lower()
            if action == "set":
                actual_args = {"volume": float(actual_args.get("level", 50))}
            elif action == "increase":
                actual_args = {"direction": "up", "relative": True, "volume": float(actual_args.get("level", 10))}
            elif action == "decrease":
                actual_args = {"direction": "down", "relative": True, "volume": -float(actual_args.get("level", 10))}
            elif action == "mute":
                actual_args = {"mute": True}
            elif action == "unmute":
                actual_args = {"mute": False}
            elif action == "get":
                actual_args = {"volume": 0.0, "relative": True}

        elif tool_name == "dark_mode":
            actual_tool = "quick_settings"
            mode = str(actual_args.get("mode", "dark")).lower()
            enabled = mode not in ("light", "default", "false", "0")
            actual_args = {"setting": "dark_style", "enabled": enabled}

        elif tool_name == "app_launcher":
            actual_tool = "launch_application"
            app_name = actual_args.get("app_name") or actual_args.get("app") or ""
            actual_args = {"app_name": app_name}

        elif tool_name == "system_media":
            actual_tool = "media_control"
            act = str(actual_args.get("action", "play")).lower().replace("-", "_")
            actual_args = {"action": act}

        # 2. Check explicitly registered native tools (tests/plugins)
        if actual_tool in self.native_tools:
            try:
                logger.info(f"Esecuzione tool registrato '{actual_tool}' con args: {actual_args}")
                return await self.native_tools[actual_tool].execute(actual_args)
            except Exception as e:
                logger.error(f"Errore durante l'esecuzione del tool '{actual_tool}': {e}")
                return f"Errore nell'esecuzione del tool '{actual_tool}': {e}"

        # 3. External MCP Server Execution (gnome-mcp-server or configured server)
        if actual_tool in self.external_tools_map:
            server_name = self.external_tools_map[actual_tool]
            client = self.external_clients.get(server_name)
            if client:
                try:
                    logger.info(f"Esecuzione tool '{actual_tool}' su server MCP '{server_name}'")
                    return await client.call_tool(actual_tool, actual_args)
                except Exception as e:
                    logger.error(f"Errore esecuzione tool '{actual_tool}' su '{server_name}': {e}")
                    return f"Errore nell'esecuzione del tool '{actual_tool}': {e}"

        # 4. If gnome-mcp-server client is running, try calling it directly
        gnome_client = self.external_clients.get("gnome-mcp-server")
        if gnome_client and actual_tool in self.default_tools:
            try:
                return await gnome_client.call_tool(actual_tool, actual_args)
            except Exception as e:
                return f"Errore nell'esecuzione del tool '{actual_tool}': {e}"

        # 5. Friendly fallback message if gnome-mcp-server is not running
        if actual_tool in self.default_tools:
            return f"gnome-mcp-server non è attualmente attivo. Installa o avvia gnome-mcp-server (cargo install --git https://github.com/bilelmoussaoui/gnome-mcp-server)."

        return f"Tool '{tool_name}' non trovato o non registrato."

    async def close(self):
        """Stops all active external MCP server processes."""
        for client in self.external_clients.values():
            await client.stop()
        self.external_clients.clear()
        self.external_tools_map.clear()
        self.external_tools_schemas.clear()

    # ============================================================================
    # Marketplace & Installation Methods (exposed via D-Bus)
    # ============================================================================

    async def get_marketplace_featured(self) -> str:
        """Returns featured MCP servers as JSON string."""
        try:
            servers = await self.registry_client.get_featured()
            return json.dumps(servers)
        except Exception as e:
            logger.error(f"[MCPManager] Errore caricamento featured servers: {e}")
            return json.dumps([])

    async def search_marketplace(self, query: str) -> str:
        """Searches marketplace for servers matching query."""
        try:
            servers = await self.registry_client.search(query)
            return json.dumps(servers)
        except Exception as e:
            logger.error(f"[MCPManager] Errore ricerca marketplace: {e}")
            return json.dumps([])

    async def get_server_details(self, server_name: str) -> str:
        """Fetches detailed info about a server."""
        try:
            details = await self.registry_client.get_server_details(server_name)
            return json.dumps(details or {})
        except Exception as e:
            logger.error(f"[MCPManager] Errore caricamento dettagli server: {e}")
            return json.dumps({})

    async def get_marketplace_categories(self) -> str:
        """Returns list of server categories."""
        try:
            categories = await self.registry_client.get_categories()
            return json.dumps(categories)
        except Exception as e:
            logger.error(f"[MCPManager] Errore caricamento categorie: {e}")
            return json.dumps([])

    async def filter_marketplace_by_category(self, category: str) -> str:
        """Returns servers in a specific category."""
        try:
            servers = await self.registry_client.filter_by_category(category)
            return json.dumps(servers)
        except Exception as e:
            logger.error(f"[MCPManager] Errore filtro categoria: {e}")
            return json.dumps([])

    async def install_mcp_server(
        self, server_name: str, server_config: str, env_vars: str = ""
    ) -> Tuple[bool, str]:
        """
        Installs an MCP server, enables it, and starts it.

        Returns:
            (success: bool, message: str)
        """
        try:
            server_def = json.loads(server_config)
            required_env = json.loads(env_vars) if env_vars else {}
            success, msg = await self.installer.install_server(
                server_name, server_def, required_env
            )
            if success:
                self.config_loader.set_server_status(server_name, True)
                start_ok, start_msg = await self.start_server(server_name)
                if start_ok:
                    return True, f"Server '{server_name}' installato e avviato con successo!"
                return True, f"Server '{server_name}' installato, ma l'avvio non è riuscito: {start_msg}"
            return success, msg
        except Exception as e:
            logger.error(f"[MCPManager] Errore installazione server '{server_name}': {e}")
            return False, f"Errore: {str(e)}"

    async def uninstall_mcp_server(self, server_name: str) -> Tuple[bool, str]:
        """Uninstalls an MCP server from config."""
        try:
            # Stop if running
            if server_name in self.external_clients:
                await self.external_clients[server_name].stop()
                del self.external_clients[server_name]
            
            success, msg = await self.installer.uninstall_server(server_name)
            return success, msg
        except Exception as e:
            logger.error(f"[MCPManager] Errore disinstallazione server '{server_name}': {e}")
            return False, f"Errore: {str(e)}"

    async def test_mcp_server(self, server_name: str) -> Tuple[bool, str]:
        """Tests if a server can be started and responds correctly."""
        try:
            success, msg = await self.installer.test_server(server_name)
            return success, msg
        except Exception as e:
            logger.error(f"[MCPManager] Errore test server '{server_name}': {e}")
            return False, f"Errore: {str(e)}"

    async def update_server_config(
        self, server_name: str, env_vars: str, enabled: bool
    ) -> Tuple[bool, str]:
        """Updates server configuration (env vars and enabled status)."""
        try:
            required_env = json.loads(env_vars) if env_vars else {}
            
            # Update env vars
            success, msg = await self.installer.update_server_env(server_name, required_env)
            if not success:
                return False, msg
            
            # Update enabled status
            success = self.config_loader.set_server_status(server_name, enabled)
            if success:
                return True, "Configurazione aggiornata con successo"
            else:
                return False, "Errore aggiornamento stato server"
        except Exception as e:
            logger.error(f"[MCPManager] Errore aggiornamento config server '{server_name}': {e}")
            return False, f"Errore: {str(e)}"

    async def get_installed_servers(self) -> str:
        """Returns list of installed MCP servers as JSON."""
        try:
            servers = self.config_loader.get_servers()
            result = []
            for name, cfg in servers.items():
                result.append({
                    "name": name,
                    "command": cfg.get("command"),
                    "description": cfg.get("description", ""),
                    "enabled": cfg.get("enabled", False),
                    "installed_at": cfg.get("installed_at"),
                    "env_keys": sorted(cfg.get("env", {}).keys()),
                })
            return json.dumps(result)
        except Exception as e:
            logger.error(f"[MCPManager] Errore caricamento server installati: {e}")
            return json.dumps([])
