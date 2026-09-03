"""Tool Call Parser for extracting and validating tool invocations from LLM responses.

Parses JSON tool calls embedded in LLM text and validates against available tools.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("VoiceAssistant.ToolCallParser")


class ToolCall:
    """Represents a single tool invocation."""

    def __init__(self, tool_name: str, args: Dict[str, Any], confidence: float = 1.0):
        self.tool_name = tool_name
        self.args = args
        self.confidence = confidence

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool": self.tool_name,
            "args": self.args,
            "confidence": self.confidence,
        }

    def __repr__(self) -> str:
        return f"ToolCall({self.tool_name}, {self.args}, {self.confidence:.2f})"


class ToolCallParser:
    """Parses tool calls from LLM responses and validates them."""

    # Common JSON patterns in LLM responses
    JSON_PATTERNS = [
        r'\{\s*"tool"\s*:\s*"([^"]+)"\s*,\s*"args"\s*:\s*({.*?})\s*\}',
        r'\{\s*"tool_name"\s*:\s*"([^"]+)"\s*,\s*"parameters"\s*:\s*({.*?})\s*\}',
        r'```json\s*({.*?"tool".*?})\s*```',
    ]

    def __init__(self, available_tools: Optional[List[str]] = None):
        """Initialize parser.

        Args:
            available_tools: List of valid tool names for validation
        """
        self.available_tools = available_tools or [
            "system_volume",
            "dark_mode",
            "app_launcher",
            "date_time",
            "screen_brightness",
            "system_media",
            "system_power",
            "clipboard",
        ]

    def parse(self, text: str) -> Tuple[Optional[ToolCall], str]:
        """Parse tool call from text.

        Returns:
            (ToolCall or None, remaining_text)
        """
        for pattern in self.JSON_PATTERNS:
            matches = re.finditer(pattern, text, re.DOTALL | re.IGNORECASE)
            for match in matches:
                try:
                    if len(match.groups()) >= 2:
                        tool_name = match.group(1)
                        args_str = match.group(2)
                    else:
                        # Fallback: try to parse the whole match as JSON
                        args_str = match.group(1)
                        json_obj = json.loads(args_str)
                        tool_name = json_obj.get("tool") or json_obj.get("tool_name", "")
                        args_str = json.dumps(json_obj.get("args") or json_obj.get("parameters") or {})

                    tool_call = self._validate_and_create(tool_name, args_str)
                    if tool_call:
                        # Remove the matched text from original
                        remaining = text[: match.start()] + text[match.end() :]
                        return (tool_call, remaining)
                except Exception as e:
                    logger.debug(f"[ToolCallParser] Pattern match failed: {e}")
                    continue

        return (None, text)

    def parse_all(self, text: str) -> Tuple[List[ToolCall], str]:
        """Parse all tool calls from text (iterative).

        Returns:
            (List of ToolCalls, remaining_text)
        """
        tool_calls = []
        remaining = text

        while True:
            tool_call, remaining = self.parse(remaining)
            if tool_call:
                tool_calls.append(tool_call)
            else:
                break

        return (tool_calls, remaining)

    def _validate_and_create(self, tool_name: str, args_str: str) -> Optional[ToolCall]:
        """Validate and create a ToolCall object."""
        try:
            # Normalize tool name
            tool_name = tool_name.strip().lower().replace("-", "_")

            # Validate against available tools
            if tool_name not in self.available_tools:
                logger.warning(f"[ToolCallParser] Unknown tool: {tool_name}")
                return None

            # Parse arguments
            args = json.loads(args_str)
            if not isinstance(args, dict):
                args = {}

            return ToolCall(tool_name=tool_name, args=args)
        except json.JSONDecodeError as e:
            logger.debug(f"[ToolCallParser] Invalid JSON for args: {e}")
            return None
        except Exception as e:
            logger.error(f"[ToolCallParser] Unexpected error: {e}")
            return None

    def extract_text_response(self, text: str) -> str:
        """Extract text response after removing tool calls."""
        _, remaining = self.parse_all(text)
        return remaining.strip()

    def validate_args(self, tool_name: str, args: Dict[str, Any]) -> bool:
        """Basic validation of tool arguments."""
        if tool_name == "system_volume":
            if "action" not in args:
                return False
            action = args["action"].lower()
            if action == "set" and ("level" not in args or not 0 <= args["level"] <= 100):
                return False
            return True

        elif tool_name == "dark_mode":
            if "mode" not in args:
                return False
            return args["mode"].lower() in {"dark", "light"}

        elif tool_name == "app_launcher":
            return "app_name" in args

        elif tool_name == "date_time":
            if "format" not in args:
                return False
            return args["format"].lower() in {"time", "date", "full"}

        elif tool_name == "screen_brightness":
            if "action" not in args:
                return False
            action = args["action"].lower()
            if action == "set" and ("level" not in args or not 0 <= args["level"] <= 100):
                return False
            return True

        elif tool_name == "system_media":
            if "action" not in args:
                return False
            return args["action"].lower() in {"play", "pause", "play-pause", "next", "previous", "stop"}

        return True
