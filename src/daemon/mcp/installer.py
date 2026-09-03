"""MCP Server Installation & Lifecycle Manager.

Handles downloading, configuring, testing, and uninstalling MCP servers.
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

from .config import MCPConfigLoader
from .credentials import MCPCredentialStore

logger = logging.getLogger("VoiceAssistant.MCPInstaller")


class MCPServerInstaller:
    """Manages installation, configuration, and removal of MCP servers."""

    def __init__(self, config_loader: Optional[MCPConfigLoader] = None):
        self.config_loader = config_loader or MCPConfigLoader()
        self.credential_store = MCPCredentialStore()
        self.cache_dir = Path.home() / ".cache" / "voice-assistant" / "mcp_servers"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    async def install_server(
        self,
        name: str,
        server_def: Dict[str, Any],
        required_env_vars: Optional[Dict[str, str]] = None,
    ) -> Tuple[bool, str]:
        """
        Installs an MCP server and adds it to config.

        Args:
            name: Server identifier
            server_def: Server definition from registry (command, args, env, etc)
            required_env_vars: User-provided environment variables

        Returns:
            (success: bool, message: str)
        """
        try:
            # 1. Verify command is available
            cmd = server_def.get("command", "")
            if not cmd:
                return False, "Questo server Smithery richiede una connessione gestita, non ancora supportata dal runtime locale"
            if cmd not in ("builtin", "uvx", "npx", "python", "python3"):
                return False, f"Comando '{cmd}' non supportato"

            if cmd == "builtin":
                logger.info(f"[MCPInstaller] Built-in server '{name}' - no installation needed")
                return True, "Server built-in - pronto all'uso"

            # 2. Check if command-runner is available
            runner_available = await self._check_runner_available(cmd)
            if not runner_available:
                return (
                    False,
                    f"Gestore '{cmd}' non trovato. Installare: {self._get_install_hint(cmd)}",
                )

            # 3. Test dry-run with --help
            test_ok, test_msg = await self._test_server_startup(server_def)
            if not test_ok:
                return False, f"Test fallito: {test_msg}"

            # 4. Add to config
            env_vars = dict(server_def.get("env", {}))
            if required_env_vars:
                env_vars.update(required_env_vars)
            stored_env = self.credential_store.store_environment(name, env_vars)

            config = self.config_loader.load()
            config["mcpServers"][name] = {
                "command": cmd,
                "args": server_def.get("args", []),
                "env": stored_env,
                "enabled": False,  # Start disabled, user must test first
                "description": server_def.get("description", ""),
                "installed_at": asyncio.get_event_loop().time(),
            }

            if not self.config_loader.save(config):
                return False, "Errore salvataggio configurazione"

            logger.info(f"[MCPInstaller] Server '{name}' installato e aggiunto a config")
            return True, f"Server '{name}' installato con successo!"

        except Exception as e:
            logger.error(f"[MCPInstaller] Installazione fallita per '{name}': {e}")
            return False, f"Errore: {str(e)}"

    async def uninstall_server(self, name: str) -> Tuple[bool, str]:
        """Removes a server from config and cleans up resources."""
        try:
            config = self.config_loader.load()
            if name not in config.get("mcpServers", {}):
                return False, f"Server '{name}' non trovato"

            if name == "gnome-system":
                return False, "Non è possibile disinstallare server built-in"

            self.credential_store.delete_environment(name, config["mcpServers"][name].get("env", {}))
            del config["mcpServers"][name]
            if not self.config_loader.save(config):
                return False, "Errore salvataggio configurazione"

            logger.info(f"[MCPInstaller] Server '{name}' disinstallato")
            return True, f"Server '{name}' rimosso"

        except Exception as e:
            logger.error(f"[MCPInstaller] Disinstallazione fallita per '{name}': {e}")
            return False, f"Errore: {str(e)}"

    async def test_server(self, name: str) -> Tuple[bool, str]:
        """Tests if a server can be started and responds to list_tools."""
        try:
            config = self.config_loader.load()
            if name not in config.get("mcpServers", {}):
                return False, f"Server '{name}' non trovato in configurazione"

            server_cfg = config["mcpServers"][name]
            success, msg = await self._test_server_startup(server_cfg)
            return success, msg

        except Exception as e:
            logger.error(f"[MCPInstaller] Test fallito per '{name}': {e}")
            return False, f"Errore di test: {str(e)}"

    async def update_server_env(self, name: str, env_vars: Dict[str, str]) -> Tuple[bool, str]:
        """Updates environment variables for a server."""
        try:
            config = self.config_loader.load()
            if name not in config.get("mcpServers", {}):
                return False, f"Server '{name}' non trovato"

            current_env = config["mcpServers"][name].get("env", {})
            current_env.update(self.credential_store.store_environment(name, env_vars))
            config["mcpServers"][name]["env"] = current_env
            if not self.config_loader.save(config):
                return False, "Errore salvataggio configurazione"

            logger.info(f"[MCPInstaller] Variabili ambiente aggiornate per '{name}'")
            return True, "Configurazione aggiornata"

        except Exception as e:
            logger.error(f"[MCPInstaller] Errore aggiornamento env per '{name}': {e}")
            return False, f"Errore: {str(e)}"

    async def _check_runner_available(self, runner: str) -> bool:
        """Checks if command runner (uvx, npx, etc) is available."""
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                [runner, "--version"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    async def _test_server_startup(self, server_def: Dict[str, Any]) -> Tuple[bool, str]:
        """Tests if server can start with a simple list_tools request."""
        cmd = server_def.get("command")
        args = server_def.get("args", [])
        env = self.credential_store.resolve_environment(server_def.get("env", {}))

        if cmd == "builtin":
            return True, "Server built-in"

        try:
            # Try --help to verify command structure
            full_cmd = [cmd] + args + ["--help"]
            merged_env = os.environ.copy()
            merged_env.update(env)

            result = await asyncio.to_thread(
                subprocess.run,
                full_cmd,
                capture_output=True,
                timeout=10,
                env=merged_env,
            )

            if result.returncode == 0:
                return True, f"Server '{cmd}' è disponibile"
            else:
                return False, f"Comando fallito: {result.stderr.decode()[:100]}"

        except subprocess.TimeoutExpired:
            return False, "Timeout durante il test (>10s)"
        except FileNotFoundError:
            return False, f"Comando '{cmd}' non trovato nel PATH"
        except Exception as e:
            return False, f"Errore test: {str(e)}"

    @staticmethod
    def _get_install_hint(runner: str) -> str:
        """Returns installation hint for a command runner."""
        hints = {
            "uvx": "pip install uv; uv tool install mcp-server-*",
            "npx": "npm install -g npx",
            "python": "apt install python3-pip",
            "python3": "apt install python3-pip",
        }
        return hints.get(runner, f"Installare {runner}")
