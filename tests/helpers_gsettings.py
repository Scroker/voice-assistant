import os
import subprocess
import tempfile
from gi.repository import Gio

_schema_dir = None
_settings = None

def get_real_settings():
    global _schema_dir, _settings
    if _settings is None:
        _schema_dir = tempfile.mkdtemp()
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        schemas_src = os.path.join(root_dir, "data", "schemas")
        subprocess.run(["glib-compile-schemas", "--targetdir", _schema_dir, schemas_src], check=True)
        os.environ["GSETTINGS_SCHEMA_DIR"] = _schema_dir
        os.environ["GSETTINGS_BACKEND"] = "memory"
        _settings = Gio.Settings.new("org.gnome.shell.extensions.voice-assistant")
    return _settings
