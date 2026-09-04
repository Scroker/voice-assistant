"""Shared pytest configuration and fixtures."""
import os
import sys
from pathlib import Path

# Make daemon and gui source importable from every test module
_root = Path(__file__).resolve().parent.parent
_daemon_dir = _root / "src" / "daemon"
_daemon_core = _daemon_dir / "core"
_gui_dir = _root / "src" / "gui"

for _p in (_daemon_dir, _daemon_core, _root / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
