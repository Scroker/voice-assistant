"""
Componente per la configurazione di Model Context Protocol (MCP).
"""

import json
import logging
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gio', '2.0')
from gi.repository import Gtk, Gio

from .base import bind_setting

_log = logging.getLogger("VoiceAssistant.GUI.Settings.MCP")


class MCPSettings:
    """Configura la pagina Model Context Protocol (MCP)."""

    def __init__(self, builder: Gtk.Builder, settings: Gio.Settings | None):
        self.builder = builder
        self.settings = settings
        self._setup()

    def _setup(self) -> None:
        bind_setting(self.settings, "mcp-enabled", self.builder, "mcp_enable_row", "active")
        bind_setting(self.settings, "mcp-registry-url", self.builder, "mcp_registry_url_row", "text")

        # Visualizza i server configurati da mcp-servers
        server_row = self.builder.get_object("mcp_server_gnome_row")
        if server_row and self.settings:
            try:
                servers_json = self.settings.get_string("mcp-servers")
                if servers_json:
                    servers = json.loads(servers_json)
                    if isinstance(servers, list) and len(servers) > 0:
                        first = servers[0]
                        name = first.get("name", "GNOME MCP Server")
                        status = "Attivo" if first.get("enabled", True) else "Disabilitato"
                        server_row.set_title(name)
                        server_row.set_subtitle(f"{first.get('command', '')} — {status}")
            except Exception as e:
                _log.warning("Errore lettura mcp-servers: %s", e)
