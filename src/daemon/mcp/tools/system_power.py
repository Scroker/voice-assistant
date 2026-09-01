import subprocess
import asyncio
from typing import Dict, Any
from .base import NativeTool

class SystemPowerTool(NativeTool):
    """Tool for controlling GNOME system power actions (lock screen, suspend, restart, logout)."""

    @property
    def name(self) -> str:
        return "system_power"

    @property
    def description(self) -> str:
        return "Perform system session/power management: lock screen, suspend, restart, logout, or shutdown."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["lock", "suspend", "logout", "restart", "shutdown"],
                    "description": "Power action to execute.",
                }
            },
            "required": ["action"],
        }

    async def execute(self, args: Dict[str, Any]) -> str:
        action = args.get("action", "lock")

        def _exec():
            try:
                if action == "lock":
                    subprocess.run(["loginctl", "lock-session"], check=True)
                    return "Schermo bloccato."
                elif action == "suspend":
                    subprocess.run(["systemctl", "suspend"], check=True)
                    return "Sistema in sospensione."
                elif action == "logout":
                    subprocess.run([
                        "gdbus", "call", "--session",
                        "--dest", "org.gnome.SessionManager",
                        "--object-path", "/org/gnome/SessionManager",
                        "--method", "org.gnome.SessionManager.Logout", "1"
                    ], check=True)
                    return "Disconnessione della sessione avviata."
                elif action == "restart":
                    subprocess.run(["systemctl", "reboot"], check=True)
                    return "Riavvio del sistema avviato."
                elif action == "shutdown":
                    subprocess.run(["systemctl", "poweroff"], check=True)
                    return "Spegnimento del sistema avviato."
            except Exception as e:
                return f"Impossibile eseguire l'azione di alimentazione '{action}': {e}"

            return f"Azione '{action}' eseguita con successo."

        return await asyncio.to_thread(_exec)
