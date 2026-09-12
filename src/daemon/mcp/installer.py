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
from core.data_loader import load_json_data

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
            if cmd not in ("builtin", "uvx", "npx", "python", "python3", "gnome-mcp-server", "cargo"):
                return False, f"Comando '{cmd}' non supportato"

            if cmd == "builtin":
                logger.info(f"[MCPInstaller] Built-in server '{name}' - no installation needed")
                return True, "Server built-in - pronto all'uso"

            # 2. Check if command-runner is available
            runner_available = await self._check_runner_available(cmd)
            if not runner_available:
                if cmd == "gnome-mcp-server":
                    # Attempt automated cargo install if cargo is available
                    cargo_available = await self._check_runner_available("cargo")
                    if cargo_available:
                        install_ok, install_msg = await self.install_gnome_mcp_server()
                        if not install_ok:
                            return False, install_msg
                    else:
                        return (
                            False,
                            f"Gestore 'cargo' non trovato. Installare prima il compilatore Rust: {self._get_install_hint('cargo')}",
                        )
                else:
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

            if name in ("gnome-system", "gnome-mcp-server"):
                return False, "Non è possibile disinstallare il server MCP desktop predefinito"

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
        """Checks if command runner (uvx, npx, cargo, gnome-mcp-server, etc) is available."""
        if runner == "gnome-mcp-server":
            cargo_candidate = os.path.expanduser("~/.cargo/bin/gnome-mcp-server")
            if os.path.isfile(cargo_candidate) and os.access(cargo_candidate, os.X_OK):
                return True
            import shutil
            return shutil.which("gnome-mcp-server") is not None

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

        if cmd == "gnome-mcp-server":
            cargo_candidate = os.path.expanduser("~/.cargo/bin/gnome-mcp-server")
            import shutil
            if not shutil.which("gnome-mcp-server") and os.path.isfile(cargo_candidate):
                cmd = cargo_candidate

        try:
            # Try --help to verify command structure
            full_cmd = [cmd] + args + ["--help"]
            merged_env = os.environ.copy()
            merged_env.update(env)
            cargo_bin = os.path.expanduser("~/.cargo/bin")
            if cargo_bin not in merged_env.get("PATH", "").split(os.pathsep):
                merged_env["PATH"] = f"{cargo_bin}:{merged_env.get('PATH', '')}"

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

    async def install_gnome_mcp_server(self) -> Tuple[bool, str]:
        """Compiles and installs gnome-mcp-server via Cargo."""
        try:
            logger.info("[MCPInstaller] Installazione gnome-mcp-server via cargo...")
            import shutil
            cargo_path = shutil.which("cargo")
            if not cargo_path:
                for candidate in ("/usr/bin/cargo", os.path.expanduser("~/.cargo/bin/cargo")):
                    if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                        cargo_path = candidate
                        break
            if not cargo_path:
                cargo_path = "cargo"

            env = os.environ.copy()
            cargo_bin = os.path.expanduser("~/.cargo/bin")
            if cargo_bin not in env.get("PATH", "").split(os.pathsep):
                env["PATH"] = f"{cargo_bin}:{env.get('PATH', '')}"

            default_servers = load_json_data("mcp/default_servers.json", fallback_default={})
            gnome_cfg = default_servers.get("mcpServers", {}).get("gnome-mcp-server", {})
            repo_url = gnome_cfg.get("repo_url", "https://github.com/bilelmoussaoui/gnome-mcp-server")

            result = await asyncio.to_thread(
                subprocess.run,
                [cargo_path, "install", "--git", repo_url],
                capture_output=True,
                text=True,
                env=env,
                timeout=600,
            )
            if result.returncode == 0:
                logger.info("[MCPInstaller] gnome-mcp-server installato con successo.")
                return True, "gnome-mcp-server installato con successo via cargo"
            err = (result.stderr or result.stdout or "").strip()[-300:]
            return False, f"Compilazione cargo fallita:\n{err}"
        except Exception as e:
            return False, f"Errore durante l'installazione di gnome-mcp-server: {e}"

    @staticmethod
    def _get_install_hint(runner: str) -> str:
        """Returns installation hint for a command runner."""
        hints = load_json_data("mcp/runner_hints.json", fallback_default={})
        return hints.get(runner, f"Installare {runner}")
