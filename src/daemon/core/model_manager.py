"""
Multi-Vendor GPU VRAM & Memory Manager for Voice Assistant Daemon
"""
import gc
import ctypes
import threading
import time


class ModelManager:
    """
    Manages lazy-loading, RAM/VRAM resource tracking,
    and idle auto-unloading across NVIDIA CUDA, AMD ROCm, Intel SYCL, and Vulkan.
    """

    def __init__(self, idle_timeout_sec: int = 300):
        self._lock = threading.Lock()
        self.idle_timeout_sec = idle_timeout_sec
        self.last_active_time = time.time()
        
        # References to loaded instances
        self.stt_instance = None
        self.llm_instance = None
        self.tts_instance = None
        self.embedding_instance = None

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
            if elapsed >= self.idle_timeout_sec:
                print(f"[ModelManager] Idle timeout reached ({elapsed:.1f}s >= {self.idle_timeout_sec}s). Purging VRAM & RAM...")
                self.purge_vram_and_ram()
                return True
        return False

    def purge_vram_and_ram(self, unload_llm: bool = True, unload_stt: bool = True, unload_tts: bool = True):
        """
        Reclaims memory buffers across CUDA, AMD ROCm/HIP, Vulkan, and Intel SYCL.
        """
        with self._lock:
            if unload_llm and self.llm_instance:
                print("[ModelManager] Unloading LLM instance...")
                del self.llm_instance
                self.llm_instance = None

            if unload_stt and self.stt_instance:
                print("[ModelManager] Unloading STT instance...")
                del self.stt_instance
                self.stt_instance = None

            if unload_tts and self.tts_instance:
                print("[ModelManager] Unloading TTS instance...")
                del self.tts_instance
                self.tts_instance = None

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
            print(f"[ModelManager] Warning trimming libc malloc: {e}")

        print("[ModelManager] VRAM and RAM reclamation completed.")
