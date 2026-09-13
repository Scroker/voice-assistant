"""Advanced Prompt Building with Context Injection for Smart Path.

Constructs rich prompts with RAG context, chat history, and skill metadata.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from core.data_loader import load_text_data

logger = logging.getLogger("VoiceAssistant.PromptBuilder")


def load_default_system_prompt() -> str:
    """Carica il prompt di sistema universale da data/prompts/system_prompt.md con fallback."""
    return load_text_data(
        "prompts/system_prompt.md",
        fallback_default="""You are a voice assistant integrated into the GNOME desktop environment. Your goal is to assist the user by executing system actions or answering questions.

IMPORTANT RULES:
1. Always respond in the language used by the user (e.g., if the user speaks Italian, reply in Italian; if English, reply in English).
2. Keep responses brief, natural, and direct, optimized for spoken voice synthesis (maximum 1 to 2 short sentences, under 25 words whenever possible).
3. Never include reasoning preambles, meta-commentary, or unprompted greetings.
4. When executing system actions or tools, confirm what was done simply and clearly.

{tools_definition}

{context}

{clock}"""
    )


class PromptBuilder:
    """Builds contextual prompts for LLM with RAG and memory injection."""

    # Retained for backwards compatibility
    SYSTEM_PROMPT_TEMPLATE = load_default_system_prompt()

    def __init__(
        self,
        system_prompt: Optional[str] = None,
        include_rag_context: bool = True,
        include_chat_history: bool = True,
        tools_definition: str = "",
        clock_info: str = "",
    ):
        """Initialize prompt builder.

        Args:
            system_prompt: Custom system prompt template
            include_rag_context: Whether to inject RAG results
            include_chat_history: Whether to inject conversation history
            tools_definition: Formatted available tools prompt
            clock_info: Formatted system date/time string
        """
        self.system_prompt_template = system_prompt or load_default_system_prompt()
        self.include_rag_context = include_rag_context
        self.include_chat_history = include_chat_history
        self.tools_definition = tools_definition
        self.clock_info = clock_info

    def format_context(self, rag_results: Optional[List[tuple]] = None) -> str:
        """Format RAG results as a memory context block."""
        if not self.include_rag_context or not rag_results:
            return ""
        lines = ["📚 Memoria rilevante:"]
        for content, score in rag_results[:3]:
            lines.append(f"  • {content[:100]} (rilevanza: {score:.2f})")
        return "\n".join(lines)

    def build_prompt(
        self,
        user_message: str,
        rag_results: Optional[List[tuple]] = None,
        chat_history: Optional[List[Dict[str, str]]] = None,
        skills_available: Optional[List[Dict[str, Any]]] = None,
        tools_definition: Optional[str] = None,
        clock_info: Optional[str] = None,
    ) -> str:
        """Build a complete prompt with context.

        Args:
            user_message: The current user message
            rag_results: List of (content, score) tuples from RAG search
            chat_history: List of {"role": "...", "content": "..."} dicts
            skills_available: List of available skill definitions
            tools_definition: Optional tools definition override
            clock_info: Optional clock info override

        Returns:
            The complete prompt string
        """
        context_parts = []

        # Add RAG context
        if self.include_rag_context and rag_results:
            context_parts.append("📚 Memoria rilevante:")
            for content, score in rag_results[:3]:
                context_parts.append(f"  • {content[:100]} (rilevanza: {score:.2f})")

        # Add recent chat history
        if self.include_chat_history and chat_history:
            context_parts.append("\n💬 Conversazione recente:")
            for msg in chat_history[-3:]:
                role = "Tu" if msg["role"] == "user" else "Assistente"
                context_parts.append(f"  {role}: {msg['content'][:100]}")

        # Add available skills
        if skills_available:
            context_parts.append("\n🛠️ Competenze disponibili:")
            for skill in skills_available[:5]:
                skill_name = skill.get("name", "Unknown")
                context_parts.append(f"  • {skill_name}")

        context_str = "\n".join(context_parts) if context_parts else "Nessun contesto aggiuntivo disponibile."

        # Interpolate into template
        tools_str = tools_definition if tools_definition is not None else self.tools_definition
        clock_str = clock_info if clock_info is not None else self.clock_info

        prompt = self.system_prompt_template
        has_context = "{context}" in prompt
        has_tools = "{tools_definition}" in prompt
        has_clock = "{clock}" in prompt

        if has_context:
            prompt = prompt.replace("{context}", context_str)
        if has_tools:
            prompt = prompt.replace("{tools_definition}", tools_str.strip())
        if has_clock:
            prompt = prompt.replace("{clock}", clock_str.strip())

        if not has_tools and tools_str.strip():
            prompt = f"{prompt.strip()}\n\n{tools_str.strip()}"
        if not has_clock and clock_str.strip():
            prompt = f"{prompt.strip()}\n\n{clock_str.strip()}"
        if not has_context and context_str.strip():
            prompt = f"{prompt.strip()}\n\n{context_str.strip()}"

        prompt = re.sub(r'\n{3,}', '\n\n', prompt).strip()
        return prompt

    def build_conversation_messages(
        self,
        user_message: str,
        rag_results: Optional[List[tuple]] = None,
        chat_history: Optional[List[Dict[str, str]]] = None,
        skills_available: Optional[List[Dict[str, Any]]] = None,
        tools_definition: Optional[str] = None,
        clock_info: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """Build OpenAI-compatible message list for LLM.

        Args:
            user_message: Current user input
            rag_results: RAG search results
            chat_history: Conversation history
            skills_available: Available skills
            tools_definition: Optional tools definition override
            clock_info: Optional clock info override

        Returns:
            List of {"role": "...", "content": "..."} messages
        """
        messages = []

        # Add system prompt
        system_prompt = self.build_prompt(
            user_message,
            rag_results=rag_results,
            chat_history=chat_history,
            skills_available=skills_available,
            tools_definition=tools_definition,
            clock_info=clock_info,
        )
        messages.append({"role": "system", "content": system_prompt})

        # Add chat history if provided
        if self.include_chat_history and chat_history:
            messages.extend(chat_history[-5:])

        # Add current user message
        messages.append({"role": "user", "content": user_message})

        return messages

    def inject_skill_context(self, skill_name: str, skill_body: str) -> str:
        """Inject skill-specific instructions into prompt."""
        return f"Usa questa skill disponibile se rilevante:\n```\n{skill_body[:200]}\n```\n"
