# Guida Sviluppatori

> Setup dell'ambiente di sviluppo, comandi utili, workflow di debug e convenzioni del progetto.

---

## Prerequisiti di Sistema

| Pacchetto | Scopo |
|---|---|
| `meson` + `ninja-build` | Build system |
| `blueprint-compiler` | Compilatore interfacce UI Blueprint (.blp → .ui) |
| `python3`, `python3-venv` | Runtime del daemon |
| `glib-compile-schemas` | Compilazione GSettings |
| `glib-compile-resources` | Compilazione GResource (icone, UI, D-Bus XML, servizi) |
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

### Generazione del Pacchetto ZIP installabile

Per pacchettizzare l'estensione in un file `.zip` pronto per la distribuzione o l'installazione su altri sistemi:

```bash
# Genera build/voice-assistant@mkswap.github.io.shell-extension.zip
meson compile -C build zip
```

---

## Workflow di Sviluppo

### Modifiche all'Interfaccia Preferenze (`data/ui/prefs.blp`)

L'interfaccia preferenze è scritta in **Blueprint**. Non modificare manualmente file XML `.ui`.

```bash
# Modifica il file Blueprint
nano data/ui/prefs.blp

# Ricompila ed installa l'estensione
meson install -C build
```

### Modifiche all'Estensione GNOME (`src/extension.js`, `src/prefs.js`)

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

---

## Struttura del Repository

```
voice-assistant@mkswap.github.io/
├── meson.build                              # Build config root
├── stylesheet.css                           # Stili CSS per l'indicatore GNOME Shell
├── README.md                                # Documentazione utente
├── LICENSE                                  # GPLv3
├── data/
│   ├── meson.build                          # Build rules per assets e Blueprint
│   ├── metadata.json.in                     # Metadata dell'estensione GNOME
│   ├── dbus/
│   │   └── org.local.VoiceAssistant.xml     # Introspezione D-Bus XML
│   ├── services/
│   │   ├── voice-assistant.service.in       # Template servizio Systemd
│   │   └── org.local.VoiceAssistant.service.in # Template attivazione D-Bus
│   ├── ui/
│   │   └── prefs.blp                        # Interfaccia preferenze in Blueprint
│   ├── icons/
│   │   ├── mic-1-symbolic.svg               # Icona microfono
│   │   └── vocal-assistant-symbolic.svg     # Icona principale dell'assistente
│   └── schemas/
│       ├── ...gschema.xml                   # Schema GSettings
│       └── ...gresource.xml                 # Manifest GResource
├── src/
│   ├── meson.build                          # Build rules per sorgenti
│   ├── extension.js                         # Estensione GNOME Shell
│   ├── prefs.js                             # Logic & Binding del pannello preferenze
│   └── daemon/
│       ├── main.py                          # Entry point del daemon
│       ├── start.sh                         # Script di avvio (systemd)
│       ├── requirements.txt                 # Dipendenze pip
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
```

---

## Convenzioni di Codice

### UI e Stile (Blueprint & CSS)
- Tutta la struttura delle preferenze va definita in `data/ui/prefs.blp`.
- `prefs.js` deve contenere solo la logica dei segnali, i binding GSettings ed il popolamento dinamico delle liste.
- Gli stili grafici dell'estensione vanno aggiunti in `stylesheet.css`.

### Python (Daemon)
- Thread-safety: l'accesso allo stato avviene sempre tramite `GLib.idle_add()` per il main thread.
- I download operano in thread dedicati per isolare ciascuna operazione.

### JavaScript (Estensione GNOME)
- Seguire le convenzioni GJS / GNOME Shell.
- I log usano il prefisso `[VoiceAssistant]`.
- Le stringhe traducibili usano `_()`.
