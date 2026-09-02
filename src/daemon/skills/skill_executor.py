"""Skill Executor Engine for SKILL.md declarative actions and tool mapping.

This module provides the core engine for executing markdown-defined skills with:
- Declarative action parsing from skill body instructions
- Automatic tool name recognition and validation
- Standardized response generation
- Optional LLM fallback for complex or ambiguous skills
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("VoiceAssistant.SkillExecutor")


class SkillExecutor:
    """Executes a matched SKILL.md skill with tool mapping and response generation."""

    # Common tool patterns recognized in skill bodies
    TOOL_KEYWORDS = {
        "volume": "system_volume",
        "brightness": "screen_brightness",
        "theme": "dark_mode",
        "app": "app_launcher",
        "application": "app_launcher",
        "time": "date_time",
        "date": "date_time",
        "media": "system_media",
        "music": "system_media",
        "notification": "notification",
    }

    STANDARD_RESPONSES = {
        "system_volume": {
            "set": "Volume impostato a {level}%.",
            "increase": "Volume alzato.",
            "decrease": "Volume abbassato.",
            "mute": "Audio silenziato.",
        },
        "dark_mode": {
            "dark": "Tema scuro attivato.",
            "light": "Tema chiaro attivato.",
        },
        "app_launcher": {
            "launch": "Lancio {app_name}.",
        },
        "date_time": {
            "time": "Ecco l'orario: {result}",
            "date": "Ecco la data: {result}",
        },
        "screen_brightness": {
            "set": "Luminosità impostata a {level}%.",
            "increase": "Luminosità aumentata.",
            "decrease": "Luminosità diminuita.",
        },
        "system_media": {
            "play": "Riproduzione avviata.",
            "pause": "Riproduzione in pausa.",
            "next": "Brano successivo.",
            "previous": "Brano precedente.",
        },
    }

    def __init__(self, skill: Dict[str, Any]):
        """Initialize executor with a skill definition."""
        self.skill = skill
        self.intent = skill.get("intent", "")
        self.name = skill.get("name", "")
        self.body = skill.get("_body", "")
        self.tools_allowed = skill.get("tools_allowed", [])
        self.triggers = skill.get("triggers", [])
        self.description = skill.get("description", "")

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

        if tool_name == "system_volume":
            if any(w in text_lower for w in ["alza", "aumenta", "più forte"]):
                return ("increase", {"action": "increase", "level": 10})
            if any(w in text_lower for w in ["abbassa", "riduc", "più basso"]):
                return ("decrease", {"action": "decrease", "level": 10})
            if any(w in text_lower for w in ["silen", "mute", "zitto"]):
                return ("mute", {"action": "mute"})
            if "volume" in text_lower and re.search(r"\d+", text_lower):
                match = re.search(r"(\d+)", text_lower)
                if match:
                    level = int(match.group(1))
                    return ("set", {"action": "set", "level": min(100, max(0, level))})
            return ("increase", {"action": "increase", "level": 10})

        elif tool_name == "dark_mode":
            if any(w in text_lower for w in ["scuro", "dark", "night", "nero"]):
                return ("dark", {"action": "set", "mode": "dark"})
            if any(
                w in text_lower for w in ["chiaro", "light", "day", "bianco", "giorno"]
            ):
                return ("light", {"action": "set", "mode": "light"})
            return None

        elif tool_name == "app_launcher":
            apps = ["firefox", "browser", "terminale", "calendar", "impostazioni"]
            for app in apps:
                if app in text_lower:
                    return ("launch", {"action": "launch", "app_name": app})
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

        elif tool_name == "system_media":
            if any(w in text_lower for w in ["play", "riproduci", "avvia"]):
                return ("play", {"action": "play"})
            if any(w in text_lower for w in ["pausa", "stop", "interrompi"]):
                return ("pause", {"action": "pause"})
            if any(w in text_lower for w in ["prossimo", "next", "successivo"]):
                return ("next", {"action": "next"})
            if any(w in text_lower for w in ["precedente", "prev", "indietro"]):
                return ("previous", {"action": "previous"})
            return None

        return None

    def execute(
        self,
        user_text: str,
        mcp_manager: Optional[Any] = None,
        llm_fallback: Optional[Any] = None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Execute the skill based on user text.

        Returns:
            (success: bool, response_text: str, result_data: dict or None)
        """
        if not user_text or not user_text.strip():
            return (False, "Testo di input vuoto.", None)

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
        tools_to_try = self.tools_allowed or self._extract_tool_keywords_from_body()

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
                    result = mcp_manager.execute_tool(tool_name, action_params)
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
        template = templates.get(action, f"Azione {action} completata.")

        try:
            if isinstance(result, dict):
                return template.format(**result)
            else:
                return template.format(result=result)
        except (KeyError, TypeError):
            return template
