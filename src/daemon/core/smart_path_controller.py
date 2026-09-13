"""Smart Path Controller for Multi-Turn Conversational Context.

Orchestrates RAG, memory, prompting, and tool calling for the SMART PATH pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import json
import threading
from typing import Any, Dict, List, Optional, Tuple

from services.memory_manager import ConversationMemory
from services.prompt_builder import PromptBuilder
from services.rag_store import VectorStore
from services.tool_call_parser import ToolCall, ToolCallParser
from skills.skill_registry import SkillRegistry

logger = logging.getLogger("VoiceAssistant.SmartPathController")


class SmartPathController:
    """Orchestrates SMART PATH pipeline with RAG, memory, and tool calling."""

    def __init__(
        self,
        memory_max_messages: int = 20,
        vector_store_max_docs: int = 1000,
        memory_enabled: bool = True,
    ):
        """Initialize Smart Path controller."""
        self._memory_enabled = bool(memory_enabled)
        self.memory = ConversationMemory(max_messages=memory_max_messages)
        self.vector_store = VectorStore(max_documents=vector_store_max_docs)
        self.prompt_builder = PromptBuilder(include_chat_history=self._memory_enabled)
        self.tool_parser = ToolCallParser()
        self.skill_registry = SkillRegistry.from_default_directory()
        self.last_tool_result: Optional[Dict[str, Any]] = None

    @property
    def memory_enabled(self) -> bool:
        return self._memory_enabled

    @memory_enabled.setter
    def memory_enabled(self, value: bool) -> None:
        self._memory_enabled = bool(value)
        if hasattr(self, 'prompt_builder') and self.prompt_builder:
            self.prompt_builder.include_chat_history = self._memory_enabled
        if not self._memory_enabled and hasattr(self, 'memory') and self.memory:
            self.memory.clear()

    def clear_memory(self) -> None:
        """Clear conversation memory."""
        if hasattr(self, 'memory') and self.memory:
            self.memory.clear()

    def get_conversation_summary(self) -> str:
        """Get summary of current conversation memory."""
        if hasattr(self, 'memory') and self.memory:
            return self.memory.get_summary()
        return ""

    def get_stats(self) -> Dict[str, Any]:
        """Get Smart Path controller statistics."""
        memory_count = len(self.memory.messages) if hasattr(self, 'memory') and self.memory else 0
        return {
            "memory_messages": memory_count,
            "rag_documents": len(self.vector_store.documents),
            "available_skills": len(self.skill_registry.skills),
            "memory_enabled": self._memory_enabled,
        }

    @staticmethod
    def _resolve_maybe_async(value: Any) -> Any:
        if not asyncio.iscoroutine(value):
            return value
        from core.async_bridge import run_async
        return run_async(value)

    @staticmethod
    def _emit_sentence_chunks(text: str, buffer: str, sentence_callback: Optional[Any]) -> str:
        if not sentence_callback:
            return buffer

        buffer += text
        parts = []
        start = 0
        for idx, char in enumerate(buffer):
            if char in ".!?\n":
                sentence = buffer[start:idx + 1].strip()
                if sentence:
                    parts.append(sentence)
                start = idx + 1

        for sentence in parts:
            sentence_callback(sentence)

        return buffer[start:]

    def _execute_tool_call(self, tool_call: ToolCall, mcp_manager: Optional[Any]) -> Optional[Any]:
        if not mcp_manager:
            return None

        try:
            result = mcp_manager.execute_tool(tool_call.tool_name, tool_call.args)
            result = self._resolve_maybe_async(result)
            logger.info(f"[SmartPath] Tool executed: {tool_call.tool_name}")
            return result
        except Exception as e:
            logger.error(f"[SmartPath] Tool execution failed: {e}")
            return None

    def add_user_message(self, text: str, context: Any = None) -> None:
        """Record user message in memory."""
        ctx_id = getattr(context, "context_id", None) if context else "voice"
        if context:
            # GUI chats always record messages for transcript, even if memory is disabled
            if ctx_id != "voice" or self._memory_enabled:
                context.add_message("user", text)
        elif self._memory_enabled:
            # Voice memory
            self.memory.add_user_message(text)
            ctx_id = "voice"

        # Only add to RAG if memory is enabled
        if self._memory_enabled:
            import hashlib
            doc_id = hashlib.md5(f"{ctx_id}\0{text}".encode("utf-8")).hexdigest()[:16]
            self.vector_store.add_document(
                content=text,
                metadata={"source": "user_input", "context_id": ctx_id},
                doc_id=doc_id,
            )

    def add_assistant_message(self, text: str, metadata: Any = None, context: Any = None) -> None:
        """Record assistant message in memory."""
        if context is None and metadata is not None and not isinstance(metadata, dict):
            context = metadata
            metadata = None
        ctx_id = getattr(context, "context_id", None) if context else "voice"
        if context:
            if ctx_id != "voice" or self._memory_enabled:
                context.add_message("assistant", text, metadata=metadata)
        elif self._memory_enabled:
            self.memory.add_assistant_message(text, metadata=metadata)
            ctx_id = "voice"

        # Add non-tool-call text to RAG if memory is enabled
        if self._memory_enabled:
            text_only = self.tool_parser.extract_text_response(text)
            if text_only.strip():
                import hashlib
                doc_id = hashlib.md5(f"{ctx_id}\0{text_only}".encode("utf-8")).hexdigest()[:16]
                self.vector_store.add_document(
                    content=text_only,
                    metadata={"source": "assistant_response", "context_id": ctx_id},
                    doc_id=doc_id,
                )

    def build_smart_prompt(
        self,
        user_message: str,
        rag_query: Optional[str] = None,
        use_rag: bool = True,
        use_history: bool = True,
        context: Any = None,
    ) -> List[Dict[str, str]]:
        """Build a contextual prompt with RAG and history."""
        rag_results = None
        ctx_id = getattr(context, "context_id", None) if context else None
        if use_rag:
            query = rag_query or user_message
            rag_results = self.vector_store.search(query, top_k=3, min_score=0.15, context_id=ctx_id)

        chat_history = None
        if use_history and self._memory_enabled:
            if context:
                chat_history = context.get_messages_for_llm()
            elif hasattr(self, 'memory') and self.memory:
                chat_history = self.memory.get_context_window()

        messages = self.prompt_builder.build_conversation_messages(
            user_message,
            rag_results=rag_results,
            chat_history=chat_history,
            skills_available=[s for s in self.skill_registry.skills[:5]],
        )

        return messages

    def parse_llm_response(self, response_text: str) -> Tuple[List[ToolCall], str]:
        """Extract tool calls and text response from LLM output.

        Args:
            response_text: Raw LLM response text

        Returns:
            (List of ToolCalls, text_only_response)
        """
        tool_calls, remaining_text = self.tool_parser.parse_all(response_text)

        # Validate tool calls
        valid_calls = []
        for call in tool_calls:
            if self.tool_parser.validate_args(call.tool_name, call.args):
                valid_calls.append(call)
                logger.info(f"[SmartPath] Valid tool call: {call.tool_name}")
            else:
                logger.warning(f"[SmartPath] Invalid args for {call.tool_name}: {call.args}")

        return (valid_calls, remaining_text.strip())

    def execute_smart_path(
        self,
        user_message: str,
        llm_streamer: Optional[Any] = None,
        mcp_manager: Optional[Any] = None,
        token_callback: Optional[Any] = None,
        sentence_callback: Optional[Any] = None,
        context: Any = None,
        extra_context: str = "",
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Execute the full SMART PATH pipeline.

        Args:
            user_message: User input text
            llm_streamer: LLM streaming callable (takes history parameter)
            mcp_manager: MCP manager for tool execution
            token_callback: Optional token streaming callback
            sentence_callback: Optional sentence callback
            context: Optional ConversationContext object
            extra_context: Optional extra context string injected into system prompt

        Returns:
            (success, response_text, tool_result)
        """
        # 1. Read history before adding current message
        history_msgs = None
        if self._memory_enabled:
            if context:
                history_msgs = context.get_messages_for_llm()
            elif hasattr(self, 'memory') and self.memory:
                history_msgs = self.memory.get_context_window()

        # 2. Search RAG before adding current message
        ctx_id = getattr(context, "context_id", None) if context else None
        rag_results = self.vector_store.search(user_message, top_k=3, min_score=0.15, context_id=ctx_id)
        rag_text = self.prompt_builder.format_context(rag_results=rag_results)
        llm_context = "\n\n".join(p for p in (rag_text, extra_context) if p)

        # 3. Add to memory / transcript
        self.add_user_message(user_message, context=context)

        # 4. Get LLM response
        if not llm_streamer:
            return (False, "LLM non disponibile.", None)

        try:
            try:
                stream = llm_streamer(user_message, history=history_msgs, context=llm_context)
            except TypeError:
                try:
                    stream = llm_streamer(user_message, history=history_msgs)
                except TypeError:
                    stream = llm_streamer(user_message)

            llm_response, text_response, tool_result = self._consume_llm_stream(
                stream,
                mcp_manager=mcp_manager,
                token_callback=token_callback,
                sentence_callback=sentence_callback,
            )
        except Exception as e:
            logger.error(f"[SmartPath] LLM streaming error: {e}")
            return (False, f"Errore LLM: {e}", None)

        # 5. Record response in memory
        self.add_assistant_message(llm_response, context=context)

        return (True, text_response or "Ok", tool_result)

    def _consume_llm_stream(
        self,
        token_stream: Any,
        mcp_manager: Optional[Any] = None,
        token_callback: Optional[Any] = None,
        sentence_callback: Optional[Any] = None,
    ) -> Tuple[str, str, Optional[Any]]:
        """Consume an LLM stream while hiding tool JSON from GUI/TTS and executing it early."""
        llm_response = ""
        visible_response = ""
        sentence_buffer = ""
        json_buffer = ""
        visible_chunk = ""
        in_json = False
        brace_depth = 0
        in_string = False
        escape = False
        tool_result = None

        def emit_visible(text: str):
            nonlocal visible_response, sentence_buffer
            if not text:
                return
            visible_response += text
            if token_callback:
                token_callback(text)
            sentence_buffer = self._emit_sentence_chunks(text, sentence_buffer, sentence_callback)

        def finish_json_buffer():
            nonlocal json_buffer, tool_result
            try:
                payload = json.loads(json_buffer)
            except json.JSONDecodeError:
                emit_visible(json_buffer)
                json_buffer = ""
                return

            tool_name = payload.get("tool") or payload.get("tool_name")
            args = payload.get("args") or payload.get("parameters") or {}
            if not tool_name:
                emit_visible(json_buffer)
                json_buffer = ""
                return

            candidate = ToolCall(str(tool_name), args if isinstance(args, dict) else {})
            if not self.tool_parser.validate_args(candidate.tool_name, candidate.args):
                logger.warning(f"[SmartPath] Invalid args for {candidate.tool_name}: {candidate.args}")
                json_buffer = ""
                return

            result = self._execute_tool_call(candidate, mcp_manager)
            if result is not None:
                tool_result = result
                result_text = str(result)
                if result_text:
                    emit_visible(("\n" if visible_response.strip() else "") + result_text)
            json_buffer = ""

        for token in token_stream:
            token = str(token)
            llm_response += token
            visible_chunk = ""

            for char in token:
                if not in_json:
                    if char == "{":
                        emit_visible(visible_chunk)
                        visible_chunk = ""
                        in_json = True
                        brace_depth = 1
                        in_string = False
                        escape = False
                        json_buffer = char
                    else:
                        visible_chunk += char
                    continue

                json_buffer += char
                if escape:
                    escape = False
                    continue
                if char == "\\":
                    escape = True
                    continue
                if char == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if char == "{":
                    brace_depth += 1
                elif char == "}":
                    brace_depth -= 1
                    if brace_depth == 0:
                        in_json = False
                        finish_json_buffer()

            emit_visible(visible_chunk)

        if json_buffer:
            emit_visible(json_buffer)

        if sentence_callback and sentence_buffer.strip():
            sentence_callback(sentence_buffer.strip())

        # Fallback parser for responses that arrived as one malformed/non-streamed block.
        if tool_result is None:
            tool_calls, parsed_visible = self.parse_llm_response(llm_response)
            if tool_calls:
                visible_response = parsed_visible
                for tool_call in tool_calls:
                    result = self._execute_tool_call(tool_call, mcp_manager)
                    if result is not None:
                        tool_result = result

        return llm_response, visible_response.strip(), tool_result

    def get_conversation_summary(self) -> str:
        """Get summary of current conversation for logging/debugging."""
        return self.memory.get_summary()

    def clear_memory(self) -> None:
        """Clear conversation memory."""
        self.memory.clear()
        logger.info("[SmartPath] Conversation memory cleared")

    def get_stats(self) -> Dict[str, Any]:
        """Get controller statistics."""
        return {
            "memory_messages": len(self.memory.messages),
            "rag_documents": self.vector_store.get_size(),
            "available_skills": len(self.skill_registry.skills),
        }
