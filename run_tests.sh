#!/bin/bash
# Script per l'esecuzione automatica degli Unit Test del demone Voice Assistant

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$HOME/.local/share/gnome-shell/extensions/voice-assistant@mkswap.github.io/daemon/venv/bin/python3"

if [ ! -f "$VENV_PYTHON" ]; then
    VENV_PYTHON="python3"
fi

echo "=== Esecuzione Unit Test Voice Assistant ==="
"$VENV_PYTHON" -m pytest -v "$SCRIPT_DIR/tests/"

echo ""
echo "✅ Tutti gli unit test sono stati completati con successo!"
