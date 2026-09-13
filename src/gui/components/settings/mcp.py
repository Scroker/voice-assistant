"""
Componente per la configurazione di Model Context Protocol (MCP).
"""

import json
import logging
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gio', '2.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Gio, Adw

from .base import bind_setting

_log = logging.getLogger("VoiceAssistant.GUI.Settings.MCP")


class MCPSettings:
    """Configura la pagina Model Context Protocol (MCP)."""

    def __init__(
        self,
        builder: Gtk.Builder | None = None,
        settings: Gio.Settings | None = None,
        enable_row: Adw.SwitchRow | None = None,
        registry_url_row: Adw.EntryRow | None = None,
        server_gnome_row: Adw.ActionRow | None = None,
        servers_group: Adw.PreferencesGroup | None = None,
        mcp_page: Adw.NavigationPage | None = None,
    ):
        self.builder = builder
        self.mcp_page = mcp_page or (builder.get_object("mcp_subpage") if builder else None)
        self.settings = settings
        self.enable_row = enable_row or (builder.get_object("mcp_enable_row") if builder else None)
        self.registry_url_row = registry_url_row or (builder.get_object("mcp_registry_url_row") if builder else None)
        self.server_gnome_row = server_gnome_row or (builder.get_object("mcp_server_gnome_row") if builder else None)
        self.servers_group = servers_group or (builder.get_object("mcp_servers_group") if builder else None)
        self._setup()

    def _setup(self) -> None:
        if self.settings:
            if self.enable_row:
                self.settings.bind("mcp-enabled", self.enable_row, "active", Gio.SettingsBindFlags.DEFAULT)
            elif self.builder:
                bind_setting(self.settings, "mcp-enabled", self.builder, "mcp_enable_row", "active")

            if self.registry_url_row:
                self.settings.bind("mcp-registry-url", self.registry_url_row, "text", Gio.SettingsBindFlags.DEFAULT)
            elif self.builder:
                bind_setting(self.settings, "mcp-registry-url", self.builder, "mcp_registry_url_row", "text")

        self.reload()

    def reload(self) -> None:
        server_row = self.server_gnome_row or (self.builder.get_object("mcp_server_gnome_row") if self.builder else None)
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
