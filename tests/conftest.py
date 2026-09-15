import os
import sys
import tempfile
import shutil
import subprocess
from pathlib import Path

# Redirect every per-user directory to a throwaway location BEFORE project
# modules are imported: several modules compute paths at import time.
_test_home = tempfile.TemporaryDirectory(prefix="va_test_home_")
os.environ["HOME"] = _test_home.name
os.environ["XDG_DATA_HOME"] = os.path.join(_test_home.name, ".local", "share")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_test_home.name, ".config")
os.environ["XDG_CACHE_HOME"] = os.path.join(_test_home.name, ".cache")
for _d in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
    os.makedirs(os.environ[_d], exist_ok=True)

try:
    import keyring
    import keyring.errors
    from keyring.backend import KeyringBackend
except ImportError:
    keyring = None

if keyring is not None:
    class _MemoryKeyring(KeyringBackend):
        priority = 1

        def __init__(self):
            super().__init__()
            self._store = {}

        def get_password(self, service, username):
            return self._store.get((service, username))

        def set_password(self, service, username, password):
            self._store[(service, username)] = password

        def delete_password(self, service, username):
            if self._store.pop((service, username), None) is None:
                raise keyring.errors.PasswordDeleteError(username)

    keyring.set_keyring(_MemoryKeyring())

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

_schema_dir = tempfile.TemporaryDirectory(prefix="va_test_schemas_")
shutil.copy(
    _root / "data" / "schemas" / "org.gnome.shell.extensions.voice-assistant.gschema.xml",
    _schema_dir.name,
)
subprocess.run(["glib-compile-schemas", _schema_dir.name], check=True)
os.environ["GSETTINGS_SCHEMA_DIR"] = _schema_dir.name
