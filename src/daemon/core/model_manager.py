"""
Multi-Vendor GPU VRAM & Memory Manager for Voice Assistant Daemon
"""
import gc
import ctypes
import threading
import time
import logging

logger = logging.getLogger("VoiceAssistant.ModelManager")


class ModelManager:
    """
    Manages lazy-loading, RAM/VRAM resource tracking,
    and idle auto-unloading across NVIDIA CUDA, AMD ROCm, Intel SYCL, and Vulkan.
    """

    def __init__(self, idle_timeout_sec: int = 300, idle_timeouts=None):
        self._lock = threading.RLock()
        self.idle_timeout_sec = idle_timeout_sec
        self.idle_timeouts = {"stt": idle_timeout_sec, "llm": idle_timeout_sec, "tts": idle_timeout_sec}
        self.set_idle_timeouts(idle_timeouts or {})
        self.last_active_time = time.time()

        # References to loaded instances
        self.stt_instance = None
        self.llm_instance = None
        self.tts_instance = None
        self.embedding_instance = None
        self._unload_callbacks = {}

    def register_instance(self, kind: str, instance, unload_callback=None):
        """Register a loaded model and the callback that releases its owning service."""
        attribute = f"{kind}_instance"
        if not hasattr(self, attribute):
            raise ValueError(f"Unsupported model kind: {kind}")

        with self._lock:
            setattr(self, attribute, instance)
            if unload_callback:
                self._unload_callbacks[kind] = unload_callback
            else:
                self._unload_callbacks.pop(kind, None)
            self.last_active_time = time.time()

    def set_idle_timeouts(self, idle_timeouts):
        """Set per-model idle timeouts; a non-positive value uses the global timeout."""
        with self._lock:
            for kind in ("stt", "llm", "tts"):
                value = int(idle_timeouts.get(kind, 0))
                self.idle_timeouts[kind] = value if value > 0 else self.idle_timeout_sec

    def update_active_timestamp(self):
        """Reset the inactivity timer to current timestamp."""
        with self._lock:
            self.last_active_time = time.time()

    def check_idle_and_purge(self) -> bool:
        """
        Check if idle timeout has expired and purge VRAM/RAM if needed.
        Returns True if models were purged.
        """
        with self._lock:
            elapsed = time.time() - self.last_active_time
            unload_kinds = {
                kind: elapsed >= self.idle_timeouts[kind]
                for kind in ("stt", "llm", "tts")
            }

        if not any(unload_kinds.values()):
            return False

        logger.info(f"Idle timeout reached after {elapsed:.1f}s. Purging inactive models...")
        return self.purge_vram_and_ram(
            unload_llm=unload_kinds["llm"],
            unload_stt=unload_kinds["stt"],
            unload_tts=unload_kinds["tts"],
        )

    def get_resource_metrics(self):
        """Return process memory, accelerator memory, and loaded-model status."""
        metrics = {
            "rss_bytes": 0,
            "vms_bytes": 0,
            "gpu_allocated_bytes": 0,
            "gpu_reserved_bytes": 0,
            "loaded_models": {},
            "idle_timeouts": {},
        }
        try:
            with open("/proc/self/status", encoding="utf-8") as status_file:
                values = dict(
                    line.split(":", 1) for line in status_file if ":" in line
                )
            metrics["rss_bytes"] = int(values.get("VmRSS", "0").split()[0]) * 1024
            metrics["vms_bytes"] = int(values.get("VmSize", "0").split()[0]) * 1024
        except (OSError, ValueError, IndexError):
            pass

        try:
            import torch
            if hasattr(torch, "cuda") and torch.cuda.is_available():
                metrics["gpu_allocated_bytes"] = torch.cuda.memory_allocated()
                metrics["gpu_reserved_bytes"] = torch.cuda.memory_reserved()
            elif hasattr(torch, "xpu") and torch.xpu.is_available():
                metrics["gpu_allocated_bytes"] = torch.xpu.memory_allocated()
                metrics["gpu_reserved_bytes"] = torch.xpu.memory_reserved()
        except (ImportError, AttributeError):
            pass

        with self._lock:
            metrics["loaded_models"] = {
                kind: getattr(self, f"{kind}_instance") is not None
                for kind in ("stt", "llm", "tts", "embedding")
            }
            metrics["idle_timeouts"] = dict(self.idle_timeouts)
        return metrics

    def purge_vram_and_ram(self, unload_llm: bool = True, unload_stt: bool = True, unload_tts: bool = True) -> bool:
        """
        Reclaims memory buffers across CUDA, AMD ROCm/HIP, Vulkan, and Intel SYCL.
        """
        unload_kinds = {
            "llm": unload_llm,
            "stt": unload_stt,
            "tts": unload_tts,
        }
        callbacks = []

        with self._lock:
            for kind, should_unload in unload_kinds.items():
                attribute = f"{kind}_instance"
                if should_unload and getattr(self, attribute):
                    logger.info(f"Unloading {kind.upper()} instance...")
                    callback = self._unload_callbacks.pop(kind, None)
                    if callback:
                        callbacks.append((kind, callback))
                    setattr(self, attribute, None)
            self.last_active_time = time.time()

        for kind, callback in callbacks:
            try:
                callback()
            except Exception:
                logger.exception(f"Error unloading {kind.upper()} instance")

        # 1. Force Python Garbage Collection
        gc.collect()

        # 2. NVIDIA CUDA & AMD ROCm/HIP purging (PyTorch uses cuda namespace for ROCm)
        try:
            import torch
            if hasattr(torch, 'cuda') and torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.ipc_collect()
            # Intel Arc / SYCL acceleration purging
            if hasattr(torch, 'xpu') and torch.xpu.is_available():
                torch.xpu.empty_cache()
        except ImportError:
            pass

        # 3. Trim glibc malloc heap on Linux
        try:
            libc = ctypes.CDLL("libc.so.6")
            libc.malloc_trim(0)
        except Exception as e:
            logger.warning(f"Warning trimming libc malloc: {e}")

        logger.info("VRAM and RAM reclamation completed.")
        return bool(callbacks)
