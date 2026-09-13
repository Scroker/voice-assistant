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
        on_conversation_transcript: Callable[[str, str, bool], None] | None = None,
        on_conversation_token: Callable[[str, str, bool], None] | None = None,
        on_conversation_created: Callable[[str, str], None] | None = None,
        on_state_changed: Callable[[str], None] | None = None,
        on_dependency_required: Callable[[], None] | None = None,
        on_ready: Callable[['DaemonClient'], None] | None = None,
    ):
        self._proxy: Gio.DBusProxy | None = None
        self._on_transcript = on_transcript
        self._on_token = on_token
        self._on_conversation_transcript = on_conversation_transcript
        self._on_conversation_token = on_conversation_token
        self._on_conversation_created = on_conversation_created
        self._on_state_changed = on_state_changed
        self._on_dependency_required = on_dependency_required
        self._on_ready = on_ready

        self._subscribers: dict[str, dict[int, Callable[..., None]]] = {}
        self._next_handler_id = 1
        self._subscribers_lock = threading.Lock()

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
            elif signal_name == "ConversationToken" and self._on_conversation_token:
                ctx_id, token, is_complete = params.unpack()
                self._on_conversation_token(ctx_id, token, is_complete)
            elif signal_name == "ConversationTranscript" and self._on_conversation_transcript:
                ctx_id, text, is_final = params.unpack()
                self._on_conversation_transcript(ctx_id, text, is_final)
            elif signal_name == "ConversationCreated" and self._on_conversation_created:
                ctx_id, reason = params.unpack()
                self._on_conversation_created(ctx_id, reason)
            elif signal_name == "StateChanged" and self._on_state_changed:
                state = params.unpack()[0]
                self._on_state_changed(state)
            elif signal_name == "DependencyRequired" and self._on_dependency_required:
                self._on_dependency_required()

            with self._subscribers_lock:
                handlers = list(self._subscribers.get(signal_name, {}).values())
            if handlers:
                unpacked = params.unpack() if params is not None else ()
                for handler in handlers:
                    try:
                        GLib.idle_add(handler, *unpacked)
                    except Exception as he:
                        _log.error("Errore notifica subscriber %s: %s", signal_name, he)
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

    def toggle_listening(self) -> None:
        """Alterna l'ascolto vocale sul demone."""
        self.call_async("ToggleListening")

    def trigger_listening_in_context(self, context_id: str) -> None:
        """Attiva l'ascolto vocale sul demone legato a una specifica chat."""
        self.call_async("TriggerListeningInContext", GLib.Variant("(s)", (context_id,)))

    def toggle_listening_in_context(self, context_id: str) -> None:
        """Alterna l'ascolto vocale sul demone legato a una specifica chat."""
        self.call_async("ToggleListeningInContext", GLib.Variant("(s)", (context_id,)))

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

    def send_text_in_context(self, text: str, context_id: str) -> None:
        """Invia un input di testo a uno specifico contesto."""
        self.call_async("ProcessTextInContext", GLib.Variant("(ss)", (text, context_id)))

    def list_conversations_sync(self) -> list:
        if not self._proxy:
            return []
        try:
            res = self._proxy.call_sync("ListConversations", None, Gio.DBusCallFlags.NONE, 3000, None)
            return json.loads(res.unpack()[0])
        except Exception as e:
            _log.error("DaemonClient: errore ListConversations: %s", e)
            return []

    def create_conversation_sync(self) -> str:
        if not self._proxy:
            return ""
        try:
            res = self._proxy.call_sync("CreateConversation", None, Gio.DBusCallFlags.NONE, 3000, None)
            return res.unpack()[0]
        except Exception as e:
            _log.error("DaemonClient: errore CreateConversation: %s", e)
            return ""

    def delete_conversation_sync(self, context_id: str) -> bool:
        if not self._proxy:
            return False
        try:
            res = self._proxy.call_sync("DeleteConversation", GLib.Variant("(s)", (context_id,)), Gio.DBusCallFlags.NONE, 3000, None)
            return res.unpack()[0]
        except Exception as e:
            _log.error("DaemonClient: errore DeleteConversation: %s", e)
            return False

    def get_conversation_messages_sync(self, context_id: str) -> list:
        if not self._proxy:
            return []
        try:
            res = self._proxy.call_sync("GetConversationMessages", GLib.Variant("(s)", (context_id,)), Gio.DBusCallFlags.NONE, 3000, None)
            return json.loads(res.unpack()[0])
        except Exception as e:
            _log.error("DaemonClient: errore GetConversationMessages: %s", e)
            return []

    def subscribe(self, signal_name: str, callback: Callable[..., None]) -> int:
        """Sottoscrive una callback a un segnale D-Bus. Ritorna un handler_id."""
        with self._subscribers_lock:
            hid = self._next_handler_id
            self._next_handler_id += 1
            if signal_name not in self._subscribers:
                self._subscribers[signal_name] = {}
            self._subscribers[signal_name][hid] = callback
            return hid

    def unsubscribe(self, signal_name: str, handler_id: int) -> None:
        """Rimuove la sottoscrizione identificata da handler_id."""
        with self._subscribers_lock:
            if signal_name in self._subscribers:
                self._subscribers[signal_name].pop(handler_id, None)

    def start_speaker_enrollment(
        self,
        name: str,
        duration: float = 8.0,
        callback: Callable[[bool, Exception | None], None] | None = None,
    ) -> None:
        """Avvia la registrazione vocale asincrona."""
        def _cb(res, exc):
            if callback:
                if exc:
                    callback(False, exc)
                else:
                    success = bool(res.unpack()[0]) if res else False
                    callback(success, None)

        self.call_async(
            "StartSpeakerEnrollment",
            GLib.Variant("(sd)", (name, float(duration))),
            _cb,
        )

    def cancel_speaker_enrollment(
        self,
        callback: Callable[[bool, Exception | None], None] | None = None,
    ) -> None:
        """Annulla la registrazione vocale in corso."""
        def _cb(res, exc):
            if callback:
                if exc:
                    callback(False, exc)
                else:
                    success = bool(res.unpack()[0]) if res else False
                    callback(success, None)

        self.call_async("CancelSpeakerEnrollment", None, _cb)

    def get_speaker_status(
        self,
        callback: Callable[[dict, Exception | None], None],
    ) -> None:
        """Recupera lo stato del componente speaker ID (disponibilità, download in corso)."""
        def _cb(res, exc):
            if exc:
                callback({}, exc)
            else:
                try:
                    raw = res.unpack()[0] if res else "{}"
                    data = json.loads(raw) if raw else {}
                    callback(data, None)
                except Exception as e:
                    callback({}, e)

        self.call_async("GetSpeakerStatus", None, _cb)

    def get_speaker_status_sync(self) -> dict:
        """Recupera lo stato del componente speaker ID in modo sincrono."""
        if not self._proxy:
            return {}
        try:
            res = self._proxy.call_sync("GetSpeakerStatus", None, Gio.DBusCallFlags.NONE, 2000, None)
            raw = res.unpack()[0] if res else "{}"
            return json.loads(raw) if raw else {}
        except Exception as e:
            _log.debug("DaemonClient: errore GetSpeakerStatus: %s", e)
            return {}

    def get_speaker_profiles(
        self,
        callback: Callable[[list[dict], Exception | None], None],
    ) -> None:
        """Recupera l'elenco dei profili vocali registrati in modo asincrono."""
        def _cb(res, exc):
            if exc:
                callback([], exc)
            else:
                try:
                    raw = res.unpack()[0] if res else "[]"
                    data = json.loads(raw)
                    callback(data, None)
                except Exception as e:
                    callback([], e)

        self.call_async("GetSpeakerProfiles", None, _cb)

    def get_speaker_profiles_sync(self) -> list[dict]:
        """Recupera l'elenco dei profili vocali registrati in modo sincrono."""
        if not self._proxy:
            return []
        try:
            res = self._proxy.call_sync("GetSpeakerProfiles", None, Gio.DBusCallFlags.NONE, 3000, None)
            return json.loads(res.unpack()[0])
        except Exception as e:
            _log.error("DaemonClient: errore GetSpeakerProfiles: %s", e)
            return []

    def delete_speaker_profile(
        self,
        profile_id: str,
        callback: Callable[[bool, Exception | None], None] | None = None,
    ) -> None:
        """Elimina un profilo vocale tramite il suo ID."""
        def _cb(res, exc):
            if callback:
                if exc:
                    callback(False, exc)
                else:
                    success = bool(res.unpack()[0]) if res else False
                    callback(success, None)

        self.call_async(
            "DeleteSpeakerProfile",
            GLib.Variant("(s)", (profile_id,)),
            _cb,
        )

    def close(self) -> None:
        """Chiude il client D-Bus e azzera i callback."""
        self._on_transcript = None
        self._on_token = None
        self._on_state_changed = None
        self._on_dependency_required = None
        self._on_ready = None
        with self._subscribers_lock:
            self._subscribers.clear()
        self._proxy = None

