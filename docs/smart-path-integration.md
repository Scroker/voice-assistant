# SMART PATH Integration Guide

## Overview

The Voice Assistant now implements a **dual-path dispatch architecture** for intelligent query handling:

1. **FAST PATH** (<10ms): Regex patterns + sparse vector semantic matching
2. **SMART PATH** (50-500ms): RAG retrieval + conversation memory + LLM tool calling
3. **Fallback** (streaming): Traditional LLM streaming with token-based TTS

## Architecture

```
User Input (STT)
    ↓
Fast-Path Matcher
    ├─ Regex patterns (exact match)
    ├─ Semantic similarity (>85% threshold)
    └─ Known intents (system_control, theme_control, etc.)
    ↓ No match → proceed to SMART PATH
    
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

### 1. Fast-Path Dispatcher (`src/daemon/core/fast_path_dispatcher.py`)
- **Purpose**: Immediate dispatch for known patterns
- **Latency**: <10ms
- **Patterns**:
  - Regex rules for fixed responses
  - VectorIntentMatcher for semantic similarity
  - Stopword filtering to prevent false positives
- **Example**:
  ```python
  # Input: "Accendi la luce della cucina"
  # Pattern matches "turn_on_light" intent
  # Response via FastPathDispatcher → direct MCP execution
  ```

### 2. Smart-Path Controller (`src/daemon/core/smart_path_controller.py`)
- **Purpose**: Contextual LLM with memory + RAG + tool calling
- **Latency**: 50-500ms (depends on LLM response time)
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
- **Type**: In-memory vector database (no ML library dependency)
- **Vectors**: Sparse token-based (simple word matching + TF-IDF)
- **Search**: Cosine similarity with configurable threshold
- **Deduplication**: Content hash + LRU eviction
- **Capacity**: ~1000 documents (configurable)
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
- **Loading**:
  - Default: `~/.local/share/gnome-shell/extensions/voice-assistant@mkswap.github.io/skills/`
  - User: `~/.config/voice-assistant/skills/`
  - Dynamic reload on demand

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

### Memory Limits
```python
# conversation_memory.py
SLIDING_WINDOW_SIZE = 20  # messages
MESSAGE_TTL_SECONDS = 3600  # 1 hour
```

### RAG Store Limits
```python
# rag_store.py
MAX_DOCUMENTS = 1000
DOCUMENT_TTL_SECONDS = 86400  # 24 hours
MIN_RELEVANCE_SCORE = 0.3
```

### Fast-Path Thresholds
```python
# fast_path_dispatcher.py
SEMANTIC_SIMILARITY_THRESHOLD = 0.85
STOPWORD_FILTERING = True
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
# In REPL
from src.daemon.services.mcp_manager import MCPManager
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
- **Fix**: Increase MIN_RELEVANCE_SCORE or decrease MAX_DOCUMENTS

### False Positives in FAST-PATH
- **Cause**: Semantic similarity threshold too low
- **Fix**: Increase SEMANTIC_SIMILARITY_THRESHOLD or add stopwords

### Tool Calls Not Executing
- **Cause**: Tool not in `tools_allowed` list in SKILL.md
- **Fix**: Add tool to skill definition or update MCP registry

## Future Enhancements

1. **Persistent Storage**: SQLite backend for long-term RAG
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
