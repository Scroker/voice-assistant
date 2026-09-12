# Voice Assistant GNOME Extension
# Copyright (C) 2026 Giorgio Dramis
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""
Componente per la gestione unificata delle dipendenze mancanti (Python e di sistema).

Gestisce:
- Dipendenze di sistema (OS): tramite PackageKit D-Bus (agnostico dalla distribuzione,
  supporta Fedora, Debian, Ubuntu, Arch, openSUSE) con fallback CLI (pkexec).
- Dipendenze Python: installate nel virtualenv del daemon tramite pip.

Mostra un unico Adw.AlertDialog consolidato con il consenso esplicito dell'utente.
"""

import logging
import os
import shutil
import subprocess
import threading

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk, GLib, Gio

logger = logging.getLogger("VoiceAssistant.DependencyInstaller")

_VENV_PIP = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "daemon", "venv", "bin", "pip")
)

try:
    from core.data_loader import load_json_data
except ImportError:
    import sys
    _daemon_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "daemon"))
    if _daemon_path not in sys.path:
        sys.path.insert(0, _daemon_path)
    from core.data_loader import load_json_data


def detect_system_package_manager() -> tuple[str, list[str]]:
    """
    Rileva il gestore pacchetti disponibile sul sistema operativo leggendo
    le definizioni da dependencies/package_managers.json.
    Ritorna (nome_gestore, comando_cli_fallback).
    """
    pms = load_json_data("dependencies/package_managers.json", fallback_default=[])
    for item in pms:
        binary = item.get("binary")
        if binary and shutil.which(binary):
            return item.get("name", binary), list(item.get("install_cmd", []))
    return "unknown", []


def _resolve_system_package(dep: dict, pm_name: str) -> str:
    """Risolve il nome del pacchetto corretto per la distribuzione corrente."""
    pkgs = dep.get("system_packages") or {}
    if pm_name in pkgs:
        return pkgs[pm_name]
    if pm_name == "dnf5" and "dnf" in pkgs:
        return pkgs["dnf"]
    if pm_name in ("apt-get", "apt") and "apt" in pkgs:
        return pkgs["apt"]
    return dep.get("package", "")


def show_missing_deps_dialog(
    parent: Gtk.Widget,
    deps: list,
    on_done=None,
    auto_start: bool = False,
) -> None:
    """
    Mostra un unico Adw.AlertDialog consolidato per tutte le dipendenze mancanti,
    oppure avvia direttamente l'installazione se auto_start=True.

    Args:
        parent:     widget padre (tipicamente AssistantWindow)
        deps:       lista di dict {package: str, description: str, is_critical: bool, type: str, ...}
        on_done:    callable(success: bool) opzionale, chiamata al termine
        auto_start: se True, avvia direttamente il processo di installazione
    """
    if not deps:
        if on_done:
            on_done(True)
        return

    system_deps = [d for d in deps if d.get("type") == "system"]
    pip_deps = [d for d in deps if d.get("type", "pip") == "pip"]
    mcp_deps = [d for d in deps if d.get("type") == "mcp"]

    # Se un pacchetto di sistema è prerequisito di un MCP, accodiamo l'MCP corrispondente
    for d in system_deps:
        s_name = d.get("mcp_server")
        if s_name and not any(m.get("package") == s_name for m in mcp_deps):
            mcp_deps.append({"package": s_name, "type": "mcp", "description": f"Server MCP {s_name}"})

    if auto_start and (system_deps or pip_deps or mcp_deps):
        logger.info("[DependencyInstaller] auto_start=True: avvio immediato installazione")
        _run_install(system_deps, pip_deps, parent, on_done, mcp_deps=mcp_deps)
        return

    pm_name, _ = detect_system_package_manager()

    body_lines = ["Sono state rilevate dipendenze necessarie non ancora installate:\n"]

    if system_deps:
        pm_label = f" ({pm_name.upper()})" if pm_name != "unknown" else ""
        body_lines.append(f"📦 Pacchetti di sistema{pm_label}:")
        for d in system_deps:
            pkg_name = _resolve_system_package(d, pm_name)
            body_lines.append(f"  • {d['description']} ({pkg_name})")
        body_lines.append("")

    if pip_deps:
        body_lines.append("🐍 Pacchetti Python (nel virtualenv):")
        for d in pip_deps:
            body_lines.append(f"  • {d['description']} ({d['package']})")
        body_lines.append("")

    if mcp_deps:
        body_lines.append("🧩 Server MCP:")
        for d in mcp_deps:
            body_lines.append(f"  • {d['description']} ({d['package']})")
        body_lines.append("")

    if system_deps:
        body_lines.append("Vuoi procedere con l'installazione?\n(Per i pacchetti di sistema verrà richiesta l'autorizzazione dell'amministratore)")
    else:
        body_lines.append("Vuoi procedere con l'installazione?")

    total_count = len(system_deps) + len(pip_deps) + len(mcp_deps)
    plural = total_count > 1

    dlg = Adw.AlertDialog(
        heading="Dipendenze mancanti" if plural else "Dipendenza mancante",
        body="\n".join(body_lines),
    )
    dlg.add_response("skip", "Salta")
    dlg.add_response("install", "Installa tutto" if plural else "Installa")
    dlg.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
    dlg.set_default_response("install")
    dlg.set_close_response("skip")
    dlg.connect("response", _on_consent_response, system_deps, pip_deps, parent, on_done, mcp_deps)
    dlg.present(parent)


def _on_consent_response(dlg, response, system_deps, pip_deps, parent, on_done, mcp_deps=None):
    if response != "install":
        if on_done:
            on_done(False)
        return
    _run_install(system_deps, pip_deps, parent, on_done, mcp_deps=mcp_deps)


def _install_system_packages(system_deps: list) -> tuple[bool, str]:
    """
    Installa i pacchetti di sistema tramite il gestore pacchetti nativo
    (dnf5, dnf, apt, pacman, zypper) con privilegi elevati tramite pkexec.
    """
    pm_name, pm_cmd = detect_system_package_manager()
    packages = [_resolve_system_package(d, pm_name) for d in system_deps if _resolve_system_package(d, pm_name)]
    if not packages:
        return True, "Nessun pacchetto di sistema da installare"

    if not pm_cmd or not shutil.which("pkexec"):
        return False, (
            "Impossibile installare i pacchetti di sistema automaticamente (pkexec non disponibile).\n"
            f"Esegui manualmente da terminale: sudo {pm_name} install {' '.join(packages)}"
        )

    cmd = pm_cmd + packages
    logger.info(f"[Installazione Pacchetti Sistema] Esecuzione: {' '.join(cmd)}")
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0:
            logger.info(f"[Installazione Pacchetti Sistema] Installazione completata con successo: {packages}")
            return True, "Installazione pacchetti di sistema completata"
        elif res.returncode in (126, 127):
            return False, "Autorizzazione amministratore annullata dall'utente"
        error_output = (res.stderr or res.stdout or "").strip()[-400:]
        return False, f"Installazione {pm_name} fallita (codice {res.returncode}):\n{error_output}"
    except Exception as e:
        return False, f"Errore esecuzione {pm_name}: {e}"


def _install_pip_packages(pip_deps: list) -> tuple[bool, str]:
    """Installa le dipendenze Python nel virtualenv tramite pip."""
    packages = [d["package"] for d in pip_deps if d.get("package")]
    if not packages:
        return True, "Nessun pacchetto Python da installare"

    pip = _VENV_PIP if os.path.isfile(_VENV_PIP) else "pip3"
    cmd = [pip, "install", "--prefer-binary"] + packages
    logger.info(f"[pip] Esecuzione: {' '.join(cmd)}")
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0:
            return True, "Installazione pip completata"
        error_output = (res.stderr or res.stdout or "").strip()[-400:]
        return False, f"Installazione pip fallita:\n{error_output}"
    except Exception as e:
        return False, f"Errore esecuzione pip: {e}"


def _install_mcp_packages(mcp_deps: list) -> tuple[bool, str]:
    """Compila e installa i server MCP (es. gnome-mcp-server tramite cargo)."""
    for dep in mcp_deps:
        pkg = dep.get("package", "")
        if pkg == "gnome-mcp-server":
            cargo_path = shutil.which("cargo")
            if not cargo_path:
                for candidate in ("/usr/bin/cargo", os.path.expanduser("~/.cargo/bin/cargo")):
                    if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                        cargo_path = candidate
                        break
            if not cargo_path:
                return False, "Compilatore cargo non trovato sul sistema dopo l'installazione dei pacchetti."

            env = os.environ.copy()
            cargo_bin = os.path.expanduser("~/.cargo/bin")
            if cargo_bin not in env.get("PATH", "").split(os.pathsep):
                env["PATH"] = f"{cargo_bin}:{env.get('PATH', '')}"

            cmd = [cargo_path, "install", "--git", "https://github.com/bilelmoussaoui/gnome-mcp-server"]
            logger.info(f"[MCP Installation] Esecuzione: {' '.join(cmd)}")
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, env=env)
                if res.returncode != 0:
                    err = (res.stderr or res.stdout or "").strip()[-400:]
                    return False, f"Compilazione gnome-mcp-server fallita:\n{err}"
                logger.info("[MCP Installation] gnome-mcp-server compilato e installato con successo.")
            except Exception as e:
                return False, f"Errore durante l'installazione di gnome-mcp-server: {e}"

    return True, "Installazione server MCP completata"


def _start_mcp_servers(mcp_deps: list) -> tuple[bool, str]:
    """Avvia i server MCP appena installati nel demone tramite D-Bus."""
    for dep in mcp_deps:
        pkg = dep.get("package", "")
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            res = bus.call_sync(
                "org.local.VoiceAssistant",
                "/org/local/VoiceAssistant",
                "org.local.VoiceAssistant",
                "StartMCPServer",
                GLib.Variant("(s)", (pkg,)),
                GLib.VariantType("(bs)"),
                Gio.DBusCallFlags.NONE,
                15000,
                None,
            )
            success, msg = res.unpack()
            logger.info(f"[MCP Start] StartMCPServer({pkg}) -> success={success}, msg={msg}")
        except Exception as e:
            logger.info(f"[MCP Start] Impossibile avviare {pkg} via D-Bus: {e}")
    return True, "Server MCP avviati"


def _run_install(
    system_deps: list,
    pip_deps: list,
    parent: Gtk.Widget,
    on_done=None,
    mcp_deps: list = None,
) -> None:
    mcp_deps = list(mcp_deps or [])
    # Assicura che se un pacchetto di sistema installato era per un MCP, quell'MCP sia in lista
    for d in system_deps:
        s_name = d.get("mcp_server")
        if s_name and not any(m.get("package") == s_name for m in mcp_deps):
            mcp_deps.append({"package": s_name, "type": "mcp", "description": f"Server MCP {s_name}"})

    progress_dlg = Adw.AlertDialog(
        heading="Installazione in corso…",
        body="Preparazione dell'installazione…",
    )
    spinner = Gtk.Spinner(spinning=True)
    spinner.set_margin_top(8)
    spinner.set_size_request(32, 32)
    progress_dlg.set_extra_child(spinner)
    progress_dlg.present(parent)

    def _do_work():
        # Step 1: Pacchetti di sistema (es. cargo)
        if system_deps:
            GLib.idle_add(progress_dlg.set_body, "Installazione pacchetti di sistema…")
            success, msg = _install_system_packages(system_deps)
            if not success:
                GLib.idle_add(_on_install_done, progress_dlg, False, msg, parent, on_done)
                return

        # Step 2: Pacchetti Python
        if pip_deps:
            GLib.idle_add(progress_dlg.set_body, "Installazione pacchetti Python nel virtualenv…")
            success, msg = _install_pip_packages(pip_deps)
            if not success:
                GLib.idle_add(_on_install_done, progress_dlg, False, msg, parent, on_done)
                return

        # Step 3: Compilazione e installazione server MCP
        if mcp_deps:
            GLib.idle_add(progress_dlg.set_body, "Compilazione e installazione server MCP (potrebbe richiedere qualche minuto)…")
            success, msg = _install_mcp_packages(mcp_deps)
            if not success:
                GLib.idle_add(_on_install_done, progress_dlg, False, msg, parent, on_done)
                return

            # Step 4: Avvio e attivazione server MCP nel demone
            GLib.idle_add(progress_dlg.set_body, "Avvio e attivazione server MCP…")
            _start_mcp_servers(mcp_deps)

        success_msg = (
            "Tutte le dipendenze e i componenti sono stati installati e attivati correttamente."
        )
        GLib.idle_add(_on_install_done, progress_dlg, True, success_msg, parent, on_done)

    threading.Thread(target=_do_work, daemon=True).start()


def _on_install_done(progress_dlg, success: bool, message: str, parent, on_done):
    progress_dlg.close()

    res_dlg = Adw.AlertDialog(
        heading="Installazione completata" if success else "Installazione non riuscita",
        body=message,
    )
    res_dlg.add_response("ok", "OK")
    res_dlg.present(parent)

    if on_done:
        on_done(success)

    return GLib.SOURCE_REMOVE
