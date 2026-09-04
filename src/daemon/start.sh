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
# Installazione dipendenze CORE
#
# Solo le dipendenze necessarie all'avvio del processo Python vengono
# installate automaticamente qui. Le dipendenze opzionali (vosk, sherpa-onnx,
# faster-whisper, llama-cpp-python, piper-tts, keyring, openwakeword, onnxruntime)
# vengono gestite con consenso esplicito tramite l'interfaccia grafica.
#
# --prefer-binary    Preferisce wheel pre-compilate (evita compilazione C/C++).
# --------------------------------------------------------------------------
NEEDS_INSTALL=false
for module in sounddevice dasbus notify2 huggingface_hub; do
    if ! "$PYTHON" -c "import $module" &>/dev/null; then
        NEEDS_INSTALL=true
        break
    fi
done

if [ "$NEEDS_INSTALL" = true ]; then
    echo "Installazione dipendenze core..."
    "$PYTHON" -m pip install \
        --prefer-binary \
        sounddevice dasbus notify2 huggingface-hub \
        || echo "ATTENZIONE: alcune dipendenze core non installate. Il demone potrebbe non avviarsi correttamente."
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
