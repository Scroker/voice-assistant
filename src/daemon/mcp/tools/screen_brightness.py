import subprocess
import asyncio
from typing import Dict, Any
from .base import NativeTool

class ScreenBrightnessTool(NativeTool):
    """Tool for controlling laptop/monitor screen brightness in GNOME."""

    @property
    def name(self) -> str:
        return "screen_brightness"

    @property
    def description(self) -> str:
        return "Get, set, increase, or decrease screen brightness percentage (0-100)."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["get", "set", "increase", "decrease"],
                    "description": "Brightness action: 'get', 'set', 'increase', or 'decrease'.",
                },
                "level": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                    "description": "Brightness percentage (0-100) when action is 'set'.",
                }
            },
            "required": ["action"],
        }

    async def execute(self, args: Dict[str, Any]) -> str:
        action = args.get("action", "get")
        level = args.get("level", 50)

        def _exec():
            # Try brightnessctl first
            try:
                if action == "get":
                    res = subprocess.check_output(["brightnessctl", "g"], text=True).strip()
                    max_b = subprocess.check_output(["brightnessctl", "m"], text=True).strip()
                    pct = int(round((int(res) / int(max_b)) * 100))
                    return f"Luminosità dello schermo attuale: {pct}%."
                elif action == "set":
                    subprocess.run(["brightnessctl", "set", f"{level}%"], check=True)
                    return f"Luminosità impostata al {level}%."
                elif action == "increase":
                    subprocess.run(["brightnessctl", "set", "+10%"], check=True)
                    return "Luminosità aumentata del 10%."
                elif action == "decrease":
                    subprocess.run(["brightnessctl", "set", "10%-"], check=True)
                    return "Luminosità ridotta del 10%."
            except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
                pass

            # Fallback to gdbus org.gnome.SettingsDaemon.Power.Screen
            try:
                if action == "get":
                    res = subprocess.check_output([
                        "gdbus", "call", "--session",
                        "--dest", "org.gnome.SettingsDaemon.Power",
                        "--object-path", "/org/gnome/SettingsDaemon/Power",
                        "--method", "org.freedesktop.DBus.Properties.Get",
                        "org.gnome.SettingsDaemon.Power.Screen", "Brightness"
                    ], text=True).strip()
                    return f"Luminosità dello schermo: {res}."
                elif action == "set":
                    subprocess.run([
                        "gdbus", "call", "--session",
                        "--dest", "org.gnome.SettingsDaemon.Power",
                        "--object-path", "/org/gnome/SettingsDaemon/Power",
                        "--method", "org.freedesktop.DBus.Properties.Set",
                        "org.gnome.SettingsDaemon.Power.Screen", "Brightness", f"<int32 {level}>"
                    ], check=True)
                    return f"Luminosità impostata al {level}%."
            except Exception as e:
                return f"Impossibile regolare la luminosità dello schermo ({e})."

            return f"Azione luminosità '{action}' completata."

        return await asyncio.to_thread(_exec)
