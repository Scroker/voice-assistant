import os
import sys
import tempfile
from pathlib import Path

# Make daemon and gui source importable from every test module
_root = Path(__file__).resolve().parent.parent
_daemon_dir = _root / "src" / "daemon"
_daemon_core = _daemon_dir / "core"
_gui_dir = _root / "src" / "gui"

for _p in (_daemon_dir, _daemon_core, _root / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# Ensure unit tests run in an isolated configuration environment
_test_cfg_dir = tempfile.TemporaryDirectory(prefix="va_test_config_")
os.environ.setdefault("VOICE_ASSISTANT_CONFIG_DIR", _test_cfg_dir.name)
os.environ.setdefault("GSETTINGS_BACKEND", "memory")
