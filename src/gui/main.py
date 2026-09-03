#!/usr/bin/env python3
import sys
import os

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gio, Adw

_GUI_DIR = os.path.dirname(os.path.abspath(__file__))
if _GUI_DIR not in sys.path:
    sys.path.insert(0, _GUI_DIR)

from assistant_window import AssistantWindow


def main() -> int:
    app = Adw.Application(
        application_id="org.local.VoiceAssistant.GUI",
        flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
    )

    def on_command_line(app_instance: Adw.Application, cl: Gio.ApplicationCommandLine) -> int:
        args = cl.get_arguments()
        cl.done()

        if '--open-settings' in args:
            from settings_window import open_settings_window
            parent = next(
                (w for w in app_instance.get_windows() if isinstance(w, AssistantWindow)),
                None,
            )
            open_settings_window(parent, application=app_instance)
        else:
            existing = [w for w in app_instance.get_windows() if isinstance(w, AssistantWindow)]
            if existing:
                existing[0].present()
            else:
                win = AssistantWindow(application=app_instance)
                win.present()

        return 0

    app.connect("command-line", on_command_line)
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
