"""
Tests for Performance Metrics & Structured Context Tracing
"""
import time
import unittest
import logging
from unittest.mock import MagicMock, patch

import sys
import os
from pathlib import Path

# Setup paths
daemon_dir = Path(__file__).resolve().parent.parent / 'src' / 'daemon'
sys.path.insert(0, str(daemon_dir))

from core.performance_metrics import (
    OperationContext, PerformanceMetric, PerformanceMonitor,
    measure_latency, get_performance_monitor, get_current_operation_id
)
from core.logger import setup_logger, ContextTraceFilter, LOG_DIR


class TestPerformanceMetrics(unittest.TestCase):
    """Tests for performance metrics system."""

    def setUp(self):
        """Initialize test fixtures."""
        self.monitor = get_performance_monitor()
        self.monitor.clear_metrics()

    def test_operation_context_basic(self):
        """Test basic operation context creation and tracking."""
        with OperationContext("test_operation", component="Test") as ctx:
            self.assertIsNotNone(ctx.operation_id)
            self.assertIsNotNone(ctx.trace_id)
            self.assertEqual(ctx.operation_name, "test_operation")
            self.assertEqual(ctx.component, "Test")
            self.assertTrue(ctx.active)

        self.assertFalse(ctx.active)

    def test_operation_context_nesting(self):
        """Test nested operation contexts."""
        with OperationContext("outer", component="Outer") as outer:
            outer_id = outer.operation_id
            
            with OperationContext("inner", component="Inner") as inner:
                inner_id = inner.operation_id
                self.assertEqual(OperationContext.current(), inner)
                self.assertNotEqual(outer_id, inner_id)
            
            # Back to outer context
            self.assertEqual(OperationContext.current(), outer)

    def test_operation_context_metric_recording(self):
        """Test recording metrics within an operation."""
        with OperationContext("pipeline", component="E2E") as ctx:
            ctx.record_metric("stt_processing", 250.5, status="success", model="vosk")
            ctx.record_metric("llm_streaming", 450.2, status="success", tokens=42)
            
            self.assertEqual(len(ctx.metrics), 2)
            self.assertIn("stt_processing", ctx.metrics)
            self.assertIn("llm_streaming", ctx.metrics)
            
            stt_metric = ctx.metrics["stt_processing"]
            self.assertAlmostEqual(stt_metric.duration_ms, 250.5, places=1)
            self.assertEqual(stt_metric.status, "success")
            self.assertEqual(stt_metric.tags.get("model"), "vosk")

    def test_operation_context_error_handling(self):
        """Test operation context with errors."""
        with self.assertRaises(ValueError):
            with OperationContext("failing_op", component="Error") as ctx:
                raise ValueError("Test error")
        
        self.assertFalse(ctx.active)

    def test_operation_context_summary(self):
        """Test operation context summary generation."""
        with OperationContext("analysis", component="Audio") as ctx:
            ctx.record_metric("wav_decode", 100.0, status="success")
            ctx.record_metric("pcm_processing", 50.0, status="success")
            
            summary = ctx.get_summary()
            self.assertEqual(summary["operation_name"], "analysis")
            self.assertEqual(summary["component"], "Audio")
            self.assertIn("operation_id", summary)
            self.assertIn("trace_id", summary)
            self.assertEqual(len(summary["metrics"]), 2)

    def test_performance_metric_dataclass(self):
        """Test PerformanceMetric dataclass."""
        metric = PerformanceMetric(
            operation_name="test",
            start_time=time.time(),
            component="Unit"
        )
        
        time.sleep(0.01)
        metric.finalize(status="success")
        
        self.assertGreater(metric.duration_ms, 10)
        self.assertEqual(metric.status, "success")
        self.assertIsNone(metric.error_message)

    def test_performance_monitor_average_latency(self):
        """Test performance monitor average latency calculation."""
        monitor = PerformanceMonitor()
        
        # Record several successful metrics
        for _ in range(3):
            metric_dict = {
                "operation_name": "test_op",
                "duration_ms": 100.0,
                "status": "success"
            }
            monitor._metrics["test_op"].append(metric_dict)
        
        avg = monitor.get_average_latency("test_op")
        self.assertEqual(avg, 100.0)

    def test_performance_monitor_threshold_check(self):
        """Test performance monitor threshold checking."""
        monitor = PerformanceMonitor()
        
        # Within threshold
        result = monitor.check_threshold("stt_processing", 300.0)
        self.assertTrue(result)
        
        # Exceeds threshold
        result = monitor.check_threshold("stt_processing", 600.0)
        self.assertFalse(result)

    def test_performance_monitor_report(self):
        """Test performance monitor report generation."""
        monitor = PerformanceMonitor()
        
        # Record metrics
        for duration in [100.0, 150.0, 200.0]:
            monitor._metrics["latency_test"].append({
                "operation_name": "latency_test",
                "duration_ms": duration,
                "status": "success"
            })
        
        report = monitor.get_performance_report()
        
        self.assertIn("latency_test", report)
        stats = report["latency_test"]
        self.assertEqual(stats["count"], 3)
        self.assertEqual(stats["successful"], 3)
        self.assertEqual(stats["failed"], 0)
        self.assertEqual(stats["avg_ms"], 150.0)
        self.assertEqual(stats["min_ms"], 100.0)
        self.assertEqual(stats["max_ms"], 200.0)

    def test_measure_latency_decorator(self):
        """Test @measure_latency decorator."""
        with OperationContext("decorated", component="Test") as ctx:
            
            @measure_latency("decorated_func", component="Test")
            def slow_function():
                time.sleep(0.05)
                return "success"
            
            result = slow_function()
            self.assertEqual(result, "success")
            self.assertIn("decorated_func", ctx.metrics)
            
            metric = ctx.metrics["decorated_func"]
            self.assertGreaterEqual(metric.duration_ms, 50)

    def test_measure_latency_decorator_with_error(self):
        """Test @measure_latency decorator with error."""
        with OperationContext("error_context", component="Test") as ctx:
            
            @measure_latency("failing_func", component="Test")
            def failing_function():
                raise RuntimeError("Test failure")
            
            with self.assertRaises(RuntimeError):
                failing_function()
            
            self.assertIn("failing_func", ctx.metrics)
            metric = ctx.metrics["failing_func"]
            self.assertEqual(metric.status, "error")
            self.assertIn("Test failure", metric.error_message)

    def test_context_trace_filter(self):
        """Test ContextTraceFilter adds operation_id to log records."""
        filter_obj = ContextTraceFilter()
        
        # Create a log record
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="test message", args=(), exc_info=None
        )
        
        # Without context
        result = filter_obj.filter(record)
        self.assertTrue(result)
        self.assertEqual(record.operation_id, "no-ctx")
        
        # With context
        with OperationContext("filter_test", component="Log") as ctx:
            record2 = logging.LogRecord(
                name="test", level=logging.INFO, pathname="", lineno=0,
                msg="test message", args=(), exc_info=None
            )
            result = filter_obj.filter(record2)
            self.assertTrue(result)
            self.assertEqual(record2.operation_id, ctx.operation_id)

    def test_get_current_operation_id(self):
        """Test get_current_operation_id helper function."""
        # Without context
        op_id = get_current_operation_id()
        self.assertEqual(op_id, "no-context")
        
        # With context
        with OperationContext("context_test") as ctx:
            op_id = get_current_operation_id()
            self.assertEqual(op_id, ctx.operation_id)

    def test_full_e2e_pipeline_metrics(self):
        """Test a complete E2E pipeline with multiple operations."""
        monitor = PerformanceMonitor()
        
        # Simulate complete pipeline
        with OperationContext("e2e_pipeline", component="Pipeline") as main_ctx:
            main_ctx.record_metric("audio_capture", 100.0, status="success")
            main_ctx.record_metric("stt_processing", 250.0, status="success")
            main_ctx.record_metric("llm_first_token", 800.0, status="success")
            main_ctx.record_metric("tts_synthesis", 150.0, status="success")
            
            summary = main_ctx.get_summary()
            monitor.record_operation(main_ctx)
        
        # Verify metrics were recorded
        self.assertEqual(len(monitor._metrics), 4)
        
        # Verify performance report
        report = monitor.get_performance_report()
        self.assertAlmostEqual(report["audio_capture"]["avg_ms"], 100.0, places=0)
        self.assertAlmostEqual(report["stt_processing"]["avg_ms"], 250.0, places=0)

    def test_performance_metrics_thread_safety(self):
        """Test performance metrics under concurrent access."""
        import threading
        
        monitor = PerformanceMonitor()
        errors = []
        
        def record_metrics():
            try:
                for i in range(10):
                    metric_dict = {
                        "operation_name": f"thread_metric",
                        "duration_ms": 100.0 + i,
                        "status": "success"
                    }
                    monitor._metrics["thread_metric"].append(metric_dict)
            except Exception as e:
                errors.append(e)
        
        threads = [threading.Thread(target=record_metrics) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        self.assertEqual(len(errors), 0)
        self.assertEqual(len(monitor._metrics["thread_metric"]), 50)

    def test_performance_monitor_export_metrics(self):
        """Test exporting metrics to JSON file."""
        import tempfile
        import json
        
        monitor = PerformanceMonitor()
        monitor._metrics["test_op"] = [
            {"operation_name": "test_op", "duration_ms": 100.0, "status": "success"}
        ]
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
            temp_file = f.name
        
        try:
            monitor.export_metrics_json(temp_file)
            
            # Verify file was created and contains data
            self.assertTrue(os.path.exists(temp_file))
            with open(temp_file, 'r') as f:
                data = json.load(f)
            self.assertIn("test_op", data)
        finally:
            if os.path.exists(temp_file):
                os.remove(temp_file)


if __name__ == '__main__':
    unittest.main(verbosity=2)
