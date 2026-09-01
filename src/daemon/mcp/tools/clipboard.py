import subprocess
import asyncio
from typing import Dict, Any
from .base import NativeTool

class ClipboardTool(NativeTool):
    """Tool for reading or writing text to the desktop clipboard."""

    @property
    def name(self) -> str:
        return "clipboard"

    @property
    def description(self) -> str:
        return "Get text from the clipboard or copy text to the clipboard."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["get", "copy"],
                    "description": "Action to perform: 'get' to read clipboard, 'copy' to write text to clipboard.",
                },
                "text": {
                    "type": "string",
                    "description": "Text to copy when action is 'copy'.",
                }
            },
            "required": ["action"],
        }

    async def execute(self, args: Dict[str, Any]) -> str:
        action = args.get("action", "get")
        text = args.get("text", "")

        def _exec():
            # Wayland wl-paste / wl-copy
            if action == "get":
                try:
                    content = subprocess.check_output(["wl-paste", "--no-newline"], text=True)
                    return f"Contenuto negli appunti: \"{content}\""
                except Exception:
                    pass

                try:
                    content = subprocess.check_output(["xclip", "-selection", "clipboard", "-o"], text=True)
                    return f"Contenuto negli appunti: \"{content}\""
                except Exception:
                    pass

                return "Impossibile leggere gli appunti (nessun utility 'wl-paste' o 'xclip' trovata)."

            elif action == "copy":
                if not text:
                    return "Specificare il testo da copiare negli appunti."

                try:
                    p = subprocess.Popen(["wl-copy"], stdin=subprocess.PIPE, text=True)
                    p.communicate(input=text)
                    return f"Testo copiato negli appunti."
                except Exception:
                    pass

                try:
                    p = subprocess.Popen(["xclip", "-selection", "clipboard"], stdin=subprocess.PIPE, text=True)
                    p.communicate(input=text)
                    return f"Testo copiato negli appunti."
                except Exception:
                    pass

                return "Impossibile copiare negli appunti (utilità 'wl-copy' / 'xclip' non disponibile)."

            return f"Azione appunti '{action}' completata."

        return await asyncio.to_thread(_exec)
