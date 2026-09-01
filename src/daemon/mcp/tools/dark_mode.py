import subprocess
import asyncio
from typing import Dict, Any
from .base import NativeTool

class DarkModeTool(NativeTool):
    """Tool for toggling GNOME dark mode / light mode theme."""

    @property
    def name(self) -> str:
        return "dark_mode"

    @property
    def description(self) -> str:
        return "Toggle, enable, or disable GNOME desktop dark mode theme."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["toggle", "dark", "light", "get"],
                    "description": "Theme mode to apply: 'dark' (prefer-dark), 'light' (default), 'toggle', or 'get'.",
                },
            },
            "required": ["mode"],
        }

    async def execute(self, args: Dict[str, Any]) -> str:
        mode = args.get("mode", "toggle")

        def _run_gsettings(cmd):
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, check=True)
                return res.stdout.strip()
            except Exception:
                return None

        def _exec_action():
            schema = "org.gnome.desktop.interface"
            key = "color-scheme"

            if mode == "get":
                current = _run_gsettings(["gsettings", "get", schema, key])
                if current and "prefer-dark" in current:
                    return "Il tema corrente di GNOME è Scuro (Dark)."
                return "Il tema corrente di GNOME è Chiaro (Light)."

            target_scheme = "prefer-dark"
            if mode == "light":
                target_scheme = "default"
            elif mode == "toggle":
                current = _run_gsettings(["gsettings", "get", schema, key])
                if current and "prefer-dark" in current:
                    target_scheme = "default"
                else:
                    target_scheme = "prefer-dark"

            res = _run_gsettings(["gsettings", "set", schema, key, target_scheme])
            if res is not None:
                mode_str = "Scuro (Dark)" if target_scheme == "prefer-dark" else "Chiaro (Light)"
                return f"Tema di GNOME impostato su {mode_str}."
            return "Impossibile modificare il tema di GNOME."

        return await asyncio.to_thread(_exec_action)
