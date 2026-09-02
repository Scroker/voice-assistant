"""Advanced Prompt Building with Context Injection for Smart Path.

Constructs rich prompts with RAG context, chat history, and skill metadata.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("VoiceAssistant.PromptBuilder")


class PromptBuilder:
    """Builds contextual prompts for LLM with RAG and memory injection."""

    SYSTEM_PROMPT_TEMPLATE = """Tu sei un assistente vocale intelligente integrato in GNOME Desktop.

Capacità:
- Controlli diretti del sistema (volume, tema, app, media)
- Ricerca ed esecuzione di comandi
- Risposta a domande conversazionali
- Esecuzione di tool MCP quando appropriato

Istruzioni:
1. Rispondi in italiano, brevemente e chiaramente
2. Se l'utente richiede un'azione di sistema (volume, tema, app), rispondi con:
   {{"tool": "<tool_name>", "args": {{...}}}}
3. Se la richiesta è conversazionale, rispondi normalmente
4. Se non conosci la risposta, dillo chiaramente

Tool disponibili:
- system_volume: {{"action": "increase|decrease|set|mute", "level": 0-100}}
- dark_mode: {{"action": "set", "mode": "dark|light"}}
- app_launcher: {{"action": "launch", "app_name": "..."}}
- date_time: {{"action": "time|date"}}
- screen_brightness: {{"action": "increase|decrease|set", "level": 0-100}}
- system_media: {{"action": "play|pause|next|previous"}}

Contesto attuale:
{context}
"""

    def __init__(
        self,
        system_prompt: Optional[str] = None,
        include_rag_context: bool = True,
        include_chat_history: bool = True,
    ):
        """Initialize prompt builder.

        Args:
            system_prompt: Custom system prompt template
            include_rag_context: Whether to inject RAG results
            include_chat_history: Whether to inject conversation history
        """
        self.system_prompt_template = system_prompt or self.SYSTEM_PROMPT_TEMPLATE
        self.include_rag_context = include_rag_context
        self.include_chat_history = include_chat_history

    def build_prompt(
        self,
        user_message: str,
        rag_results: Optional[List[tuple]] = None,
        chat_history: Optional[List[Dict[str, str]]] = None,
        skills_available: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Build a complete prompt with context.

        Args:
            user_message: The current user message
            rag_results: List of (content, score) tuples from RAG search
            chat_history: List of {"role": "...", "content": "..."} dicts
            skills_available: List of available skill definitions

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

        # Build system prompt with context
        system_prompt = self.system_prompt_template.format(context=context_str)

        return system_prompt

    def build_conversation_messages(
        self,
        user_message: str,
        rag_results: Optional[List[tuple]] = None,
        chat_history: Optional[List[Dict[str, str]]] = None,
        skills_available: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, str]]:
        """Build OpenAI-compatible message list for LLM.

        Args:
            user_message: Current user input
            rag_results: RAG search results
            chat_history: Conversation history
            skills_available: Available skills

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
