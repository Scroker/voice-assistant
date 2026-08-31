#!/bin/bash
# Ottieni la directory in cui si trova questo script
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
cd "$DIR"

# Systemd intercetta automaticamente stdout e stderr e li manda a journalctl


echo "Avvio script del demone..."

# Se il virtual environment non esiste, creiamolo
if [ ! -d "venv" ]; then
    echo "Il virtual environment non esiste. Creazione in corso..."
    python3 -m venv --system-site-packages venv
    source venv/bin/activate
    echo "Installazione dipendenze..."
    pip install -r requirements.txt
else
    echo "Virtual environment trovato, attivazione..."
    source venv/bin/activate
fi

echo "Avvio del main.py..."
# Creiamo una copia reale dell'eseguibile python per cambiare il nome visto da Pipewire ALSA
rm -f venv/bin/VoiceAssistant
cp $(readlink -f $(which python3)) venv/bin/VoiceAssistant
# Usiamo exec in modo che il processo rimpiazzi questo script bash
exec venv/bin/VoiceAssistant main.py
