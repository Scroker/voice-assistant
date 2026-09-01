import os
import subprocess
import shutil
import asyncio
from typing import Dict, Any
from .base import NativeTool

class AppLauncherTool(NativeTool):
    """Tool for launching desktop applications in GNOME."""

    @property
    def name(self) -> str:
        return "app_launcher"

    @property
    def description(self) -> str:
        return "Launch a desktop application or browser in GNOME (e.g. firefox, nautilus, gedit, terminal, calculator)."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "app_name": {
                    "type": "string",
                    "description": "Name or executable of the application to launch (e.g. 'firefox', 'nautilus', 'calculator', 'terminal').",
                },
            },
            "required": ["app_name"],
        }

    async def execute(self, args: Dict[str, Any]) -> str:
        app_name = args.get("app_name", "").strip().lower()
        if not app_name:
            return "Specificare il nome dell'applicazione da avviare."

        # Mappatura nomi amichevoli ➔ comandi/desktop files
        APP_MAP = {
            "browser": "firefox",
            "internet": "firefox",
            "file manager": "nautilus",
            "file": "nautilus",
            "cartella": "nautilus",
            "cartelle": "nautilus",
            "calcolatrice": "gnome-calculator",
            "calculator": "gnome-calculator",
            "terminale": "gnome-terminal",
            "terminal": "gnome-terminal",
            "testo": "gnome-text-editor",
            "editor": "gnome-text-editor",
            "impostazioni": "gnome-control-center",
            "settings": "gnome-control-center",
            "calendario": "gnome-calendar",
            "calendar": "gnome-calendar",
            "orologio": "gnome-clocks",
            "sveglia": "gnome-clocks",
            "musica": "rhythmbox",
            "monitor": "gnome-system-monitor",
            "risorse": "gnome-system-monitor",
            "estensioni": "gnome-extensions-app",
        }

        cmd_name = APP_MAP.get(app_name, app_name)

        def _exec_launch():
            candidates = [cmd_name]
            if cmd_name == "gnome-calendar":
                candidates.extend(["org.gnome.Calendar.desktop", "org.gnome.Calendar", "gnome-calendar.desktop"])
            elif cmd_name == "gnome-calculator":
                candidates.extend(["org.gnome.Calculator.desktop", "org.gnome.Calculator", "gnome-calculator.desktop"])
            elif cmd_name == "gnome-terminal":
                candidates.extend(["org.gnome.Terminal.desktop", "ptyxis.desktop", "org.gnome.Console.desktop"])
            elif cmd_name == "gnome-clocks":
                candidates.extend(["org.gnome.Clocks.desktop", "gnome-clocks.desktop"])
            elif cmd_name == "gnome-control-center":
                candidates.extend(["org.gnome.Settings.desktop", "gnome-control-center.desktop"])
            elif cmd_name == "nautilus":
                candidates.extend(["org.gnome.Nautilus.desktop", "nautilus.desktop"])
            else:
                candidates.extend([f"org.gnome.{cmd_name.capitalize()}.desktop", f"{cmd_name}.desktop"])

            desktop_dirs = [
                "/usr/share/applications",
                os.path.expanduser("~/.local/share/applications"),
                "/var/lib/snapd/desktop/applications",
                "/var/lib/flatpak/exports/share/applications"
            ]

            for target in candidates:
                if shutil.which(target):
                    try:
                        subprocess.Popen([target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        return f"Apro {app_name}."
                    except Exception:
                        pass

                if target.endswith(".desktop") and shutil.which("gtk-launch"):
                    for d in desktop_dirs:
                        if os.path.exists(os.path.join(d, target)):
                            try:
                                subprocess.Popen(["gtk-launch", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                                return f"Apro {app_name}."
                            except Exception:
                                pass

            return f"Impossibile trovare o avviare l'applicazione '{app_name}'."

        return await asyncio.to_thread(_exec_launch)
