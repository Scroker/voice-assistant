"""
Componente per la configurazione del Bug Reporting e il test di connessione Bugzilla.
"""

import json as _json
import threading
import urllib.error
import urllib.request
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gio', '2.0')
from gi.repository import Gtk, Adw, Gio, GLib

from .base import bind_setting


class BugReportSettings:
    """Configura la pagina Bug Reporting e gestisce il test di connessione."""

    def __init__(self, builder: Gtk.Builder, settings: Gio.Settings | None, parent_window: Gtk.Window | None = None):
        self.builder = builder
        self.settings = settings
        self.parent_window = parent_window
        self._setup()

    def _setup(self) -> None:
        bind_setting(self.settings, "bugreport-enabled",   self.builder, "bugreport_enable_row",    "active")
        bind_setting(self.settings, "bugreport-endpoint",  self.builder, "bugreport_endpoint_row",  "text")
        bind_setting(self.settings, "bugreport-api-key",   self.builder, "bugreport_apikey_row",    "text")
        bind_setting(self.settings, "bugreport-product",   self.builder, "bugreport_product_row",   "text")
        bind_setting(self.settings, "bugreport-component", self.builder, "bugreport_component_row", "text")

        test_btn = self.builder.get_object("test_bugreport_btn")
        if test_btn:
            sig = "activated" if isinstance(test_btn, Adw.ButtonRow) else "clicked"
            test_btn.connect(sig, self._on_test_bugreport)

    def _on_test_bugreport(self, _btn) -> None:
        if not self.settings:
            return

        endpoint = self.settings.get_string("bugreport-endpoint").strip()
        api_key = self.settings.get_string("bugreport-api-key").strip()

        if not endpoint or not api_key:
            dlg = Adw.AlertDialog(
                heading="Configurazione incompleta",
                body="Inserisci endpoint e API key prima di testare la connessione.",
            )
            dlg.add_response("ok", "OK")
            dlg.present(self.parent_window)
            return

        def _do_test():
            url = endpoint.rstrip("/") + "/rest/version"
            req = urllib.request.Request(url)
            req.add_header("X-BUGZILLA-API-KEY", api_key)
            try:
                with urllib.request.urlopen(req, timeout=8) as resp:
                    data = _json.loads(resp.read())
                    version = data.get("version", "sconosciuta")
                    GLib.idle_add(_show_result, True, f"Connessione OK — Bugzilla {version}")
            except urllib.error.HTTPError as e:
                GLib.idle_add(_show_result, False, f"Errore HTTP {e.code}: {e.reason}")
            except Exception as e:
                GLib.idle_add(_show_result, False, str(e))

        def _show_result(ok: bool, msg: str):
            dlg = Adw.AlertDialog(
                heading="Connessione riuscita" if ok else "Connessione fallita",
                body=msg,
            )
            dlg.add_response("ok", "OK")
            dlg.present(self.parent_window)

        threading.Thread(target=_do_test, daemon=True).start()
