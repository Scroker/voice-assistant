"""
Performance Metrics & Structured Context Tracing for Voice Assistant Daemon.

Provides:
- Operation context tracking (operation_id, trace_id for distributed tracing)
- Automatic latency measurement with decorators
- Metric aggregation and reporting
- Performance thresholds and alerts
"""
import time
import logging
import threading
import uuid
from typing import Dict, Any, Optional, Callable
from dataclasses import dataclass, field, asdict
from datetime import datetime
from collections import defaultdict
import json

logger = logging.getLogger("VoiceAssistant.Performance")


@dataclass
class PerformanceMetric:
    """Single performance measurement."""
    operation_name: str
    start_time: float
    end_time: float = 0.0
    duration_ms: float = 0.0
    operation_id: str = ""
    trace_id: str = ""
    component: str = ""
    status: str = "pending"  # pending, success, error
    error_message: Optional[str] = None
    tags: Dict[str, Any] = field(default_factory=dict)

    def finalize(self, status: str = "success", error: Optional[str] = None):
        """Mark metric as complete."""
        self.end_time = time.time()
        self.duration_ms = (self.end_time - self.start_time) * 1000
        self.status = status
        self.error_message = error

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


class OperationContext:
    """
    Thread-safe context for correlating related operations.
    Used for tracing flows: Audio Input → STT → LLM → TTS → Output.
    """
    _context_stack = threading.local()

    def __init__(self, operation_name: str, component: str = "", tags: Optional[Dict[str, Any]] = None):
        self.operation_id = str(uuid.uuid4())[:8]
        self.trace_id = str(uuid.uuid4())[:12]
        self.operation_name = operation_name
        self.component = component
        self.tags = tags or {}
        self.start_time = time.time()
        self.metrics: Dict[str, PerformanceMetric] = {}
        self.active = True

    def __enter__(self):
        """Enter context manager."""
        if not hasattr(self._context_stack, "stack"):
            self._context_stack.stack = []
        self._context_stack.stack.append(self)
        logger.debug(f"[{self.operation_id}] Operazione iniziata: {self.operation_name} "
                    f"({self.component})")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context manager."""
        if hasattr(self._context_stack, "stack"):
            self._context_stack.stack.pop()
        duration = (time.time() - self.start_time) * 1000
        status = "error" if exc_type else "success"
        logger.info(f"[{self.operation_id}] Operazione completata: {self.operation_name} "
                   f"({status}) in {duration:.2f}ms")
        self.active = False

    @classmethod
    def current(cls) -> Optional["OperationContext"]:
        """Get the current operation context."""
        if hasattr(cls._context_stack, "stack") and cls._context_stack.stack:
            return cls._context_stack.stack[-1]
        return None

    def record_metric(self, metric_name: str, duration_ms: float,
                     status: str = "success", error: Optional[str] = None,
                     **tags):
        """Record a sub-operation metric within this context."""
        metric = PerformanceMetric(
            operation_name=metric_name,
            start_time=time.time() - duration_ms / 1000,
            component=self.component,
            operation_id=self.operation_id,
            trace_id=self.trace_id,
            status=status,
            error_message=error,
            tags=tags
        )
        metric.finalize(status, error)
        self.metrics[metric_name] = metric
        
        log_level = logging.WARNING if status == "error" else logging.DEBUG
        logger.log(log_level,
                  f"[{self.operation_id}] Metrica: {metric_name} = {duration_ms:.2f}ms "
                  f"({status})")

    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all metrics in this operation."""
        total_duration = (time.time() - self.start_time) * 1000 if self.active else \
                        (self.metrics.get("_end_time", time.time()) - self.start_time) * 1000
        
        return {
            "operation_id": self.operation_id,
            "trace_id": self.trace_id,
            "operation_name": self.operation_name,
            "component": self.component,
            "total_duration_ms": round(total_duration, 2),
            "metrics": {name: metric.to_dict() for name, metric in self.metrics.items()},
            "tags": self.tags
        }


class PerformanceMonitor:
    """
    Centralized performance monitoring and metrics aggregation.
    Tracks latency thresholds and generates performance reports.
    """

    # Performance thresholds (in milliseconds) - alerts if exceeded
    THRESHOLDS = {
        "stt_processing": 500,      # STT should complete in < 500ms
        "llm_first_token": 1000,    # First LLM token should arrive in < 1s
        "llm_token_generation": 100,  # Subsequent tokens < 100ms
        "tts_synthesis": 300,       # TTS synthesis < 300ms
        "total_pipeline": 3000,     # Total end-to-end < 3s
        "wakeword_detection": 200,  # Wakeword < 200ms
        "audio_capture": 100,       # Audio capture chunk < 100ms
    }

    def __init__(self):
        self._metrics: Dict[str, list] = defaultdict(list)
        self._lock = threading.Lock()
        self._aggregation_window = 60  # seconds

    def record_operation(self, context: OperationContext):
        """Record a completed operation context."""
        with self._lock:
            for metric_name, metric in context.metrics.items():
                self._metrics[metric_name].append(metric.to_dict())

    def get_average_latency(self, operation_name: str) -> float:
        """Get average latency for an operation (in milliseconds)."""
        with self._lock:
            if operation_name not in self._metrics:
                return 0.0
            latencies = [m.get("duration_ms", 0) for m in self._metrics[operation_name]
                        if m.get("status") == "success"]
            return sum(latencies) / len(latencies) if latencies else 0.0

    def check_threshold(self, operation_name: str, duration_ms: float) -> bool:
        """
        Check if operation exceeded performance threshold.
        Returns True if within threshold, False if exceeded.
        """
        threshold = self.THRESHOLDS.get(operation_name, float('inf'))
        if duration_ms > threshold:
            logger.warning(
                f"Performance alert: {operation_name} took {duration_ms:.2f}ms "
                f"(threshold: {threshold}ms)"
            )
            return False
        return True

    def get_performance_report(self) -> Dict[str, Any]:
        """Generate comprehensive performance report."""
        with self._lock:
            report = {}
            for op_name in self._metrics:
                metrics = self._metrics[op_name]
                if not metrics:
                    continue

                durations = [m.get("duration_ms", 0) for m in metrics if m.get("status") == "success"]
                if not durations:
                    continue

                report[op_name] = {
                    "count": len(metrics),
                    "successful": len(durations),
                    "failed": len(metrics) - len(durations),
                    "avg_ms": round(sum(durations) / len(durations), 2),
                    "min_ms": round(min(durations), 2),
                    "max_ms": round(max(durations), 2),
                    "p95_ms": round(sorted(durations)[int(len(durations) * 0.95)] if durations else 0, 2),
                    "threshold_ms": self.THRESHOLDS.get(op_name, None),
                }

        return report

    def export_metrics_json(self, filepath: str):
        """Export metrics to JSON file for analysis."""
        report = self.get_performance_report()
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, ensure_ascii=False, default=str)
            logger.info(f"Metriche di performance esportate: {filepath}")
        except Exception as e:
            logger.error(f"Errore esportazione metriche: {e}")

    def clear_metrics(self):
        """Clear all recorded metrics."""
        with self._lock:
            self._metrics.clear()


# Global performance monitor instance
_performance_monitor = PerformanceMonitor()


def measure_latency(operation_name: str, component: str = ""):
    """
    Decorator to automatically measure function execution time.
    
    Usage:
        @measure_latency("stt_processing", component="STT")
        def process_audio(audio_data):
            ...
    """
    def decorator(func: Callable):
        def wrapper(*args, **kwargs):
            ctx = OperationContext.current()
            start = time.time()
            try:
                result = func(*args, **kwargs)
                duration_ms = (time.time() - start) * 1000
                _performance_monitor.check_threshold(operation_name, duration_ms)
                if ctx:
                    ctx.record_metric(operation_name, duration_ms, status="success")
                logger.debug(f"{operation_name} completed in {duration_ms:.2f}ms")
                return result
            except Exception as e:
                duration_ms = (time.time() - start) * 1000
                if ctx:
                    ctx.record_metric(operation_name, duration_ms, status="error",
                                    error=str(e))
                logger.error(f"{operation_name} failed after {duration_ms:.2f}ms: {e}")
                raise

        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper
    return decorator


def get_performance_monitor() -> PerformanceMonitor:
    """Get the global performance monitor instance."""
    return _performance_monitor


def get_current_operation_id() -> str:
    """Get the current operation ID for logging."""
    ctx = OperationContext.current()
    return ctx.operation_id if ctx else "no-context"
