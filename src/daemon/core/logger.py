"""
Structured Logging, Error Collection & Diagnostic Bundle System for Voice Assistant Daemon.

Provides:
- Hierarchical logger setup with rotating file + console handlers
- ErrorCollector for crash context capture and JSON report persistence
- EnvironmentSnapshot for system/environment info collection
- DiagnosticBundler to create sanitized .tar.gz bundles for GitHub issues
- Structured context tracing (operation_id, trace_id) for distributed debugging
"""
import os
import re
import sys
import json
import time
import shutil
import logging
import platform
import tarfile
import traceback
import threading
import subprocess
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import List, Dict, Any, Optional

LOG_DIR = os.path.expanduser("~/.local/share/voice-assistant/logs")
ERROR_REPORTS_DIR = os.path.join(LOG_DIR, "error_reports")
MAIN_LOG_FILE = os.path.join(LOG_DIR, "voice-assistant.log")
BUNDLE_DIR = os.path.join(LOG_DIR, "bundles")

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(ERROR_REPORTS_DIR, exist_ok=True)
os.makedirs(BUNDLE_DIR, exist_ok=True)

# Regex per sanitizzare i percorsi utente nei report
_HOME_RE = re.compile(re.escape(os.path.expanduser("~")))


def _sanitize_text(text: str) -> str:
    """Sostituisce il percorso home dell'utente con ~ per privacy."""
    return _HOME_RE.sub("~", text)


class ContextTraceFilter(logging.Filter):
    """
    Adds operation_id and trace_id to log records for structured tracing.
    These values come from the current OperationContext.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Add context information to log record."""
        # Lazily import to avoid circular dependency
        try:
            from core.performance_metrics import OperationContext
            ctx = OperationContext.current()
            if ctx:
                record.operation_id = ctx.operation_id
                record.trace_id = ctx.trace_id
            else:
                record.operation_id = "no-ctx"
                record.trace_id = ""
        except ImportError:
            record.operation_id = "no-ctx"
            record.trace_id = ""
        return True


class ErrorCollector:
    """
    Collects, logs, and persists detailed error reports with crash context.
    Each report is saved as a JSON file in ERROR_REPORTS_DIR.
    """
    _context: Dict[str, Any] = {}
    _lock = threading.Lock()

    @classmethod
    def update_context(cls, key: str, value: Any):
        with cls._lock:
            cls._context[key] = value

    @classmethod
    def set_context_dict(cls, ctx: Dict[str, Any]):
        with cls._lock:
            cls._context.update(ctx)

    @classmethod
    def record_error(cls, exc_type, exc_value, exc_traceback,
                     extra_info: Optional[Dict[str, Any]] = None,
                     component: str = "unknown",
                     severity: str = "ERROR") -> str:
        """Record an error report to disk and return the file path."""
        timestamp = datetime.now().isoformat()
        tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))

        with cls._lock:
            ctx_copy = dict(cls._context)

        report_data = {
            "timestamp": timestamp,
            "error_type": exc_type.__name__ if exc_type else "UnknownError",
            "message": str(exc_value),
            "severity": severity,
            "component": component,
            "traceback": _sanitize_text(tb_str),
            "context": {k: _sanitize_text(str(v)) if isinstance(v, str) else v
                        for k, v in ctx_copy.items()},
            "extra": extra_info or {}
        }

        filename = f"report_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.json"
        filepath = os.path.join(ERROR_REPORTS_DIR, filename)

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(report_data, f, indent=2, ensure_ascii=False, default=str)
            logging.getLogger("VoiceAssistant.ErrorCollector").error(
                f"Report di errore salvato in: {filepath}"
            )
        except Exception as e:
            logging.getLogger("VoiceAssistant.ErrorCollector").critical(
                f"Impossibile salvare report di errore: {e}"
            )

        return filepath

    @classmethod
    def list_reports(cls, limit: int = 20) -> List[Dict[str, Any]]:
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

    @classmethod
    def report_count(cls) -> int:
        """Conta il numero di report di errore presenti su disco."""
        if not os.path.exists(ERROR_REPORTS_DIR):
            return 0
        return len([f for f in os.listdir(ERROR_REPORTS_DIR) if f.endswith(".json")])

    @classmethod
    def clear_reports(cls):
        """Rimuove tutti i report di errore dal disco."""
        if not os.path.exists(ERROR_REPORTS_DIR):
            return
        for f in os.listdir(ERROR_REPORTS_DIR):
            fpath = os.path.join(ERROR_REPORTS_DIR, f)
            if os.path.isfile(fpath):
                try:
                    os.remove(fpath)
                except Exception:
                    pass


class EnvironmentSnapshot:
    """
    Raccoglie informazioni sull'ambiente di sistema per il debug:
    OS, kernel, GNOME, GPU, Python, pacchetti pip, device audio, modelli installati, GSettings.
    """

    @staticmethod
    def collect(settings=None, daemon_start_time: Optional[float] = None) -> Dict[str, Any]:
        snapshot = {
            "timestamp": datetime.now().isoformat(),
            "os": EnvironmentSnapshot._get_os_info(),
            "kernel": platform.release(),
            "arch": platform.machine(),
            "desktop": EnvironmentSnapshot._get_gnome_version(),
            "session_type": os.environ.get("XDG_SESSION_TYPE", "unknown"),
            "cpu": EnvironmentSnapshot._get_cpu_info(),
            "ram_total_mb": EnvironmentSnapshot._get_ram_mb(),
            "python_version": platform.python_version(),
            "venv_packages": EnvironmentSnapshot._get_pip_packages(),
            "pipewire_version": EnvironmentSnapshot._get_pipewire_version(),
            "audio_devices": EnvironmentSnapshot._get_audio_devices(),
            "installed_models": EnvironmentSnapshot._get_installed_models(),
        }
        if settings:
            snapshot["gsettings_dump"] = EnvironmentSnapshot._dump_gsettings(settings)
        if daemon_start_time:
            snapshot["daemon_uptime_seconds"] = round(time.time() - daemon_start_time, 1)
        return snapshot

    @staticmethod
    def _get_os_info() -> str:
        try:
            if os.path.exists("/etc/os-release"):
                with open("/etc/os-release") as f:
                    for line in f:
                        if line.startswith("PRETTY_NAME="):
                            return line.split("=", 1)[1].strip().strip('"')
        except Exception:
            pass
        return platform.platform()

    @staticmethod
    def _get_gnome_version() -> str:
        try:
            result = subprocess.run(
                ["gnome-shell", "--version"], capture_output=True, text=True, timeout=3
            )
            return result.stdout.strip()
        except Exception:
            return "unknown"

    @staticmethod
    def _get_cpu_info() -> str:
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except Exception:
            pass
        return platform.processor() or "unknown"

    @staticmethod
    def _get_ram_mb() -> int:
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) // 1024
        except Exception:
            pass
        return 0

    @staticmethod
    def _get_pip_packages() -> Dict[str, str]:
        target_packages = [
            "vosk", "sounddevice", "llama-cpp-python", "piper-tts",
            "faster-whisper", "dasbus", "huggingface-hub", "numpy",
            "notify2", "torch"
        ]
        packages = {}
        try:
            import importlib.metadata
            for pkg in target_packages:
                try:
                    packages[pkg] = importlib.metadata.version(pkg)
                except importlib.metadata.PackageNotFoundError:
                    packages[pkg] = "not installed"
        except Exception:
            pass
        return packages

    @staticmethod
    def _get_pipewire_version() -> str:
        try:
            result = subprocess.run(
                ["pipewire", "--version"], capture_output=True, text=True, timeout=3
            )
            lines = result.stdout.strip().splitlines()
            return lines[-1] if lines else "unknown"
        except Exception:
            return "unknown"

    @staticmethod
    def _get_audio_devices() -> List[str]:
        try:
            import sounddevice as sd
            devices = sd.query_devices()
            input_devices = []
            for d in devices:
                if d.get("max_input_channels", 0) > 0:
                    input_devices.append(d.get("name", "unknown"))
            return input_devices
        except Exception:
            return []

    @staticmethod
    def _get_installed_models() -> Dict[str, List[str]]:
        base_dir = os.path.expanduser("~/.local/share/voice-assistant/models")
        result = {"stt": [], "llm": [], "tts": []}
        try:
            if os.path.isdir(base_dir):
                for entry in os.listdir(base_dir):
                    full = os.path.join(base_dir, entry)
                    if entry.startswith("vosk-") or entry.startswith("whisper-"):
                        result["stt"].append(entry)
                    elif entry == "llm" and os.path.isdir(full):
                        result["llm"] = os.listdir(full)
                    elif entry == "tts" and os.path.isdir(full):
                        result["tts"] = os.listdir(full)
                    elif entry.endswith(".gguf"):
                        result["llm"].append(entry)
        except Exception:
            pass
        return result

    @staticmethod
    def _dump_gsettings(settings) -> Dict[str, Any]:
        """Dump di tutte le chiavi GSettings (valori sensibili mascherati)."""
        dump = {}
        try:
            schema = settings.get_property("settings-schema")
            if schema:
                for key in schema.list_keys():
                    try:
                        val = settings.get_value(key)
                        unpacked = val.unpack()
                        # Mascherare eventuali chiavi API/token
                        if "token" in key.lower() or "api-key" in key.lower():
                            dump[key] = "[REDACTED]"
                        else:
                            dump[key] = unpacked
                    except Exception:
                        dump[key] = "[error reading]"
        except Exception:
            dump["_error"] = "Failed to enumerate GSettings keys"
        return dump


class DiagnosticBundler:
    """
    Genera un archivio .tar.gz contenente tutto il necessario per una segnalazione bug:
    - environment.json
    - voice-assistant.log (ultimi 500KB)
    - error_reports/*.json
    - journalctl output
    """

    @staticmethod
    def generate(settings=None, state: str = "unknown",
                 daemon_start_time: Optional[float] = None) -> str:
        """Genera il bundle diagnostico e ritorna il percorso del file .tar.gz."""
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        bundle_name = f"voice-assistant-diagnostic-{timestamp_str}"
        bundle_path = os.path.join(BUNDLE_DIR, f"{bundle_name}.tar.gz")

        # Crea directory temporanea per il bundle
        tmp_dir = os.path.join(BUNDLE_DIR, bundle_name)
        os.makedirs(tmp_dir, exist_ok=True)

        try:
            # 1. Environment snapshot
            env_data = EnvironmentSnapshot.collect(
                settings=settings, daemon_start_time=daemon_start_time
            )
            env_data["current_state"] = state
            with open(os.path.join(tmp_dir, "environment.json"), "w", encoding="utf-8") as f:
                json.dump(env_data, f, indent=2, ensure_ascii=False, default=str)

            # 2. Log file (ultimi 500KB)
            if os.path.exists(MAIN_LOG_FILE):
                log_size = os.path.getsize(MAIN_LOG_FILE)
                with open(MAIN_LOG_FILE, "r", encoding="utf-8", errors="replace") as src:
                    if log_size > 512 * 1024:
                        src.seek(log_size - 512 * 1024)
                        src.readline()  # Skip partial line
                    content = _sanitize_text(src.read())
                with open(os.path.join(tmp_dir, "voice-assistant.log"), "w", encoding="utf-8") as dst:
                    dst.write(content)

            # 3. Error reports
            if os.path.exists(ERROR_REPORTS_DIR):
                reports_dst = os.path.join(tmp_dir, "error_reports")
                os.makedirs(reports_dst, exist_ok=True)
                for fname in sorted(os.listdir(ERROR_REPORTS_DIR)):
                    if fname.endswith(".json"):
                        src_path = os.path.join(ERROR_REPORTS_DIR, fname)
                        shutil.copy2(src_path, os.path.join(reports_dst, fname))

            # 4. Journalctl daemon output (ultime 200 righe)
            try:
                result = subprocess.run(
                    ["journalctl", "--user", "-u", "voice-assistant", "-n", "200", "--no-pager"],
                    capture_output=True, text=True, timeout=5
                )
                journalctl_content = _sanitize_text(result.stdout)
                with open(os.path.join(tmp_dir, "journalctl.log"), "w", encoding="utf-8") as f:
                    f.write(journalctl_content)
            except Exception:
                pass

            # 5. GNOME Shell extension errors (filtrate)
            try:
                result = subprocess.run(
                    ["journalctl", "-b", "--user", "-n", "100", "--no-pager",
                     "-g", "VoiceAssistant|voice-assistant"],
                    capture_output=True, text=True, timeout=5
                )
                shell_content = _sanitize_text(result.stdout)
                with open(os.path.join(tmp_dir, "gnome-shell-errors.log"), "w", encoding="utf-8") as f:
                    f.write(shell_content)
            except Exception:
                pass

            # Crea l'archivio .tar.gz
            with tarfile.open(bundle_path, "w:gz") as tar:
                tar.add(tmp_dir, arcname=bundle_name)

        finally:
            # Cleanup directory temporanea
            shutil.rmtree(tmp_dir, ignore_errors=True)

        logging.getLogger("VoiceAssistant").info(
            f"Bundle diagnostico generato: {bundle_path}"
        )
        return bundle_path

    @staticmethod
    def cleanup_old_bundles(max_bundles: int = 5):
        """Rimuove i bundle più vecchi mantenendo solo gli ultimi max_bundles."""
        if not os.path.exists(BUNDLE_DIR):
            return
        bundles = sorted(
            [f for f in os.listdir(BUNDLE_DIR) if f.endswith(".tar.gz")],
            reverse=True
        )
        for old in bundles[max_bundles:]:
            try:
                os.remove(os.path.join(BUNDLE_DIR, old))
            except Exception:
                pass


def setup_logger(name: str = "VoiceAssistant", enable_context_tracing: bool = True) -> logging.Logger:
    """
    Configura il sistema di logging gerarchico con output su file rotante e stdout.
    Tutti i sotto-logger (VoiceAssistant.Audio, .LLM, ecc.) propagano al root.
    
    Args:
        name: Nome del logger (default: "VoiceAssistant")
        enable_context_tracing: Se True, aggiunge operation_id e trace_id ai log
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    if not logger.handlers:
        # Formatter con context tracing (opzionale)
        if enable_context_tracing:
            file_formatter = logging.Formatter(
                '[%(asctime)s] [%(levelname)s] [%(operation_id)s] [%(name)s]: %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            console_formatter = logging.Formatter(
                '[%(levelname)s] [%(operation_id)s] [%(name)s]: %(message)s'
            )
            # Aggiungi il context filter a tutti gli handler
            context_filter = ContextTraceFilter()
        else:
            file_formatter = logging.Formatter(
                '[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            console_formatter = logging.Formatter(
                '[%(levelname)s] [%(name)s]: %(message)s'
            )
            context_filter = None

        # File Handler (5 MB max per file, 3 copie di backup)
        file_handler = RotatingFileHandler(
            MAIN_LOG_FILE,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding='utf-8'
        )
        file_handler.setFormatter(file_formatter)
        file_handler.setLevel(logging.DEBUG)
        if context_filter:
            file_handler.addFilter(context_filter)
        logger.addHandler(file_handler)

        # Console Handler (per journalctl / stdout)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(console_formatter)
        console_handler.setLevel(logging.INFO)
        if context_filter:
            console_handler.addFilter(context_filter)
        logger.addHandler(console_handler)

    return logger


_error_submitted_callback = None


def set_error_submitted_callback(callback) -> None:
    """
    Registra una callback invocata dopo ogni record_error() negli hook globali.
    Firma attesa: callback(exc_type, exc_value, exc_traceback, component=str)
    Usato da BugReporter per inviare i report a Bugzilla.
    """
    global _error_submitted_callback
    _error_submitted_callback = callback


def install_global_exception_hooks():
    """
    Installa gli hook globali per intercettare eccezioni non gestite
    nel main thread e nei thread secondari.
    """
    def sys_excepthook(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        ErrorCollector.record_error(
            exc_type, exc_value, exc_traceback,
            extra_info={"thread": "main"},
            component="sys.excepthook",
            severity="CRITICAL"
        )
        if _error_submitted_callback:
            try:
                _error_submitted_callback(exc_type, exc_value, exc_traceback,
                                          component="sys.excepthook")
            except Exception:
                pass

    def threading_excepthook(args):
        thread_name = args.thread.name if args.thread else "unknown"
        ErrorCollector.record_error(
            args.exc_type,
            args.exc_value,
            args.exc_traceback,
            extra_info={"thread": thread_name},
            component="threading.excepthook",
            severity="CRITICAL"
        )
        if _error_submitted_callback:
            try:
                _error_submitted_callback(args.exc_type, args.exc_value, args.exc_traceback,
                                          component=f"thread:{thread_name}")
            except Exception:
                pass

    sys.excepthook = sys_excepthook
    threading.excepthook = threading_excepthook


def make_asyncio_exception_handler(component: str = "asyncio"):
    """Return a loop exception handler that routes unhandled asyncio errors to ErrorCollector.

    Usage:
        loop = asyncio.new_event_loop()
        loop.set_exception_handler(make_asyncio_exception_handler("my.component"))
    """
    _log = logging.getLogger("VoiceAssistant.asyncio")

    def handler(loop, context: dict):
        exc: BaseException | None = context.get("exception")
        msg: str = context.get("message", "Unhandled asyncio exception")
        if exc is not None:
            ErrorCollector.record_error(
                type(exc), exc, exc.__traceback__,
                extra_info={"asyncio_message": msg, "loop": repr(loop)},
                component=component,
                severity="ERROR",
            )
        else:
            _log.error("[%s] %s — context: %s", component, msg, context)

    return handler


def glib_safe(fn, component: str | None = None):
    """Wrap a GLib callback (idle_add, timeout_add, D-Bus signal) so that any
    unhandled exception is routed to ErrorCollector instead of being silently
    swallowed by the GLib main loop.

    Usage:
        GLib.idle_add(glib_safe(self._my_callback))
    """
    import functools

    comp = component or getattr(fn, "__qualname__", repr(fn))
    _log = logging.getLogger("VoiceAssistant.GLib")

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            _log.exception("[glib_safe/%s] unhandled exception", comp)
            ErrorCollector.record_error(
                type(exc), exc, exc.__traceback__,
                extra_info={"glib_callback": comp},
                component=comp,
                severity="ERROR",
            )
            return False  # stop GLib repeat for timeout/idle sources

    return wrapper
