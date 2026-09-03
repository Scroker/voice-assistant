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
    ):
        """Initialize Smart Path controller.

        Args:
            memory_max_messages: Max conversation history messages
            vector_store_max_docs: Max RAG documents to retain
        """
        self.memory = ConversationMemory(max_messages=memory_max_messages)
        self.vector_store = VectorStore(max_documents=vector_store_max_docs)
        self.prompt_builder = PromptBuilder()
        self.tool_parser = ToolCallParser()
        self.skill_registry = SkillRegistry.from_default_directory()
        self.last_tool_result: Optional[Dict[str, Any]] = None

    @staticmethod
    def _resolve_maybe_async(value: Any) -> Any:
        if not asyncio.iscoroutine(value):
            return value

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            holder: Dict[str, Any] = {}

            def run():
                try:
                    holder["result"] = asyncio.run(value)
                except Exception as exc:
                    holder["error"] = exc

            thread = threading.Thread(target=run, daemon=True)
            thread.start()
            thread.join()
            if "error" in holder:
                raise holder["error"]
            return holder.get("result")

        return asyncio.run(value)

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

    def add_user_message(self, text: str) -> None:
        """Record user message in memory."""
        self.memory.add_user_message(text)
        # Also add to RAG for future retrieval
        self.vector_store.add_document(
            content=text,
            metadata={"source": "user_input"},
        )

    def add_assistant_message(self, text: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Record assistant message in memory."""
        self.memory.add_assistant_message(text, metadata=metadata)
        # Add non-tool-call text to RAG
        text_only = self.tool_parser.extract_text_response(text)
        if text_only.strip():
            self.vector_store.add_document(
                content=text_only,
                metadata={"source": "assistant_response"},
            )

    def build_smart_prompt(
        self,
        user_message: str,
        rag_query: Optional[str] = None,
        use_rag: bool = True,
        use_history: bool = True,
    ) -> List[Dict[str, str]]:
        """Build a contextual prompt with RAG and history.

        Args:
            user_message: Current user input
            rag_query: Query for RAG search (defaults to user_message)
            use_rag: Whether to include RAG context
            use_history: Whether to include chat history

        Returns:
            OpenAI-compatible message list
        """
        rag_results = None
        if use_rag:
            query = rag_query or user_message
            rag_results = self.vector_store.search(query, top_k=3, min_score=0.15)

        chat_history = None
        if use_history:
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
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Execute the full SMART PATH pipeline.

        Args:
            user_message: User input text
            llm_streamer: LLM streaming callable
            mcp_manager: MCP manager for tool execution

        Returns:
            (success, response_text, tool_result)
        """
        # 1. Add to memory
        self.add_user_message(user_message)

        # 2. Build contextual prompt
        messages = self.build_smart_prompt(user_message)

        # 3. Get LLM response
        if not llm_streamer:
            return (False, "LLM non disponibile.", None)

        try:
            llm_response, text_response, tool_result = self._consume_llm_stream(
                llm_streamer(user_message),
                mcp_manager=mcp_manager,
                token_callback=token_callback,
                sentence_callback=sentence_callback,
            )
        except Exception as e:
            logger.error(f"[SmartPath] LLM streaming error: {e}")
            return (False, f"Errore LLM: {e}", None)

        # 6. Record response in memory
        self.add_assistant_message(llm_response)

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
