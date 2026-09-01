import subprocess
import asyncio
from typing import Dict, Any
from .base import NativeTool

class SystemMediaTool(NativeTool):
    """Tool for controlling media playback (play, pause, next, previous) in GNOME via MPRIS or playerctl."""

    @property
    def name(self) -> str:
        return "system_media"

    @property
    def description(self) -> str:
        return "Control system media playback: play, pause, play-pause, next, previous, or stop."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["play", "pause", "play-pause", "next", "previous", "stop"],
                    "description": "Media action to perform.",
                }
            },
            "required": ["action"],
        }

    async def execute(self, args: Dict[str, Any]) -> str:
        action = args.get("action", "play-pause")

        def _exec():
            # Try playerctl first
            try:
                subprocess.run(["playerctl", action], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return f"Comando multimediale '{action}' eseguito."
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass

            # Fallback to gdbus / org.mpris.MediaPlayer2
            mpris_actions = {
                "play": "Play",
                "pause": "Pause",
                "play-pause": "PlayPause",
                "next": "Next",
                "previous": "Previous",
                "stop": "Stop"
            }
            method = mpris_actions.get(action, "PlayPause")
            try:
                subprocess.run([
                    "gdbus", "call", "--session",
                    "--dest", "org.mpris.MediaPlayer2.Player",
                    "--object-path", "/org/mpris/MediaPlayer2",
                    "--method", f"org.mpris.MediaPlayer2.Player.{method}"
                ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return f"Riproduzione multimediale aggiornata: {action}."
            except Exception as e:
                return f"Nessun lettore multimediale attivo o `playerctl` non installato ({e})."

        return await asyncio.to_thread(_exec)
