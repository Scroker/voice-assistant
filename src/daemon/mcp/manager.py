import json
import logging
import asyncio
from typing import Dict, Any, List, Optional, Union, Tuple

from .config import MCPConfigLoader
from .registry import MCPRegistryClient
from .installer import MCPServerInstaller
from .client import ExternalMCPClient
from .tools import (
    NativeTool,
    SystemVolumeTool,
    DarkModeTool,
    AppLauncherTool,
    DateTimeTool,
    SystemMediaTool,
    ScreenBrightnessTool,
    SystemPowerTool,
    ClipboardTool,
)

logger = logging.getLogger("VoiceAssistant.MCPManager")

class MCPManager:
    """Central MCP execution & aggregation engine for native GNOME tools & external MCP servers."""

    def __init__(self, config_loader: Optional[MCPConfigLoader] = None,
                 registry_url: Optional[str] = None):
        self.config_loader = config_loader or MCPConfigLoader()
        self.registry_client = MCPRegistryClient(
            registry_url=registry_url or "https://api.smithery.ai",
            config_loader=self.config_loader,
        )
        self.installer = MCPServerInstaller(config_loader=self.config_loader)
        self.native_tools: Dict[str, NativeTool] = {}
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
        """Initializes native GNOME tools and loads configured external MCP servers."""
        # 1. Register Built-in Native Tools
        self.register_native_tool(SystemVolumeTool())
        self.register_native_tool(DarkModeTool())
        self.register_native_tool(AppLauncherTool())
        self.register_native_tool(DateTimeTool())
        self.register_native_tool(SystemMediaTool())
        self.register_native_tool(ScreenBrightnessTool())
        self.register_native_tool(SystemPowerTool())
        self.register_native_tool(ClipboardTool())

        # 2. Load Config & Connect to External Stdio/SSE Servers
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

        logger.info(f"MCPManager inizializzato. Tools nativi: {len(self.native_tools)}, Server esterni attivi: {len(self.external_clients)}.")

    def register_native_tool(self, tool: NativeTool):
        """Registers a native GNOME tool instance."""
        self.native_tools[tool.name] = tool

    def set_registry_url(self, registry_url: str):
        """Switch the marketplace source without changing installed servers."""
        self.registry_client.registry_url = registry_url.rstrip("/")

    def get_tools_schema(self) -> List[Dict[str, Any]]:
        """Returns list of schemas for all active native and external tools."""
        if not self._enabled:
            return []

        schemas = []
        # Native tools
        for tool in self.native_tools.values():
            schemas.append(tool.to_schema())

        # External tools
        for schema in self.external_tools_schemas.values():
            schemas.append(schema)

        return schemas

    def format_system_prompt_tools(self) -> str:
        """Formats active tool schemas into a readable system prompt string for LLMs."""
        schemas = self.get_tools_schema()
        if not schemas:
            return ""

        lines = [
            "### Strumenti e Tool Disponibili:",
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
        """Executes a tool by name (native or external)."""
        if not self._enabled:
            return "Integrazione MCP disabilitata nelle impostazioni."

        # 1. Native Tool Execution
        if tool_name in self.native_tools:
            try:
                logger.info(f"Esecuzione tool nativo '{tool_name}' con args: {args}")
                return await self.native_tools[tool_name].execute(args)
            except Exception as e:
                logger.error(f"Errore durante l'esecuzione del tool nativo '{tool_name}': {e}")
                return f"Errore nell'esecuzione del tool '{tool_name}': {e}"

        # 2. External MCP Server Execution
        if tool_name in self.external_tools_map:
            server_name = self.external_tools_map[tool_name]
            client = self.external_clients.get(server_name)
            if client:
                try:
                    logger.info(f"Esecuzione tool esterno '{tool_name}' su server '{server_name}'")
                    return await client.call_tool(tool_name, args)
                except Exception as e:
                    logger.error(f"Errore durante l'esecuzione del tool esterno '{tool_name}' su '{server_name}': {e}")
                    return f"Errore nell'esecuzione del tool '{tool_name}': {e}"

        # 3. Fallback: If LLM outputs app name directly as tool name, route to app_launcher
        if "app_launcher" in self.native_tools:
            app_arg = args.get("app_name") or args.get("app") or tool_name
            res = await self.native_tools["app_launcher"].execute({"app_name": app_arg})
            if "Impossibile trovare" not in res:
                logger.info(f"Tool '{tool_name}' non nativo, eseguito tramite 'app_launcher' per l'app '{app_arg}'")
                return res

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
        Installs an MCP server and adds it to config.

        Returns:
            (success: bool, message: str)
        """
        try:
            server_def = json.loads(server_config)
            required_env = json.loads(env_vars) if env_vars else {}
            success, msg = await self.installer.install_server(
                server_name, server_def, required_env
            )
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
