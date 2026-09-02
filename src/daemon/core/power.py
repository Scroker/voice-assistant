"""Power management helper for the Voice Assistant daemon."""

import logging

from gi.repository import Gio, GLib

logger_power = logging.getLogger("VoiceAssistant.Power")


class PowerInhibitor:
    """Acquire GNOME and logind sleep inhibitors during model download or processing."""

    def __init__(self):
        self._gnome_cookie = None
        self._logind_fd = None

    def inhibit(self, reason="Scaricamento modello in corso"):
        # 1. Systemd Logind Inhibitor (System Bus FD)
        if self._logind_fd is None:
            try:
                sys_bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
                res, fd_list = sys_bus.call_with_unix_fd_list_sync(
                    'org.freedesktop.login1',
                    '/org/freedesktop/login1',
                    'org.freedesktop.login1.Manager',
                    'Inhibit',
                    GLib.Variant('(ssss)', ('sleep:idle:handle-suspend-key:handle-hibernate-key:handle-lid-switch', 'Voice Assistant', reason, 'block')),
                    GLib.VariantType.new('(h)'),
                    Gio.DBusCallFlags.NONE,
                    -1,
                    None,
                    None
                )
                if res and fd_list:
                    fd_idx = res.unpack()[0]
                    self._logind_fd = fd_list.get(fd_idx)
                    logger_power.info(f"Systemd logind lock attivato (FD: {self._logind_fd}).")
            except Exception as e:
                logger_power.warning(f"Impossibile attivare logind lock: {e}")

        # 2. GNOME SessionManager Inhibitor (Session Bus Cookie)
        if self._gnome_cookie is None:
            try:
                bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                res = bus.call_sync(
                    "org.gnome.SessionManager",
                    "/org/gnome/SessionManager",
                    "org.gnome.SessionManager",
                    "Inhibit",
                    GLib.Variant("(susu)", ("org.local.VoiceAssistant", 0, reason, 12)),
                    GLib.VariantType.new("(u)"),
                    Gio.DBusCallFlags.NONE,
                    -1,
                    None
                )
                if res:
                    self._gnome_cookie = res.unpack()[0]
                    logger_power.info(f"GNOME SessionManager lock attivato (cookie: {self._gnome_cookie}).")
            except Exception as e:
                logger_power.warning(f"Impossibile attivare GNOME lock: {e}")

    def uninhibit(self):
        if self._logind_fd is not None:
            try:
                import os
                os.close(self._logind_fd)
                logger_power.info("Systemd logind lock rilasciato.")
            except Exception as e:
                logger_power.warning(f"Errore rilascio logind lock: {e}")
            self._logind_fd = None

        if self._gnome_cookie is not None:
            try:
                bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                bus.call_sync(
                    "org.gnome.SessionManager",
                    "/org/gnome/SessionManager",
                    "org.gnome.SessionManager",
                    "Uninhibit",
                    GLib.Variant("(u)", (self._gnome_cookie,)),
                    None,
                    Gio.DBusCallFlags.NONE,
                    -1,
                    None
                )
                logger_power.info("GNOME SessionManager lock rilasciato.")
            except Exception as e:
                logger_power.warning(f"Errore rilascio GNOME lock: {e}")
            self._gnome_cookie = None
