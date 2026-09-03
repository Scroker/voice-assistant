#!/bin/bash
# Avvio del demone Voice Assistant.
# Systemd intercetta stdout/stderr e li invia a journalctl.

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
cd "$DIR"

echo "Avvio script del demone..."

# --------------------------------------------------------------------------
# Virtual environment
# --------------------------------------------------------------------------
if [ ! -d "venv" ]; then
    echo "Il virtual environment non esiste. Creazione in corso..."
    python3 -m venv --system-site-packages venv
fi

source venv/bin/activate

# Percorso esplicito al Python del venv: non dipende dal PATH di sistema.
# Su alcune distribuzioni 'python3' nel PATH dopo l'activate punta ancora
# al Python di sistema invece che a quello del venv.
PYTHON="$DIR/venv/bin/python3"

# --------------------------------------------------------------------------
# Installazione dipendenze
#
# --prefer-binary    Preferisce wheel pre-compilate (evita compilazione C/C++).
#                    Fondamentale per llama-cpp-python e piper-tts che altrimenti
#                    richiedono cmake + gcc/g++ non sempre presenti.
# --extra-index-url  Wheel pre-compilate di llama-cpp-python (CPU) pubblicate
#                    da abetlen; fallback se PyPI non ha la wheel per la
#                    versione di Python/piattaforma corrente.
# --------------------------------------------------------------------------
NEEDS_INSTALL=false
for module in sounddevice dasbus vosk faster_whisper huggingface_hub llama_cpp piper keyring; do
    if ! "$PYTHON" -c "import $module" &>/dev/null; then
        NEEDS_INSTALL=true
        break
    fi
done

if [ "$NEEDS_INSTALL" = true ]; then
    echo "Installazione/aggiornamento dipendenze..."
    "$PYTHON" -m pip install \
        --prefer-binary \
        --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu \
        -r requirements.txt \
        || echo "ATTENZIONE: alcune dipendenze non installate. Il demone parte comunque."
fi

# --------------------------------------------------------------------------
# Rinomina il binario Python per PipeWire/ALSA.
# Il processo deve chiamarsi 'VoiceAssistant' per apparire correttamente
# nelle impostazioni audio di GNOME/PipeWire.
# --------------------------------------------------------------------------
REAL_PYTHON="$(readlink -f "$PYTHON")"
rm -f venv/bin/VoiceAssistant
cp "$REAL_PYTHON" venv/bin/VoiceAssistant

echo "Avvio di main.py..."
exec venv/bin/VoiceAssistant main.py
