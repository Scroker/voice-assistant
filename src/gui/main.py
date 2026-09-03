#!/usr/bin/env python3
# Voice Assistant GUI — Standalone GTK4/Libadwaita Application
# Connects to the daemon via D-Bus (org.local.VoiceAssistant) and exposes
# a chat window with streaming token display and voice input controls.
import sys
import os

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gio, Adw

# Ensure the gui/ directory is on the path so assistant_window can be imported
_GUI_DIR = os.path.dirname(os.path.abspath(__file__))
if _GUI_DIR not in sys.path:
    sys.path.insert(0, _GUI_DIR)

from assistant_window import AssistantWindow


def main() -> int:
    app = Adw.Application(
        application_id="org.local.VoiceAssistant.GUI",
        flags=Gio.ApplicationFlags.FLAGS_NONE,
    )

    def on_activate(app_instance: Adw.Application) -> None:
        # If a window already exists (single-instance activation), just re-present it.
        existing = app_instance.get_windows()
        if existing:
            existing[0].present()
            return
        win = AssistantWindow(application=app_instance)
        win.present()

    app.connect("activate", on_activate)
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
