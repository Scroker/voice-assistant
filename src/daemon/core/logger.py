"""
Structured Logging & Error Collection System for Voice Assistant Daemon
"""
import os
import sys
import json
import trace
import logging
import traceback
import threading
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import List, Dict, Any, Optional

LOG_DIR = os.path.expanduser("~/.local/share/voice-assistant/logs")
ERROR_REPORTS_DIR = os.path.join(LOG_DIR, "error_reports")

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(ERROR_REPORTS_DIR, exist_ok=True)

MAIN_LOG_FILE = os.path.join(LOG_DIR, "voice-assistant.log")


class ErrorCollector:
    """
    Collects, logs, and persists detailed error reports for debugging and user feedback.
    """
    _context: Dict[str, Any] = {}

    @classmethod
    def update_context(cls, key: str, value: Any):
        cls._context[key] = value

    @classmethod
    def set_context_dict(cls, ctx: Dict[str, Any]):
        cls._context.update(ctx)

    @classmethod
    def record_error(cls, exc_type, exc_value, exc_traceback, extra_info: Optional[Dict[str, Any]] = None) -> str:
        timestamp = datetime.now().isoformat()
        tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))

        report_data = {
            "timestamp": timestamp,
            "error_type": exc_type.__name__ if exc_type else "UnknownError",
            "message": str(exc_value),
            "traceback": tb_str,
            "context": dict(cls._context),
            "extra": extra_info or {}
        }

        # Genera nome file unico basato sul timestamp
        filename = f"report_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.json"
        filepath = os.path.join(ERROR_REPORTS_DIR, filename)

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(report_data, f, indent=2, ensure_ascii=False)
            logging.getLogger("VoiceAssistant.ErrorCollector").error(
                f"Report di errore salvato in: {filepath}\n{tb_str}"
            )
        except Exception as e:
            logging.getLogger("VoiceAssistant.ErrorCollector").critical(f"Impossibile salvare report di errore: {e}")

        return filepath

    @classmethod
    def list_reports(cls, limit: int = 10) -> List[Dict[str, Any]]:
        """Restituisce gli ultimi report di errore memorizzati."""
        if not os.path.exists(ERROR_REPORTS_DIR):
            return []

        reports = []
        files = sorted(os.listdir(ERROR_REPORTS_DIR), reverse=True)
        for fname in files[:limit]:
            if fname.endswith(".json"):
                fpath = os.path.join(ERROR_REPORTS_DIR, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        reports.append(json.load(f))
                except Exception:
                    pass
        return reports


def setup_logger(name: str = "VoiceAssistant") -> logging.Logger:
    """
    Configura il sistema di logging con output sia su file rotante sia su stdout.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        formatter = logging.Formatter(
            '[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

        # File Handler (5 MB max per file, 3 copie di backup)
        file_handler = RotatingFileHandler(
            MAIN_LOG_FILE,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding='utf-8'
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.INFO)
        logger.addHandler(file_handler)

        # Console Handler (per journalctl / stderr)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        console_handler.setLevel(logging.INFO)
        logger.addHandler(console_handler)

    return logger


def install_global_exception_hooks():
    """
    Installa gli hook globali per intercettare eccezioni non gestite nel main thread e nei thread secondari.
    """
    def sys_excepthook(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        ErrorCollector.record_error(exc_type, exc_value, exc_traceback, {"thread": "main"})

    def threading_excepthook(args):
        ErrorCollector.record_error(
            args.exc_type,
            args.exc_value,
            args.exc_traceback,
            {"thread": args.thread.name if args.thread else "unknown"}
        )

    sys.excepthook = sys_excepthook
    threading.excepthook = threading_excepthook
