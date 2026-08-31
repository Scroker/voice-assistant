#!/bin/bash
# Ottieni la directory in cui si trova questo script
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
cd "$DIR"

# Systemd intercetta automaticamente stdout e stderr e li manda a journalctl


echo "Avvio script del demone..."

# Gestione virtual environment
if [ ! -d "venv" ]; then
    echo "Il virtual environment non esiste. Creazione in corso..."
    python3 -m venv --system-site-packages venv
fi

source venv/bin/activate

# Verifica se mancano dipendenze da requirements.txt
if ! python3 -c "import sounddevice, dasbus, vosk, llama_cpp, piper" &>/dev/null; then
    echo "Installazione/Aggiornamento dipendenze da requirements.txt..."
    pip install -r requirements.txt
fi

echo "Avvio del main.py..."
# Creiamo una copia reale dell'eseguibile python per cambiare il nome visto da Pipewire ALSA
rm -f venv/bin/VoiceAssistant
cp $(readlink -f $(which python3)) venv/bin/VoiceAssistant
# Usiamo exec in modo che il processo rimpiazzi questo script bash
exec venv/bin/VoiceAssistant main.py
