# Voice Assistant GNOME Extension
# Copyright (C) 2026 Giorgio Dramis
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""
BugReporter — invia report di eccezioni a un'istanza Bugzilla via REST API.

Viene invocato dagli hook globali di logger.py (tramite callback) e da
VoiceAssistant._report_error() per gli errori nel thread audio.

Le ImportError vengono filtrate automaticamente: sono già gestite dal
componente dependency_installer e non devono essere segnalate come bug.

La configurazione (endpoint, API key, prodotto, componente) viene letta
lazily dalle GSettings al momento dell'invio — nessuna inizializzazione
anticipata richiesta.

Auth Bugzilla REST API: header X-BUGZILLA-API-KEY (standard Bugzilla v5+).
"""

import json
import logging
import threading
import urllib.request
import urllib.error
from typing import Optional, Type

logger = logging.getLogger("VoiceAssistant.BugReporter")

_SCHEMA_ID = "org.gnome.shell.extensions.voice-assistant"


class BugReporter:
    _settings = None
    _settings_lock = threading.Lock()

    @classmethod
    def _get_settings(cls):
        """Crea/restituisce l'istanza GSettings in modo lazy e thread-safe."""
        with cls._settings_lock:
            if cls._settings is None:
                try:
                    import gi
                    gi.require_version("Gio", "2.0")
                    from gi.repository import Gio
                    cls._settings = Gio.Settings.new(_SCHEMA_ID)
                except Exception as e:
                    logger.debug("Impossibile caricare GSettings per BugReporter: %s", e)
            return cls._settings

    @classmethod
    def submit_async(
        cls,
        exc_type: Type[BaseException],
        exc_value: BaseException,
        exc_traceback,
        component: str = "unknown",
    ) -> None:
        """
        Invia il report di un'eccezione a Bugzilla in background.

        Filtra ImportError (gestite dal dependency installer).
        Non bloccante: lancia un thread daemon per la chiamata HTTP.
        """
        if issubclass(exc_type, ImportError):
            return

        settings = cls._get_settings()
        if settings is None or not settings.get_boolean("bugreport-enabled"):
            return

        endpoint = settings.get_string("bugreport-endpoint").strip()
        api_key = settings.get_string("bugreport-api-key").strip()
        if not endpoint or not api_key:
            return

        product = settings.get_string("bugreport-product") or "Voice Assistant"
        bz_component = settings.get_string("bugreport-component") or "Daemon"

        import traceback as tb_mod
        traceback_str = "".join(tb_mod.format_exception(exc_type, exc_value, exc_traceback))

        threading.Thread(
            target=cls._post_bug,
            args=(
                endpoint, api_key, product, bz_component,
                exc_type.__name__, str(exc_value), traceback_str, component,
            ),
            daemon=True,
        ).start()

    @classmethod
    def _post_bug(
        cls,
        endpoint: str,
        api_key: str,
        product: str,
        bz_component: str,
        error_type: str,
        message: str,
        traceback_str: str,
        component: str,
    ) -> None:
        """Costruisce il payload JSON e fa POST /rest/bug. Logga successo/errore."""
        summary = f"[{component}] {error_type}: {message[:80]}"
        description = (
            f"Componente: {component}\n"
            f"Tipo errore: {error_type}\n"
            f"Messaggio: {message}\n\n"
            f"Traceback:\n```\n{traceback_str}\n```"
        )

        url = endpoint.rstrip("/") + "/rest/bug"
        payload = json.dumps({
            "product": product,
            "component": bz_component,
            "summary": summary,
            "description": description,
            "version": "1.0",
            "severity": "normal",
            "op_sys": "Linux",
            "platform": "PC",
        }).encode("utf-8")

        req = urllib.request.Request(url, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("X-BUGZILLA-API-KEY", api_key)

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
                bug_id = result.get("id")
                logger.info("Bug report inviato a Bugzilla: #%s", bug_id)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:200]
            logger.warning("Bugzilla HTTP %d: %s", e.code, body)
        except Exception as e:
            logger.warning("Impossibile inviare bug report a Bugzilla: %s", e)
