# SMART PATH Integration Guide

## Overview

The Voice Assistant implements a **dual-path dispatch architecture** for intelligent query handling, all inside `PipelineController` (`src/daemon/core/pipeline.py`):

1. **FAST PATH** (<10ms): Regex patterns + sparse vector semantic matching — **off by default**, user-toggleable via the `fast-path-enabled` GSettings key (Preferences → General → Command Dispatch)
2. **MEDIUM PATH**: single-shot LLM tool selection (`_try_llm_tool_select`), run between Fast-Path and Smart-Path when both `mcp_manager` and `llm_streamer` are available — not covered by this guide's original scope, see [`docs/pipeline.md`](pipeline.md#25-medium-path-_try_llm_tool_select)
3. **SMART PATH** (50-500ms): RAG retrieval + conversation memory + LLM tool calling
4. **Fallback** (streaming): Traditional LLM streaming with token-based TTS

## Architecture

```
User Input (STT)
    ↓
Fast-Path Matcher (off by default, user-toggleable — see note above)
    ├─ Regex patterns (exact match)
    ├─ Semantic similarity (score ≥ 0.35, VectorIntentMatcher default — not 85%)
    └─ Known intents (system_control, theme_control, etc.)
    ↓ No match → Medium-Path LLM tool selection → proceed to SMART PATH if unresolved
    
Smart-Path Controller (if mcp_manager available)
    ├─ Add to conversation memory (sliding window: 20 messages)
    ├─ Build contextual prompt:
    │   ├─ Retrieve similar documents from RAG store
    │   ├─ Inject conversation history
    │   ├─ List available skills/tools
    │   └─ System instructions for tool calling
    ├─ Stream LLM response
    ├─ Extract JSON tool calls:
    │   ├─ System tool calls (format: {"tool": "name", "args": {...}})
    │   ├─ Multi-tool support (multiple calls in one response)
    │   └─ Validation against allowed tools
    ├─ Execute tools via MCP:
    │   ├─ Call mcp_manager.execute_tool(name, args)
    │   ├─ Collect results
    │   └─ Handle errors gracefully
    ├─ Record interaction in memory and RAG
    └─ Return text response + tool result
    ↓ If MCP unavailable or SMART PATH fails → fallback
    
LLM Streaming Fallback
    └─ Traditional token streaming (no tool calling)
```

## Components

### 1. Fast-Path Dispatcher (`FastPathDispatcher` class inside `src/daemon/core/pipeline.py`, not a separate `fast_path_dispatcher.py` file)
- **Purpose**: Immediate dispatch for known patterns
- **Latency**: <10ms
- **Status**: off by default; `core/runtime_manager.py` reads the `fast-path-enabled` GSettings key at pipeline construction and `core/assistant_runtime.py` applies changes live
- **Patterns**:
  - Regex rules for fixed responses
  - `VectorIntentMatcher` (`src/daemon/skills/vector_intent_matcher.py`) for semantic similarity, default `min_score=0.35` (no `SEMANTIC_SIMILARITY_THRESHOLD` constant exists in the codebase)
  - Stopword filtering (`STOPWORDS` set in the same file) to prevent false positives
- **Example**:
  ```python
  # Input: "Accendi la luce della cucina"
  # Pattern matches "turn_on_light" intent
  # Response via FastPathDispatcher → direct MCP execution
  ```

### 2. Smart-Path Controller (`src/daemon/core/smart_path_controller.py`)
- **Purpose**: Contextual LLM with memory + RAG + tool calling
- **Latency**: 50-500ms (depends on LLM response time)
- **Construction**: `pipeline.py` instantiates it with no arguments (`SmartPathController()`); its memory/RAG capacity come from `ConversationMemory`/`VectorStore` constructor defaults, not from parameters passed through `PipelineController`
- **Flow**:
  1. **Memory Management**: Add user message to sliding window
  2. **Prompt Building**: Inject RAG results + conversation context
  3. **LLM Streaming**: Get LLM response with tools available
  4. **Tool Parsing**: Extract JSON-formatted tool calls
  5. **Tool Execution**: Run via MCP if allowed
  6. **Memory Recording**: Store interaction for future context

### 3. Conversation Memory (`src/daemon/services/memory_manager.py`)
- **Type**: Sliding window buffer
- **Capacity**: 20 messages (configurable)
- **TTL**: 1 hour per message
- **Format**: OpenAI-compatible ({"role": "user/assistant", "content": "..."})
- **Features**:
  - Automatic eviction of old messages
  - Summary generation for long conversations
  - JSON serialization for logging

### 4. RAG Vector Store (`src/daemon/services/rag_store.py`)
- **Type**: Hybrid in-memory + **persistent SQLite** vector database (`~/.local/share/voice-assistant/rag_store.db`, background sync every 30s) — no ML library dependency
- **Vectors**: Sparse token-based, plain **term-frequency** (count / total) — there is no IDF component, so this is not TF-IDF despite the name similarity
- **Search**: Cosine similarity with configurable threshold (`search()` default `min_score=0.1`; `smart_path_controller.py` calls it with `min_score=0.15`)
- **Deduplication**: Content hash, confirmed. **Eviction is oldest-timestamp-first (FIFO), not LRU** — the timestamp is set once at document creation and never refreshed on access
- **Capacity**: ~1000 documents (`max_documents` constructor default, confirmed)
- **Example Query**:
  ```python
  results = rag_store.search("come controllare le luci", top_k=3)
  # Returns: [Document(content="...", relevance=0.92), ...]
  ```

### 5. Prompt Builder (`src/daemon/services/prompt_builder.py`)
- **Purpose**: Construct context-aware system prompts
- **Inputs**:
  - RAG search results
  - Conversation history
  - Available skills/tools
  - User message
- **Output**: OpenAI-compatible message list
- **Template**:
  ```
  You are a GNOME Voice Assistant...
  Available tools: [list of tool definitions]
  Available skills: [list of SKILL.md descriptions]
  Recent context: [RAG results + history]
  
  When you want to use a tool, format it as:
  {"tool": "tool_name", "args": {"param": "value"}}
  ```

### 6. Tool-Call Parser (`src/daemon/services/tool_call_parser.py`)
- **Purpose**: Extract JSON tool calls from LLM responses
- **Patterns**:
  - Strict JSON blocks: `{"tool": "...", "args": {...}}`
  - Markdown code blocks: `\`\`\`json {...}\`\`\``
  - Inline JSON detection
- **Validation**:
  - Tool must exist in allowed list
  - Arguments must match tool schema
  - Multiple tool calls per response supported

### 7. Skill Executor (`src/daemon/skills/skill_executor.py`)
- **Purpose**: Execute markdown SKILL.md files
- **Action Inference**: Maps user text to tool actions
- **Fallback**: Use LLM to infer best action if ambiguous
- **Features**:
  - Action parameter mapping
  - Tool validation before execution
  - Error handling with user feedback

### 8. Skill Registry (`src/daemon/skills/skill_registry.py`)
- **Format**: YAML frontmatter + markdown body
- **Example SKILL.md**:
  ```yaml
  ---
  name: "System Control"
  description: "Control system settings"
  tools_allowed: ["execute_shell_command"]
  triggers: ["turn_on", "turn_off", "power"]
  intents: ["system_control", "power_management"]
  ---
  
  You can turn devices on/off by using the execute_shell_command tool...
  ```
- **Loading** (`SkillRegistry.from_default_directory()`, searched in this order):
  - `<daemon>/skills/default_skills`
  - `<daemon>/default_skills`
  - User override: `~/.config/voice-assistant/skills`
  - (No dynamic `reload()` method exists on `SkillRegistry` — reloading requires re-instantiating the registry.)

## Pipeline Integration

### Modified Files

#### `src/daemon/core/pipeline.py`
```python
class PipelineController:
    def __init__(self, ..., mcp_manager=None):
        self.smart_path = SmartPathController(...)
        self.mcp_manager = mcp_manager
    
    def process_text_input(self, text, speak=True):
        # 1. Fast-Path Check
        matched, intent, params, response = self.fast_path.dispatch(text)
        if matched and response:
            return {"fast_path": True, ...}
        
        # 2. SMART PATH Check (if MCP available)
        if self.mcp_manager:
            success, response, tool_result = self.smart_path.execute_smart_path(...)
            if success:
                return {"smart_path": True, ...}
        
        # 3. LLM Streaming Fallback
        return self._llm_streaming_fallback(text, speak)
```

#### `src/daemon/core/runtime_manager.py`
```python
def initialize_pipeline(self):
    self.owner.pipeline_controller = PipelineController(
        state_machine=self.owner.state_machine,
        llm_streamer=...,
        tts_engine=...,
        audio_player=...,
        mcp_manager=self.owner.mcp_manager,  # ← NEW
    )
```

## Usage Examples

### Example 1: Fast-Path Hit
```
User: "Accendi la luce del soggiorno"
VectorMatcher: Matches intent "turn_on_light" with 92% confidence
Fast-Path execution:
  - Intent: turn_on_light
  - Params: {"room": "soggiorno", "device": "luce"}
  - MCP tool: execute_dbus_method
  - Result: Light turns on
Latency: <10ms
```

### Example 2: SMART PATH (Complex Query)
```
User: "Quante volte ho chiesto di accendere la luce oggi?"
Fast-Path: No semantic match
SMART PATH execution:
  1. Memory: Retrieve all "turn on light" interactions from today
  2. RAG: Search for similar queries
  3. Prompt: "Based on conversation history, count light-on events"
  4. LLM: Generates response "Ho trovato 5 volte che hai acceso la luce"
  5. Tool Calls: None in this case
  6. Result: Spoken response
Latency: 200-400ms
```

### Example 3: SMART PATH + Tool Calling
```
User: "Accendi tutte le luci e aumenta il volume"
Fast-Path: No exact match
SMART PATH execution:
  1. LLM: Detects need for multiple actions
  2. Response:
     ```
     Accendo tutte le luci e aumento il volume per te.
     {"tool": "turn_on_all_lights", "args": {"room": "all"}}
     {"tool": "set_volume", "args": {"level": 70}}
     ```
  3. Tool execution:
     - Turn on all lights
     - Set volume to 70
  4. Memory: Record both request and actions
  5. Result: Actions executed + spoken confirmation
Latency: 300-600ms
```

## Configuration

There are no module-level constants for any of this — all limits below are constructor default parameters, verified against the current code:

### Memory Limits
```python
# src/daemon/services/memory_manager.py
class ConversationMemory:
    def __init__(self, max_messages: int = 20, max_age_seconds: int = 3600):
        ...
```

### RAG Store Limits
```python
# src/daemon/services/rag_store.py
class VectorStore:
    def __init__(self, max_documents: int = 1000, ttl_seconds: int = ..., ...):
        ...
    def search(self, query, top_k: int = 3, min_score: float = 0.1):
        ...
```

### Fast-Path Thresholds
```python
# FastPathDispatcher class, inside src/daemon/core/pipeline.py
# fast_path_enabled defaults to False and is passed as False in runtime_manager.py

# VectorIntentMatcher, src/daemon/skills/vector_intent_matcher.py
def match(self, text, min_score: float = 0.35):
    ...
```

## Performance Considerations

| Path | Latency | Use Case | Memory | CPU |
|------|---------|----------|--------|-----|
| FAST | <10ms | Known patterns | Low | Low |
| SMART | 100-500ms | Complex queries + tools | Medium | High (LLM) |
| FALLBACK | Streaming | Unmatched queries | Low | High (LLM) |

## Debugging

### Enable Verbose Logging
```bash
export VOICE_ASSISTANT_LOG_LEVEL=DEBUG
systemctl --user restart voice-assistant
```

### Check Smart-Path Execution
```
[Pipeline] Fast-Path no match, attempting SMART PATH: 'quante luci ho acceso?'
[SmartPath] Building prompt with 8 RAG results + 15 history messages
[SmartPath] LLM response: "Ho trovato 3 volte..."
[SmartPath] Tool calls found: 0
[Pipeline] Smart-Path success: 'Ho trovato 3 volte...'
```

### Verify MCP Manager Connection
```python
# In REPL — note the real module is mcp/manager.py, not services/mcp_manager.py
from mcp.manager import MCPManager
mcp = MCPManager()
print(mcp.get_available_tools())
# Should list all registered tools
```

## Troubleshooting

### SMART PATH Not Executing
- **Cause**: `mcp_manager` is None
- **Fix**: Verify MCP services are initialized before pipeline creation

### Memory Bloat
- **Cause**: RAG store growing too large
- **Fix**: Pass a smaller `max_documents` / larger `min_score` when constructing `VectorStore`

### False Positives in FAST-PATH
- **Cause**: Semantic similarity threshold too low, or Fast-Path enabled without expecting it
- **Fix**: Raise the `min_score` passed to `VectorIntentMatcher.match()` or add stopwords, or turn Fast-Path off entirely from Preferences → General → Command Dispatch (`fast-path-enabled`).

### Tool Calls Not Executing
- **Cause**: Tool not in `tools_allowed` list in SKILL.md
- **Fix**: Add tool to skill definition or update MCP registry

## Future Enhancements

1. ~~**Persistent Storage**: SQLite backend for long-term RAG~~ — **already implemented**: `VectorStore` in `rag_store.py` persists to SQLite (`~/.local/share/voice-assistant/rag_store.db`) with a periodic background sync.
2. **Streaming Responses**: Token-by-token output for SMART PATH
3. **Custom RAG Sources**: User-provided documents/knowledge base
4. **Performance Optimization**: Async RAG search + batch tool execution
5. **Advanced Memory**: Summarization for long conversations (GPT-style)
6. **Feedback Loop**: Tool result injection back into LLM for follow-ups

## References

- [Architecture Documentation](./architecture.md)
- [D-Bus Integration](./dbus.md)
- [MCP Guide](./mcp-guide.md)
- [Skills System](./providers.md)
