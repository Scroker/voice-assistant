# Voice Assistant GNOME Extension
# Copyright (C) 2026 Giorgio Dramis
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""
Componente riutilizzabile per la gestione delle dipendenze Python mancanti.

Mostra un unico Adw.AlertDialog consolidato che chiede il consenso esplicito
all'utente prima di installare uno o più pacchetti nel virtualenv del daemon.

Uso:
    from dependency_installer import show_missing_deps_dialog
    show_missing_deps_dialog(parent_window, [
        {"package": "sherpa-onnx", "description": "Motore Wake Word Sherpa-ONNX", "is_critical": False},
        {"package": "vosk",        "description": "Riconoscimento vocale Vosk",   "is_critical": False},
    ])
"""

import os
import subprocess
import threading

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk, GLib

_VENV_PIP = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "daemon", "venv", "bin", "pip")
)


def show_missing_deps_dialog(
    parent: Gtk.Widget,
    deps: list,
    on_done=None,
) -> None:
    """
    Mostra un unico Adw.AlertDialog per tutte le dipendenze mancanti.

    Args:
        parent:  widget padre (tipicamente AssistantWindow)
        deps:    lista di dict {package: str, description: str, is_critical: bool}
        on_done: callable(success: bool) opzionale, chiamata al termine
    """
    if not deps:
        return

    packages = [d["package"] for d in deps]
    bullet_list = "\n".join(f"• {d['description']} ({d['package']})" for d in deps)
    plural = len(deps) > 1

    dlg = Adw.AlertDialog(
        heading="Dipendenze mancanti" if plural else "Dipendenza mancante",
        body=(
            f"{'I seguenti pacchetti non sono' if plural else 'Il seguente pacchetto non è'} "
            f"installato nel virtualenv:\n\n{bullet_list}\n\n"
            f"{'Vuoi installarli' if plural else 'Vuoi installarlo'} ora?"
        ),
    )
    dlg.add_response("skip", "Salta")
    dlg.add_response("install", "Installa tutto" if plural else "Installa")
    dlg.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
    dlg.set_default_response("install")
    dlg.set_close_response("skip")
    dlg.connect("response", _on_consent_response, packages, parent, on_done)
    dlg.present(parent)


def _on_consent_response(dlg, response, packages, parent, on_done):
    if response != "install":
        if on_done:
            on_done(False)
        return
    _run_install(packages, parent, on_done)


def _run_install(packages: list, parent: Gtk.Widget, on_done) -> None:
    pkg_list = " ".join(packages)
    progress_dlg = Adw.AlertDialog(
        heading="Installazione in corso…",
        body=f"pip install {pkg_list}",
    )
    spinner = Gtk.Spinner(spinning=True)
    spinner.set_margin_top(8)
    spinner.set_size_request(32, 32)
    progress_dlg.set_extra_child(spinner)
    progress_dlg.present(parent)

    def _do_pip():
        pip = _VENV_PIP if os.path.isfile(_VENV_PIP) else "pip3"
        result = subprocess.run(
            [pip, "install", "--prefer-binary"] + packages,
            capture_output=True,
            text=True,
        )
        GLib.idle_add(_on_install_done, progress_dlg, result, packages, parent, on_done)

    threading.Thread(target=_do_pip, daemon=True).start()


def _on_install_done(progress_dlg, result, packages, parent, on_done):
    progress_dlg.close()

    success = result.returncode == 0
    if success:
        res_dlg = Adw.AlertDialog(
            heading="Installazione completata",
            body=(
                f"{'I pacchetti sono stati installati' if len(packages) > 1 else f'{packages[0]} è stato installato'} "
                "correttamente.\n\n"
                "Riavvia l'assistente vocale per attivare le nuove funzionalità."
            ),
        )
    else:
        stderr_excerpt = (result.stderr or "").strip()[-400:]
        res_dlg = Adw.AlertDialog(
            heading="Installazione fallita",
            body=(
                f"Impossibile installare {'i pacchetti' if len(packages) > 1 else packages[0]}.\n\n"
                f"{stderr_excerpt}"
            ),
        )

    res_dlg.add_response("ok", "OK")
    res_dlg.present(parent)

    if on_done:
        on_done(success)

    return GLib.SOURCE_REMOVE
