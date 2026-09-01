"""
Thread-safe Async Model Downloader Service
"""
import sys
import threading
import json
import logging
from core.logger import ErrorCollector

logger = logging.getLogger("VoiceAssistant.Download")


class ModelDownloader:
    """
    Manages concurrent model downloads, progress reporting,
    and cancellation requests across providers.
    """
    def __init__(self, emit_progress_cb=None):
        self._lock = threading.Lock()
        self._downloading_models = {}
        self._cancel_requests = set()
        self.emit_progress_cb = emit_progress_cb

    def get_downloading_models_json(self) -> str:
        with self._lock:
            return json.dumps(self._downloading_models)

    def is_downloading(self, provider: str, model_name: str) -> bool:
        key = f"{provider}:{model_name}"
        with self._lock:
            return key in self._downloading_models

    def request_cancel(self, provider: str, model_name: str):
        key = f"{provider}:{model_name}"
        with self._lock:
            self._cancel_requests.add(key)

    def start_download(self, provider: str, model_name: str, download_func):
        """
        Runs download_func(progress_cb) in a separate daemon thread.
        """
        key = f"{provider}:{model_name}"
        
        with self._lock:
            self._cancel_requests.discard(key)
            self._downloading_models[key] = 0

        def _worker():
            def progress_cb(percent: int):
                with self._lock:
                    if key in self._cancel_requests:
                        raise InterruptedError(f"Download of {key} canceled by user.")
                    self._downloading_models[key] = percent

                if self.emit_progress_cb:
                    self.emit_progress_cb(provider, model_name, percent)

            try:
                if self.emit_progress_cb:
                    self.emit_progress_cb(provider, model_name, 0)
                download_func(progress_cb)
                with self._lock:
                    self._downloading_models[key] = 100
                if self.emit_progress_cb:
                    self.emit_progress_cb(provider, model_name, 100)
            except Exception as e:
                logger.error(f"Download error ({key}): {e}")
                if not isinstance(e, InterruptedError):
                    ErrorCollector.record_error(*sys.exc_info(), component="VoiceAssistant.Download")
            finally:
                with self._lock:
                    self._downloading_models.pop(key, None)
                    self._cancel_requests.discard(key)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        return t
