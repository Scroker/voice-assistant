# Guida Sviluppatori

> Setup dell'ambiente di sviluppo, comandi utili, workflow di debug e convenzioni del progetto.

---

## Prerequisiti di Sistema

| Pacchetto | Scopo |
|---|---|
| `meson` + `ninja-build` | Build system |
| `python3`, `python3-venv` | Runtime del daemon |
| `glib-compile-schemas` | Compilazione GSettings |
| `glib-compile-resources` | Compilazione GResource (icone SVG) |
| `gettext` | Localizzazione |
| `python3-gi` (PyGObject) | Accesso a GLib/Gio dal daemon |
| `libportaudio2` | Backend di `sounddevice` |

### Dipendenze Python (installate automaticamente dal venv)

| Pacchetto | Versione | Scopo |
|---|---|---|
| `dasbus` | any | Binding D-Bus ad alto livello |
| `vosk` | any | Riconoscimento vocale offline (Kaldi) |
| `sounddevice` | any | Cattura audio dal microfono |
| `notify2` | any | Notifiche desktop |
| `faster-whisper` | any | *(Opzionale)* Backend Whisper con CTranslate2 |

---

## Setup Ambiente

```bash
# Clone del repository
git clone https://github.com/mkswap/voice-assistant.git
cd voice-assistant

# Build e installazione locale
meson setup build --prefix=$HOME/.local
meson install -C build

# Riavviare la sessione GNOME (necessario su Wayland)
# Oppure Alt+F2 → 'r' su X11

# Abilitare l'estensione
gnome-extensions enable voice-assistant@mkswap.github.io
```

---

## Workflow di Sviluppo

### Modifiche all'Estensione GNOME (`extension.js`, `prefs.js`)

```bash
# Dopo ogni modifica, reinstallare e riavviare GNOME Shell
meson install -C build

# Se modifichi lo schema GSettings:
glib-compile-schemas ~/.local/share/gnome-shell/extensions/voice-assistant@mkswap.github.io/schemas/

# Vedere i log dell'estensione in tempo reale
journalctl -f -o cat /usr/bin/gnome-shell
```

### Modifiche al Daemon Python (`src/daemon/`)

```bash
# Riavviare il daemon dopo le modifiche
meson install -C build
systemctl --user restart voice-assistant.service

# Oppure, per sviluppo iterativo, eseguire direttamente:
cd src/daemon
source venv/bin/activate
python main.py

# Log del daemon
journalctl --user -u voice-assistant -f
```

### Modifiche allo Schema GSettings

Dopo aver modificato `data/schemas/org.gnome.shell.extensions.voice-assistant.gschema.xml`:

```bash
# Ricompilare
meson install -C build
# Oppure manualmente:
glib-compile-schemas data/schemas/
```

---

## Struttura del Repository

```
voice-assistant@mkswap.github.io/
├── meson.build                              # Build config root
├── README.md                                # Documentazione utente
├── LICENSE                                  # GPLv3
├── data/
│   ├── meson.build                          # Build rules per assets
│   ├── metadata.json.in                     # Metadata dell'estensione GNOME
│   ├── icons/
│   │   ├── mic-1-symbolic.svg               # Icona microfono
│   │   └── vocal-assistant-symbolic.svg     # Icona principale dell'assistente
│   └── schemas/
│       ├── ...gschema.xml                   # Schema GSettings
│       ├── ...gresource.xml                 # Manifest GResource (icone)
│       └── gschemas.compiled                # Binario compilato (generato)
├── src/
│   ├── meson.build                          # Build rules per sorgenti
│   ├── extension.js                         # Estensione GNOME Shell
│   ├── prefs.js                             # Pannello preferenze Libadwaita
│   └── daemon/
│       ├── main.py                          # Entry point del daemon
│       ├── start.sh                         # Script di avvio (systemd)
│       ├── requirements.txt                 # Dipendenze pip
│       ├── venv/                            # Virtual environment (non versionato)
│       └── providers/
│           ├── __init__.py                  # Factory dei provider
│           ├── base.py                      # Classe base astratta
│           ├── vosk_provider.py             # Implementazione Vosk
│           └── whisper_provider.py          # Implementazione Whisper
├── po/
│   ├── meson.build                          # Build rules traduzioni
│   ├── LINGUAS                              # Lingue supportate (it)
│   ├── POTFILES.in                          # File sorgente da tradurre
│   └── it.po                                # Traduzione italiana
├── docs/
│   ├── architecture.md                      # Architettura del sistema
│   ├── gsettings.md                         # Reference GSettings
│   ├── dbus.md                              # Reference D-Bus
│   ├── providers.md                         # Guida ai provider STT
│   └── development.md                       # Questa guida
└── build/                                   # Directory di build (generata)
```

---

## Debug Comune

### Il daemon non si avvia

```bash
# Controllare lo stato
systemctl --user status voice-assistant.service

# Controllare se start.sh è eseguibile
ls -la ~/.local/share/gnome-shell/extensions/voice-assistant@mkswap.github.io/daemon/start.sh

# Provare l'avvio manuale
~/.local/share/gnome-shell/extensions/voice-assistant@mkswap.github.io/daemon/start.sh
```

### Il microfono non funziona

```bash
# Verificare che sounddevice veda i device
python3 -c "import sounddevice; print(sounddevice.query_devices())"

# Controllare che Pipewire/PulseAudio sia in esecuzione
pactl info

# Testare la cattura audio
python3 -c "
import sounddevice as sd
import numpy as np
data = sd.rec(int(16000 * 2), samplerate=16000, channels=1, dtype='int16')
sd.wait()
print(f'Campioni: {len(data)}, Volume medio: {np.abs(data.astype(float)).mean():.0f}')
"
```

### La wakeword non viene rilevata

```bash
# Controllare il valore attuale
gsettings get org.gnome.shell.extensions.voice-assistant wakeword

# Controllare i log del daemon per i risultati parziali di Vosk
journalctl --user -u voice-assistant -f | grep -i "wakeword\|partial\|rilevata"
```

### Il download del modello si blocca

```bash
# Controllare se c'è un file .zip parziale
ls -la ~/.local/share/voice-assistant/models/*.zip

# Il download supporta il resume: riavviare il daemon riprenderà dal punto in cui si è interrotto
systemctl --user restart voice-assistant.service
```

---

## Convenzioni di Codice

### Python (Daemon)

- Commenti e print in **italiano** (coerenza col progetto)
- Thread-safety: l'accesso allo stato (`_state`, `provider`) avviene sempre tramite `GLib.idle_add()` per garantire l'esecuzione nel main thread
- I download operano in thread dedicati con un `load_id` per gestire la concorrenza
- Il `progress_callback` deve accettare un singolo `int` (percentuale 0-100)

### JavaScript (Estensione GNOME)

- Seguire le convenzioni GJS / GNOME Shell
- I messaggi di log usano il prefisso `[VoiceAssistant]`
- Le stringhe traducibili vanno wrappate in `_()`
- L'estensione non importa mai direttamente moduli del daemon

### GSettings

- Ogni nuova chiave richiede: modifica al `.gschema.xml`, handler in `main.py`, UI in `prefs.js`
- I binding in `prefs.js` usano `Gio.SettingsBindFlags.DEFAULT` dove possibile

---

## Localizzazione (i18n)

Le stringhe traducibili sono estratte da `extension.js` e `prefs.js` (definiti in `po/POTFILES.in`).

```bash
# Aggiornare il template POT
cd build
meson compile voice-assistant-pot

# Aggiornare la traduzione italiana
cd ../po
msgmerge -U it.po voice-assistant.pot
```

Attualmente è supportato solo l'italiano (`it`). Per aggiungere una lingua:
1. Aggiungere il codice lingua a `po/LINGUAS`
2. Creare il file `.po` con `msginit`
