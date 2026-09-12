"""
Client D-Bus per la comunicazione con il demone dell'Assistente Vocale.
"""

import json
import logging
import threading
from typing import Callable, Any

import gi
gi.require_version('Gio', '2.0')
gi.require_version('GLib', '2.0')
from gi.repository import Gio, GLib

_log = logging.getLogger("VoiceAssistant.GUI.DaemonClient")

_DBUS_NAME = "org.local.VoiceAssistant"
_DBUS_PATH = "/org/local/VoiceAssistant"
_DBUS_IFACE = "org.local.VoiceAssistant"


class DaemonClient:
    """Gestisce la connessione D-Bus asincrona e i metodi/segnali con il demone."""

    def __init__(
        self,
        on_transcript: Callable[[str, bool], None] | None = None,
        on_token: Callable[[str, bool], None] | None = None,
        on_state_changed: Callable[[str], None] | None = None,
        on_dependency_required: Callable[[], None] | None = None,
        on_ready: Callable[['DaemonClient'], None] | None = None,
    ):
        self._proxy: Gio.DBusProxy | None = None
        self._on_transcript = on_transcript
        self._on_token = on_token
        self._on_state_changed = on_state_changed
        self._on_dependency_required = on_dependency_required
        self._on_ready = on_ready

        # Inizializzazione asincrona del proxy
        Gio.DBusProxy.new_for_bus(
            Gio.BusType.SESSION,
            Gio.DBusProxyFlags.NONE,
            None,
            _DBUS_NAME,
            _DBUS_PATH,
            _DBUS_IFACE,
            None,
            self._on_proxy_created,
        )

    @property
    def is_ready(self) -> bool:
        return self._proxy is not None

    def _on_proxy_created(self, source: GLib.Object, result: Gio.AsyncResult) -> None:
        try:
            self._proxy = Gio.DBusProxy.new_for_bus_finish(result)
            self._proxy.connect("g-signal", self._on_dbus_signal)
            _log.debug("DaemonClient: D-Bus proxy pronto")
            if self._on_ready:
                self._on_ready(self)
        except Exception as e:
            _log.error("DaemonClient: connessione D-Bus fallita: %s", e)

    def _on_dbus_signal(
        self,
        proxy: Gio.DBusProxy,
        sender: str,
        signal_name: str,
        params: GLib.Variant,
    ) -> None:
        try:
            if signal_name == "TranscriptReceived" and self._on_transcript:
                text, is_final = params.unpack()
                self._on_transcript(text, is_final)
            elif signal_name == "ResponseTokenStreamed" and self._on_token:
                token, is_complete = params.unpack()
                self._on_token(token, is_complete)
            elif signal_name == "StateChanged" and self._on_state_changed:
                state = params.unpack()[0]
                self._on_state_changed(state)
            elif signal_name == "DependencyRequired" and self._on_dependency_required:
                self._on_dependency_required()
        except Exception as e:
            _log.error("Errore gestione segnale D-Bus %s: %s", signal_name, e)

    def call_async(
        self,
        method: str,
        params: GLib.Variant | None = None,
        callback: Callable[[Any, Exception | None], None] | None = None,
    ) -> None:
        """Chiama un metodo D-Bus in un thread in background per non bloccare la UI."""
        if not self._proxy:
            _log.warning("DaemonClient: proxy non disponibile, metodo '%s' ignorato.", method)
            if callback:
                callback(None, RuntimeError("Proxy non disponibile"))
            return

        def _do_call():
            try:
                res = self._proxy.call_sync(
                    method,
                    params,
                    Gio.DBusCallFlags.NONE,
                    5000,
                    None,
                )
                if callback:
                    GLib.idle_add(callback, res, None)
            except Exception as e:
                _log.error("Errore chiamata D-Bus %s: %s", method, e)
                if callback:
                    GLib.idle_add(callback, None, e)

        threading.Thread(target=_do_call, daemon=True).start()

    def send_text(self, text: str) -> None:
        """Invia un input di testo al demone."""
        self.call_async("ProcessTextInput", GLib.Variant("(s)", (text,)))

    def trigger_listening(self) -> None:
        """Attiva l'ascolto vocale (Push-to-Talk) sul demone."""
        self.call_async("TriggerListening")

    def get_missing_dependencies_sync(self) -> list:
        """Recupera le dipendenze mancanti chiamando GetMissingDependencies (sync)."""
        if not self._proxy:
            return []
        try:
            res = self._proxy.call_sync(
                "GetMissingDependencies", None, Gio.DBusCallFlags.NONE, 3000, None
            )
            raw = res.unpack()[0]
            return json.loads(raw)
        except Exception as e:
            _log.error("DaemonClient: errore GetMissingDependencies: %s", e)
            return []

    def close(self) -> None:
        """Chiude il client D-Bus e azzera i callback."""
        self._on_transcript = None
        self._on_token = None
        self._on_state_changed = None
        self._on_dependency_required = None
        self._on_ready = None
        self._proxy = None
