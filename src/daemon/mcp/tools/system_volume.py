import subprocess
import asyncio
from typing import Dict, Any
from .base import NativeTool

class SystemVolumeTool(NativeTool):
    """Tool for controlling GNOME system volume."""

    @property
    def name(self) -> str:
        return "system_volume"

    @property
    def description(self) -> str:
        return "Get, set, mute, or unmute system audio volume in GNOME."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["get", "set", "increase", "decrease", "mute", "unmute"],
                    "description": "Action to perform: 'get', 'set', 'increase', 'decrease', 'mute', or 'unmute'.",
                },
                "level": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                    "description": "Target volume percentage (0-100) when action is 'set'.",
                },
            },
            "required": ["action"],
        }

    async def execute(self, args: Dict[str, Any]) -> str:
        action = args.get("action", "get")
        level = args.get("level")

        def _run_cmd(cmd):
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, check=True)
                return res.stdout.strip()
            except Exception:
                return None

        def _exec_action():
            if action == "set" and level is not None:
                clamped = max(0, min(100, int(level)))
                if _run_cmd(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{clamped}%"]) is not None:
                    return f"Volume del sistema impostato al {clamped}%."
                if _run_cmd(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{clamped}%"]) is not None:
                    return f"Volume del sistema impostato al {clamped}%."
                if _run_cmd(["amixer", "set", "Master", f"{clamped}%"]) is not None:
                    return f"Volume del sistema impostato al {clamped}%."
                return f"Impossibile impostare il volume al {clamped}%."

            elif action == "increase":
                step = int(level) if level is not None else 10
                if _run_cmd(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{step}%+"]) is not None or \
                   _run_cmd(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"+{step}%"]) is not None or \
                   _run_cmd(["amixer", "set", "Master", f"{step}%+"]) is not None:
                    return f"Volume del sistema aumentato del {step}%."
                return "Impossibile aumentare il volume."

            elif action == "decrease":
                step = int(level) if level is not None else 10
                if _run_cmd(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{step}%-"]) is not None or \
                   _run_cmd(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"-{step}%"]) is not None or \
                   _run_cmd(["amixer", "set", "Master", f"{step}%-"]) is not None:
                    return f"Volume del sistema ridotto del {step}%."
                return "Impossibile ridurre il volume."

            elif action == "mute":
                if _run_cmd(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1"]) is not None or \
                   _run_cmd(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1"]) is not None:
                    return "Audio del sistema disattivato (Mute)."
                return "Impossibile disattivare l'audio."

            elif action == "unmute":
                if _run_cmd(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"]) is not None or \
                   _run_cmd(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"]) is not None:
                    return "Audio del sistema riattivato."
                return "Impossibile riattivare l'audio."

            else:  # get
                out = _run_cmd(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
                if out:
                    return f"Stato volume: {out}"
                out = _run_cmd(["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
                if out:
                    return f"Stato volume: {out}"
                return "Impossibile leggere il volume corrente."

        return await asyncio.to_thread(_exec_action)
