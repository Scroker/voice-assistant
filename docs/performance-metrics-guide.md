# Structured Logging & Performance Metrics - Usage Guide

## Overview
The Voice Assistant daemon now includes a built-in **Structured Logging System** with **Performance Metrics** tracking. This enables:
- ✅ Automatic latency measurement of critical operations
- ✅ Distributed tracing with `operation_id` and `trace_id`
- ✅ Performance threshold alerts and violations
- ✅ Comprehensive performance reports (min/max/avg/p95 latency)
- ✅ JSON export for monitoring dashboards

---

## Quick Start

### 1. Wrap a Pipeline Flow with OperationContext

```python
from core.performance_metrics import OperationContext
from core.logger import setup_logger

# Setup logging with context tracing
setup_logger("VoiceAssistant", enable_context_tracing=True)

# Wrap your operation
with OperationContext("user_query", component="Pipeline", tags={"user_input": "accendi luce"}) as ctx:
    # All logs within this block will include operation_id
    logger.info("Processing user query")
    
    # Record sub-operations
    ctx.record_metric("stt_processing", 250.5, status="success", model="vosk", confidence=0.92)
    ctx.record_metric("fast_path_dispatch", 5.2, status="success", intent="set_volume")
    ctx.record_metric("tts_synthesis", 150.0, status="success", voice="paola")
    
    # Get operation summary
    summary = ctx.get_summary()
    print(summary)
```

**Log Output:**
```
[INFO] [a7f2c1d8] [VoiceAssistant.Pipeline]: Processing user query
[DEBUG] [a7f2c1d8] [VoiceAssistant.Performance]: Metrica: stt_processing = 250.50ms (success)
[DEBUG] [a7f2c1d8] [VoiceAssistant.Performance]: Metrica: fast_path_dispatch = 5.20ms (success)
```

---

### 2. Automatic Latency Measurement with Decorators

```python
from core.performance_metrics import measure_latency, OperationContext

with OperationContext("audio_processing", component="Audio"):
    
    @measure_latency("vosk_decode", component="STT")
    def decode_audio(audio_chunk):
        # Automatically measured and recorded in context metrics
        return vosk_recognizer.AcceptWaveform(audio_chunk)
    
    result = decode_audio(chunk)
    # Metric automatically added to context.metrics["vosk_decode"]
```

---

### 3. Check Performance Thresholds

```python
from core.performance_metrics import get_performance_monitor

monitor = get_performance_monitor()

# Thresholds are predefined:
# - stt_processing: 500ms
# - llm_first_token: 1000ms
# - llm_token_generation: 100ms
# - tts_synthesis: 300ms
# - total_pipeline: 3000ms
# - wakeword_detection: 200ms

# Check if an operation exceeded threshold
is_fast = monitor.check_threshold("stt_processing", 350.0)  # True (350 < 500)
is_fast = monitor.check_threshold("stt_processing", 600.0)  # False (600 > 500)
```

---

### 4. Generate Performance Reports

```python
from core.performance_metrics import get_performance_monitor

monitor = get_performance_monitor()

# Get comprehensive report
report = monitor.get_performance_report()

# Example output:
# {
#   "stt_processing": {
#     "count": 42,
#     "successful": 40,
#     "failed": 2,
#     "avg_ms": 245.5,
#     "min_ms": 120.0,
#     "max_ms": 850.0,
#     "p95_ms": 520.3,
#     "threshold_ms": 500
#   },
#   ...
# }

# Export to JSON for analysis
monitor.export_metrics_json("/tmp/performance_report.json")
```

---

## Architecture

### OperationContext
Thread-safe context manager that groups related operations:
```python
class OperationContext:
    operation_id: str      # Unique ID (8 chars, e.g., "a7f2c1d8")
    trace_id: str         # Trace ID (12 chars, e.g., "7b3d9f2a1e4c")
    operation_name: str   # Human-readable name
    component: str        # Component name (Audio, STT, LLM, etc.)
    metrics: Dict[str, PerformanceMetric]
    tags: Dict[str, Any]  # Optional context tags
```

### PerformanceMetric
Individual measurement dataclass:
```python
@dataclass
class PerformanceMetric:
    operation_name: str       # e.g., "vosk_decode"
    start_time: float        # Unix timestamp
    end_time: float          # Unix timestamp
    duration_ms: float       # Computed duration in milliseconds
    operation_id: str        # Parent operation ID
    trace_id: str           # Parent trace ID
    component: str          # Component name
    status: str             # "pending", "success", or "error"
    error_message: Optional[str]  # Error details if status="error"
    tags: Dict[str, Any]    # Additional context (model, confidence, etc.)
```

### ContextTraceFilter
Logging filter that injects `operation_id` into all log records:
```python
setup_logger("VoiceAssistant", enable_context_tracing=True)

# Now all logs include operation_id automatically:
# [INFO] [operation_id] [logger.name]: message
```

---

## Real-World Example: STT Pipeline

```python
from core.performance_metrics import OperationContext, measure_latency
from core.logger import setup_logger
import logging

setup_logger("VoiceAssistant", enable_context_tracing=True)
logger = logging.getLogger("VoiceAssistant.STT")

def process_audio_stream(audio_device, recognizer):
    """Main STT processing loop with integrated performance tracking."""
    
    with OperationContext("stt_session", component="STT") as ctx:
        
        @measure_latency("audio_capture", component="Audio")
        def capture_chunk():
            return audio_device.read(chunk_size)
        
        @measure_latency("vosk_processing", component="STT")
        def recognize_chunk(chunk):
            return recognizer.AcceptWaveform(chunk)
        
        for i in range(frames_per_buffer):
            # Capture audio
            chunk = capture_chunk()
            
            # Process with recognition
            result = recognize_chunk(chunk)
            
            # Record additional metrics
            ctx.record_metric(
                f"chunk_{i}_vad",
                duration_ms=compute_vad_time(chunk),
                status="success",
                confidence=compute_confidence(chunk),
                energy=compute_energy(chunk)
            )
        
        # Get summary with all metrics
        summary = ctx.get_summary()
        logger.info(f"STT session complete: {summary['total_duration_ms']}ms")
```

---

## Performance Thresholds

| Operation | Threshold | Rationale |
|-----------|-----------|-----------|
| `audio_capture` | 100ms | Audio chunk duration (10ms buffer) |
| `wakeword_detection` | 200ms | Wakeword recognition should be fast |
| `stt_processing` | 500ms | Complete STT pass from audio to text |
| `llm_first_token` | 1000ms | LLM inference initialization + first token |
| `llm_token_generation` | 100ms | Subsequent token generation |
| `tts_synthesis` | 300ms | TTS audio generation per sentence |
| `total_pipeline` | 3000ms | Complete end-to-end flow |

---

## Monitoring Dashboard Integration

Export metrics for Prometheus/Grafana:

```python
monitor = get_performance_monitor()
report = monitor.get_performance_report()

# Convert to Prometheus format
for op_name, stats in report.items():
    print(f"{op_name}_avg_ms {stats['avg_ms']}")
    print(f"{op_name}_p95_ms {stats['p95_ms']}")
    print(f"{op_name}_failed_count {stats['failed']}")
```

---

## Testing with Performance Metrics

```python
import unittest
from core.performance_metrics import OperationContext, get_performance_monitor

class TestPipelinePerformance(unittest.TestCase):
    
    def test_stt_latency_under_threshold(self):
        monitor = get_performance_monitor()
        
        with OperationContext("perf_test", component="STT") as ctx:
            ctx.record_metric("vosk_decode", 250.0, status="success")
        
        # Verify threshold not exceeded
        is_fast = monitor.check_threshold("vosk_decode", 250.0)
        self.assertTrue(is_fast)
    
    def test_performance_report_generation(self):
        monitor = get_performance_monitor()
        monitor.clear_metrics()
        
        # Simulate multiple operations
        for i in range(10):
            monitor._metrics["test_op"].append({
                "operation_name": "test_op",
                "duration_ms": 100.0 + i,
                "status": "success"
            })
        
        report = monitor.get_performance_report()
        self.assertEqual(report["test_op"]["count"], 10)
        self.assertGreater(report["test_op"]["avg_ms"], 100.0)
```

---

## Next Steps

- ✅ Integrate performance metrics into runtime modules (audio_runtime, assistant_runtime, etc.)
- ✅ Build a web dashboard for real-time performance monitoring
- ✅ Add alerting for threshold violations
- ✅ Export metrics to external monitoring systems (Prometheus, DataDog, etc.)
