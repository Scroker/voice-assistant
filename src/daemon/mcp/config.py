import os
import json
import logging
from pathlib import Path
from typing import Dict, Any

from core.data_loader import load_json_data

logger = logging.getLogger("VoiceAssistant.MCPConfig")

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "voice-assistant" / "mcp_servers.json"


def get_default_config() -> Dict[str, Any]:
    cfg = load_json_data("mcp/default_servers.json", fallback_default={})
    if not cfg or "mcpServers" not in cfg:
        return {
            "mcpServers": {
                "gnome-mcp-server": {
                    "command": "gnome-mcp-server",
                    "args": [],
                    "env": {},
                    "enabled": True,
                    "description": "GNOME desktop MCP server by Bilal Elmoussaoui (audio, apps, quick settings, notifications, windows)",
                }
            }
        }
    return cfg


DEFAULT_CONFIG: Dict[str, Any] = get_default_config()


class MCPConfigLoader:
    """Loader and manager for standard MCP server configurations (mcp_servers.json)."""

    def __init__(self, config_path: Path = DEFAULT_CONFIG_PATH):
        self.config_path = config_path

    def load(self) -> Dict[str, Any]:
        """Loads MCP servers config from disk or initializes default config."""
        default_cfg = get_default_config()
        if not self.config_path.exists():
            self.save(default_cfg)
            return default_cfg

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "mcpServers" not in data:
                    data["mcpServers"] = {}

                migrated = False
                # Migrazione da legacy "gnome-system" a "gnome-mcp-server"
                default_gnome = default_cfg.get("mcpServers", {}).get("gnome-mcp-server", {})
                if "gnome-system" in data["mcpServers"]:
                    legacy = data["mcpServers"].pop("gnome-system")
                    migrated = True
                    if "gnome-mcp-server" not in data["mcpServers"]:
                        data["mcpServers"]["gnome-mcp-server"] = dict(default_gnome)
                        data["mcpServers"]["gnome-mcp-server"]["enabled"] = legacy.get("enabled", True)

                if "gnome-mcp-server" not in data["mcpServers"]:
                    data["mcpServers"]["gnome-mcp-server"] = dict(default_gnome)
                    migrated = True

                if migrated:
                    self.save(data)

                return data
        except Exception as e:
            logger.error(f"Errore lettura configurazione MCP {self.config_path}: {e}")
            return DEFAULT_CONFIG

    def save(self, config: Dict[str, Any]) -> bool:
        """Saves MCP servers config to disk atomically."""
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.config_path.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            temp_path.replace(self.config_path)
            return True
        except Exception as e:
            logger.error(f"Errore salvataggio configurazione MCP {self.config_path}: {e}")
            return False

    def get_servers(self) -> Dict[str, Any]:
        """Returns map of active server configurations."""
        return self.load().get("mcpServers", {})

    def set_server_status(self, name: str, enabled: bool) -> bool:
        """Toggles active status of an MCP server."""
        config = self.load()
        if name in config.get("mcpServers", {}):
            config["mcpServers"][name]["enabled"] = enabled
            return self.save(config)
        return False
