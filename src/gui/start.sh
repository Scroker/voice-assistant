#!/bin/bash
# Avvio dell'applicazione GUI standalone dell'Assistente Vocale.
# Riusa il virtualenv del demone (--system-site-packages garantisce gi/PyGObject di sistema).

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

DAEMON_VENV="$(dirname "$DIR")/daemon/venv"
if [ -f "$DAEMON_VENV/bin/python3" ]; then
    exec "$DAEMON_VENV/bin/python3" "$DIR/main.py" "$@"
else
    exec python3 "$DIR/main.py" "$@"
fi
