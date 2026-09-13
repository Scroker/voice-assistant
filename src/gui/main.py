#!/usr/bin/env python3
import sys
import os
import threading

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gio, Adw, Gtk, Gdk, GLib

_GUI_DIR = os.path.dirname(os.path.abspath(__file__))
if _GUI_DIR not in sys.path:
    sys.path.insert(0, _GUI_DIR)

from components.resources import register_resources, register_icons
from assistant_window import AssistantWindow


def main() -> int:
    GLib.set_prgname("org.local.VoiceAssistant.GUI")
    GLib.set_application_name("Assistente Vocale")

    register_resources()
    register_icons(Gdk.Display.get_default())
    Gtk.Window.set_default_icon_name("vocal-assistant-icon")

    app = Adw.Application(
        application_id="org.local.VoiceAssistant.GUI",
        flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
    )

    quit_action = Gio.SimpleAction.new("quit", None)
    quit_action.connect("activate", lambda *_: app.quit())
    app.add_action(quit_action)
    app.set_accels_for_action("app.quit", ["<Ctrl>Q", "<Ctrl>W"])

    def on_command_line(app_instance: Adw.Application, cl: Gio.ApplicationCommandLine) -> int:
        args = cl.get_arguments()
        cl.done()

        existing = [w for w in app_instance.get_windows() if isinstance(w, AssistantWindow)]
        win = getattr(app_instance, "_assistant_win", None)
        if win is None and existing:
            win = existing[0]

        is_already_open = (win is not None)

        if '--open-settings' in args:
            from settings_window import open_settings_window
            open_settings_window(parent=None, application=app_instance)
            return 0

        if win is not None:
            win.set_visible(True)
            win.present()
        else:
            win = AssistantWindow(application=app_instance)
            app_instance._assistant_win = win
            win.present()

        if '--open-conversation' in args:
            try:
                idx = args.index('--open-conversation')
                if idx + 1 < len(args):
                    conv_id = args[idx + 1]
                    if hasattr(win, 'open_conversation'):
                        win.open_conversation(conv_id)
            except Exception as e:
                pass

        if '--install-deps' in args or '--auto-install' in args:
            auto_start = '--auto-install' in args
            win.trigger_dependency_installer(auto_start=auto_start)
        elif is_already_open:
            threading.Thread(target=win._poll_missing_deps, daemon=True).start()

        return 0

    app.connect("command-line", on_command_line)
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
