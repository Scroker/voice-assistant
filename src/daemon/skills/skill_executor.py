"""Skill Executor Engine for SKILL.md declarative actions and tool mapping.

This module provides the core engine for executing markdown-defined skills with:
- Declarative action parsing from skill body instructions
- Automatic tool name recognition and validation
- Standardized response generation
- Optional LLM fallback for complex or ambiguous skills
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("VoiceAssistant.SkillExecutor")


def load_tool_keywords() -> Dict[str, str]:
    """Carica la mappatura delle parole chiave dei tool da data/mcp/known_tools.json."""
    from core.data_loader import load_json_data
    data = load_json_data("mcp/known_tools.json", fallback_default={}) or {}
    return data.get("keywords") or {
        "volume": "set_volume",
        "set_volume": "set_volume",
        "theme": "quick_settings",
        "quick_settings": "quick_settings",
        "app": "launch_application",
        "application": "launch_application",
        "launch_application": "launch_application",
        "media": "media_control",
        "music": "media_control",
        "media_control": "media_control",
        "notification": "send_notification",
        "send_notification": "send_notification",
        "open_file": "open_file",
        "file": "open_file",
        "wallpaper": "set_wallpaper",
        "set_wallpaper": "set_wallpaper",
        "screenshot": "take_screenshot",
        "take_screenshot": "take_screenshot",
        "window": "window_management",
        "window_management": "window_management",
        "keyring": "keyring_management",
        "keyring_management": "keyring_management",
    }


def load_standard_responses(lang: str = "") -> Dict[str, Dict[str, str]]:
    """Carica le risposte standard dei tool da data/locales/responses.json."""
    if not lang:
        try:
            from core.locale_utils import get_system_language
            lang = get_system_language(default="it")
        except Exception:
            lang = "it"
    from core.data_loader import load_json_data
    data = load_json_data("locales/responses.json", fallback_default={}) or {}
    loc_data = data.get(lang) or data.get("it") or {}
    return {k: v for k, v in loc_data.items() if k != "fast_path"}


class SkillExecutor:
    """Executes a matched SKILL.md skill with tool mapping and response generation."""

    # Common tool patterns recognized in skill bodies (loaded dynamically)
    TOOL_KEYWORDS = load_tool_keywords()

    # Standardized response templates by tool and action (loaded dynamically)
    STANDARD_RESPONSES = load_standard_responses()

    def __init__(self, skill: Dict[str, Any]):
        """Initialize executor with a skill definition."""
        self.skill = skill
        self.intent = skill.get("intent", "")
        self.name = skill.get("name", "")
        self.body = skill.get("_body", "")
        self.tools_allowed = skill.get("tools_allowed", [])
        self.triggers = skill.get("triggers", [])
        self.action_type = skill.get("action_type") or "system"
        self.command = skill.get("command", "")
        self.prompt = skill.get("prompt", "")
        self.response = skill.get("response", "")
        self.tool = skill.get("tool", "")
        self.args = skill.get("args") or {}

    def _extract_tool_keywords_from_body(self) -> List[str]:
        """Extract tool names from skill body based on keyword matching."""
        detected_tools = []
        body_lower = self.body.lower()

        for keyword, tool_name in self.TOOL_KEYWORDS.items():
            if keyword in body_lower and tool_name not in detected_tools:
                detected_tools.append(tool_name)

        # Also check for explicit tool names mentioned in backticks or code blocks
        explicit_tools = re.findall(r"`([a-z_]+)`", self.body.lower())
        for tool in explicit_tools:
            if tool not in detected_tools:
                detected_tools.append(tool)

        return detected_tools

    def _infer_action_from_text(
        self, user_text: str, tool_name: str
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Infer the action and parameters from user text for a given tool."""
        text_lower = user_text.lower()

        if tool_name in ("set_volume", "system_volume"):
            if any(w in text_lower for w in ["alza", "aumenta", "più forte"]):
                return ("increase", {"direction": "up", "relative": True, "volume": 10.0, "action": "increase", "level": 10})
            if any(w in text_lower for w in ["abbassa", "riduc", "più basso"]):
                return ("decrease", {"direction": "down", "relative": True, "volume": -10.0, "action": "decrease", "level": 10})
            if any(w in text_lower for w in ["silen", "mute", "zitto"]):
                return ("mute", {"mute": True, "action": "mute"})
            if any(w in text_lower for w in ["riattiva", "unmute"]):
                return ("unmute", {"mute": False, "action": "unmute"})
            if "volume" in text_lower and re.search(r"\d+", text_lower):
                match = re.search(r"(\d+)", text_lower)
                if match:
                    level = int(match.group(1))
                    clamped = min(100, max(0, level))
                    return ("set", {"volume": float(clamped), "action": "set", "level": clamped})
            return ("increase", {"direction": "up", "relative": True, "volume": 10.0, "action": "increase", "level": 10})

        elif tool_name in ("quick_settings", "dark_mode"):
            if any(w in text_lower for w in ["scuro", "dark", "night", "nero"]):
                return ("dark", {"setting": "dark_style", "enabled": True, "action": "set", "mode": "dark"})
            if any(w in text_lower for w in ["chiaro", "light", "day", "bianco", "giorno"]):
                return ("light", {"setting": "dark_style", "enabled": False, "action": "set", "mode": "light"})
            if "wifi" in text_lower or "wi-fi" in text_lower:
                if any(w in text_lower for w in ["disattiva", "spegni", "off"]):
                    return ("wifi_off", {"setting": "wifi", "enabled": False})
                return ("wifi_on", {"setting": "wifi", "enabled": True})
            if "bluetooth" in text_lower:
                if any(w in text_lower for w in ["disattiva", "spegni", "off"]):
                    return ("bluetooth_off", {"setting": "bluetooth", "enabled": False})
                return ("bluetooth_on", {"setting": "bluetooth", "enabled": True})
            if "notturna" in text_lower or "night light" in text_lower:
                if any(w in text_lower for w in ["disattiva", "spegni", "off"]):
                    return ("night_light_off", {"setting": "night_light", "enabled": False})
                return ("night_light_on", {"setting": "night_light", "enabled": True})
            if "disturbare" in text_lower or "dnd" in text_lower:
                if any(w in text_lower for w in ["disattiva", "spegni", "off"]):
                    return ("dnd_off", {"setting": "do_not_disturb", "enabled": False})
                return ("dnd_on", {"setting": "do_not_disturb", "enabled": True})
            return None

        elif tool_name in ("launch_application", "app_launcher"):
            apps = ["firefox", "browser", "terminale", "calendar", "impostazioni", "files", "calculator"]
            for app in apps:
                if app in text_lower:
                    return ("launch", {"app_name": app, "action": "launch"})
            # Extract application after trigger words
            m = re.search(r'(?:apri|avvia|lancia|open)\s+(?:il\s+|la\s+|le\s+|l\'|i\s+)?([\w\s]+)', text_lower)
            if m:
                extracted = m.group(1).strip()
                if extracted:
                    return ("launch", {"app_name": extracted, "action": "launch"})
            return None

        elif tool_name == "date_time":
            if any(w in text_lower for w in ["ora", "time"]):
                return ("time", {"action": "time"})
            if any(w in text_lower for w in ["data", "giorno", "date"]):
                return ("date", {"action": "date"})
            return None

        elif tool_name == "screen_brightness":
            if any(w in text_lower for w in ["aumenta", "alza", "più luminoso"]):
                return ("increase", {"action": "increase", "level": 10})
            if any(
                w in text_lower for w in ["riduc", "abbassa", "meno luminoso", "scuro"]
            ):
                return ("decrease", {"action": "decrease", "level": 10})
            if "luminosità" in text_lower and re.search(r"\d+", text_lower):
                match = re.search(r"(\d+)", text_lower)
                if match:
                    level = int(match.group(1))
                    return ("set", {"action": "set", "level": min(100, max(0, level))})
            return None

        elif tool_name in ("media_control", "system_media"):
            if any(w in text_lower for w in ["play", "riproduci", "avvia"]):
                return ("play", {"action": "play"})
            if any(w in text_lower for w in ["pausa"]):
                return ("pause", {"action": "pause"})
            if any(w in text_lower for w in ["stop", "interrompi"]):
                return ("stop", {"action": "stop"})
            if any(w in text_lower for w in ["prossimo", "next", "successivo", "avanti"]):
                return ("next", {"action": "next"})
            if any(w in text_lower for w in ["precedente", "prev", "indietro"]):
                return ("previous", {"action": "previous"})
            return None

        elif tool_name == "send_notification":
            return ("send", {"summary": "Assistente Vocale", "body": user_text})

        elif tool_name == "take_screenshot":
            return ("take", {"interactive": "interattiv" in text_lower or "selezion" in text_lower})

        elif tool_name == "open_file":
            m = re.search(r'(?:apri|open)\s+(?:il\s+file\s+|il\s+|la\s+)?([^\s]+)', text_lower)
            path = m.group(1).strip() if m else ""
            return ("open", {"path": path})

        elif tool_name == "set_wallpaper":
            m = re.search(r'(?:sfondo|wallpaper)\s+(?:a\s+|con\s+)?([^\s]+)', text_lower)
            path = m.group(1).strip() if m else ""
            return ("set", {"image_path": path})

        elif tool_name == "window_management":
            return ("action", {"action": "list"})

        elif tool_name == "keyring_management":
            return ("action", {"action": "list"})

        return None

    def execute(
        self,
        user_text: str,
        mcp_manager: Optional[Any] = None,
        llm_fallback: Optional[Any] = None,
        is_voice: bool = False,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Execute the skill based on user text.

        Returns:
            (success: bool, response_text: str, result_data: dict or None)
        """
        if not user_text or not user_text.strip():
            return (False, "Testo di input vuoto.", None)

        # 0. Tool nativi interni di sistema (data e ora)
        if self.tool == "date_time" or self.intent in ("get_time", "get_date"):
            from core.locale_utils import get_current_time_str, get_current_date_str
            action_result = self._infer_action_from_text(user_text, "date_time")
            action = action_result[0] if action_result else (self.args.get("format") or "time")
            if action in ("date", "giorno") or self.intent == "get_date":
                resp = get_current_date_str()
            else:
                resp = get_current_time_str()
            return (True, resp, {"action": action, "response": resp})

        # 1. Risposta statica
        if self.action_type == "response":
            resp = self.response or self.body or "Azione completata."
            return (True, resp, {"response": resp})

        # 2. Comando terminale / script
        if self.action_type == "command":
            if is_voice:
                return (False, "I comandi di sistema non possono essere eseguiti tramite richiesta vocale per motivi di sicurezza.", None)
            cmd = (self.command or "").strip()
            if not cmd:
                return (False, "Nessun comando configurato per la skill.", None)
            import subprocess
            try:
                proc = subprocess.run(
                    cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=15.0,
                )
                stdout = (proc.stdout or "").strip()
                stderr = (proc.stderr or "").strip()
                if proc.returncode == 0:
                    msg = stdout if stdout else "Comando eseguito con successo."
                    return (True, msg, {"stdout": stdout, "returncode": 0})
                else:
                    err_msg = stderr if stderr else f"Comando terminato con codice {proc.returncode}."
                    return (False, f"Errore esecuzione comando: {err_msg}", {"stderr": stderr, "returncode": proc.returncode})
            except subprocess.TimeoutExpired:
                return (False, "Timeout durante l'esecuzione del comando.", None)
            except Exception as e:
                return (False, f"Errore durante l'esecuzione del comando: {e}", None)

        # 3. Istruzione AI / Prompt personalizzato
        if self.action_type == "prompt":
            if not llm_fallback:
                return (False, "Nessun modello LLM disponibile per questa skill.", None)
            prompt_instruction = self.prompt or self.body or ""
            full_prompt = (
                f"Istruzioni per l'assistente ({self.name}):\n{prompt_instruction}\n\n"
                f"Richiesta dell'utente:\n{user_text}"
            )
            try:
                reply = llm_fallback(full_prompt)
                return (True, reply, {"prompt": prompt_instruction})
            except Exception as e:
                logger.warning(f"Errore esecuzione prompt LLM: {e}")
                return (False, f"Errore durante la risposta dell'AI: {e}", None)

        # 4. Azione di sistema (MCP)
        if self.tool and mcp_manager:
            try:
                inferred = self._infer_action_from_text(user_text, self.tool)
                action_name = inferred[0] if inferred else "execute"
                params = dict(self.args)
                if inferred and inferred[1]:
                    params.update(inferred[1])
                res = mcp_manager.execute_tool(self.tool, params)
                if hasattr(res, "__await__") or asyncio.iscoroutine(res):
                    from core.async_bridge import run_async
                    result = run_async(res, timeout=10.0)
                else:
                    result = res
                response = self._generate_response(self.tool, action_name, result)
                return (True, response, result)
            except Exception as e:
                logger.debug(f"Explicit tool {self.tool} execution failed: {e}")

        if not self.tools_allowed and not self._extract_tool_keywords_from_body():
            # No tools defined; fallback to LLM if available
            if llm_fallback:
                try:
                    response = llm_fallback(
                        f"Esegui il seguente compito: {user_text}\n\nIstruzioni skill: {self.body}"
                    )
                    return (True, response, None)
                except Exception as e:
                    logger.warning(f"LLM fallback failed: {e}")
            return (False, f"Skill {self.name} non può essere eseguita senza LLM.", None)

        # Determine which tools to try
        tools_to_try = [self.tool] if self.tool else (self.tools_allowed or self._extract_tool_keywords_from_body())

        if not mcp_manager:
            return (
                False,
                "MCP Manager non disponibile per esecuzione tool.",
                None,
            )

        # Try each allowed tool with inferred action
        for tool_name in tools_to_try:
            action_result = self._infer_action_from_text(user_text, tool_name)
            if action_result:
                action_name, action_params = action_result
                try:
                    res = mcp_manager.execute_tool(tool_name, action_params)
                    if hasattr(res, "__await__") or asyncio.iscoroutine(res):
                        from core.async_bridge import run_async
                        result = run_async(res, timeout=10.0)
                    else:
                        result = res
                    response = self._generate_response(tool_name, action_name, result)
                    return (True, response, result)
                except Exception as e:
                    logger.debug(f"Tool {tool_name} execution failed: {e}")
                    continue

        # No tool matched; fallback to LLM if available
        if llm_fallback:
            try:
                response = llm_fallback(
                    f"Esegui il seguente compito usando le istruzioni della skill:\n\n"
                    f"Skill: {self.name}\n"
                    f"Istruzioni: {self.body}\n"
                    f"Richiesta utente: {user_text}"
                )
                return (True, response, None)
            except Exception as e:
                logger.warning(f"LLM fallback failed: {e}")

        return (False, f"Impossibile eseguire skill {self.name}.", None)

    def _generate_response(
        self, tool_name: str, action: str, result: Any
    ) -> str:
        """Generate a standardized response based on tool execution result."""
        templates = self.STANDARD_RESPONSES.get(tool_name, {})
        if not templates:
            # Fallback for alias if queried
            if tool_name == "system_volume":
                templates = self.STANDARD_RESPONSES.get("set_volume", {})
            elif tool_name == "dark_mode":
                templates = self.STANDARD_RESPONSES.get("quick_settings", {})
            elif tool_name == "app_launcher":
                templates = self.STANDARD_RESPONSES.get("launch_application", {})
            elif tool_name == "system_media":
                templates = self.STANDARD_RESPONSES.get("media_control", {})

        template = templates.get(action, f"Azione {action} completata.")

        try:
            if isinstance(result, dict):
                return template.format(**result)
            else:
                return template.format(result=result)
        except (KeyError, TypeError):
            return template
