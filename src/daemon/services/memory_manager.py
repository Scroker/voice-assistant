"""Memory Management for Chat History and Context Window.

This module provides short-term memory (chat history) and long-term memory management
for multi-turn conversational context in the voice assistant.
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from typing import Any, Dict, List, Optional

logger = logging.getLogger("VoiceAssistant.Memory")


class Message:
    """Single message in conversation history."""

    def __init__(
        self,
        role: str,
        content: str,
        timestamp: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.role = role  # "user" or "assistant"
        self.content = content
        self.timestamp = timestamp or time.time()
        self.metadata = metadata or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Message:
        return cls(
            role=data.get("role", "user"),
            content=data.get("content", ""),
            timestamp=data.get("timestamp"),
            metadata=data.get("metadata", {}),
        )


class ConversationMemory:
    """Short-term memory manager for chat history.

    Maintains a sliding window of recent conversation for multi-turn context.
    """

    def __init__(self, max_messages: int = 20, max_age_seconds: int = 3600):
        """Initialize memory buffer.

        Args:
            max_messages: Maximum number of messages to retain
            max_age_seconds: Maximum age in seconds (1 hour default)
        """
        self.max_messages = max_messages
        self.max_age_seconds = max_age_seconds
        self.messages: deque = deque(maxlen=max_messages)

    def add_message(
        self, role: str, content: str, metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """Add a message to conversation history."""
        msg = Message(role=role, content=content, metadata=metadata)
        self.messages.append(msg)
        logger.debug(f"[Memory] Added {role} message: {content[:50]}...")

    def add_user_message(self, content: str) -> None:
        """Add user message."""
        self.add_message("user", content)

    def add_assistant_message(self, content: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Add assistant response message."""
        self.add_message("assistant", content, metadata)

    def get_recent_messages(self, count: int = 10) -> List[Message]:
        """Get last N messages from history."""
        return list(self.messages)[-count:]

    def get_context_window(self, max_age_seconds: Optional[int] = None) -> List[Dict[str, str]]:
        """Get conversation context as OpenAI-compatible format.

        Returns list of {"role": "...", "content": "..."} dicts.
        """
        max_age = max_age_seconds or self.max_age_seconds
        now = time.time()
        context = []

        for msg in self.messages:
            if now - msg.timestamp <= max_age:
                context.append({"role": msg.role, "content": msg.content})

        return context

    def get_summary(self) -> str:
        """Generate a brief summary of conversation for context injection."""
        if not self.messages:
            return "Nessuna conversazione precedente."

        recent = self.get_recent_messages(5)
        summary_parts = []

        for msg in recent:
            role_label = "Utente" if msg.role == "user" else "Assistente"
            text = msg.content[:100]  # Truncate long messages
            summary_parts.append(f"{role_label}: {text}")

        return "\n".join(summary_parts)

    def clear(self) -> None:
        """Clear all messages."""
        self.messages.clear()
        logger.info("[Memory] Conversation history cleared")

    def export_json(self) -> str:
        """Export history as JSON string."""
        messages = [msg.to_dict() for msg in self.messages]
        return json.dumps(messages, indent=2)

    def import_json(self, json_str: str) -> None:
        """Import history from JSON string."""
        try:
            data = json.loads(json_str)
            self.messages.clear()
            for msg_data in data:
                msg = Message.from_dict(msg_data)
                self.messages.append(msg)
            logger.info(f"[Memory] Imported {len(self.messages)} messages from JSON")
        except Exception as e:
            logger.error(f"[Memory] Failed to import JSON: {e}")
