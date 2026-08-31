# Voice Assistant — GNOME Extension

Un'estensione per GNOME Shell che integra un assistente vocale **completamente locale e offline**. Nessun dato lascia la macchina: tutto il riconoscimento vocale avviene on-device tramite Vosk o Whisper.

## ✨ Funzionalità

- 🎤 **Wakeword personalizzabile** — attivazione vocale hands-free (default: *"assistente"*)
- 🧠 **Due motori STT** — Vosk (leggero, streaming reale) e Whisper (preciso, batch via faster-whisper)
- 🔇 **100% Offline** — nessuna connessione cloud, piena privacy
- ⚡ **Download automatico** dei modelli con progress tracking e resume su interruzione
- 🎨 **Feedback visivo nativo** — icona colorata nella top bar + OSD di GNOME
- ⚙️ **Impostazioni live** — pannello preferenze Libadwaita con aggiornamento in tempo reale
- 🔋 **Inibizione sospensione** — blocco automatico di sleep/idle durante il download dei modelli
- 🌍 **Localizzazione** — supporto i18n con Gettext (attualmente: italiano)

## 📋 Requisiti

| Componente | Versione minima |
|---|---|
| GNOME Shell | 45+ (testato su 50) |
| Python | 3.10+ |
| Meson | 0.53+ |
| `python3-gi` (PyGObject) | Qualsiasi |
| `libportaudio2` | Qualsiasi |

## 🚀 Installazione

```bash
# Clona il repository
git clone https://github.com/mkswap/voice-assistant.git
cd voice-assistant

# Configura e installa
meson setup build --prefix=$HOME/.local
meson install -C build
```

Riavvia la sessione GNOME (log out/in su Wayland, oppure `Alt+F2` → `r` su X11), poi abilita l'estensione:

```bash
gnome-extensions enable voice-assistant@mkswap.github.io
```

Il daemon Python viene avviato automaticamente come servizio systemd utente. I modelli vengono scaricati automaticamente al primo utilizzo.

## 🏗️ Architettura

Il progetto è composto da tre componenti:

| Componente | Tecnologia | Ruolo |
|---|---|---|
| **Extension** (`extension.js`) | GJS / GNOME Shell | Icona nella top bar, feedback visivo, orchestrazione del daemon |
| **Preferences** (`prefs.js`) | GJS / Libadwaita | Pannello impostazioni nativo con binding GSettings live |
| **Daemon** (`daemon/main.py`) | Python / dasbus | Cattura audio, wake word detection, riconoscimento vocale |

La comunicazione avviene tramite **D-Bus** (Session Bus) per i comandi e gli eventi, e **GSettings** per la configurazione persistente.

```
Extension ◄──D-Bus──► Daemon Python
    │                      │
    └──── GSettings ───────┘
         (configurazione condivisa)
```

## 📁 Struttura del Progetto

```
voice-assistant@mkswap.github.io/
├── meson.build              # Build system root
├── src/
│   ├── extension.js         # Estensione GNOME Shell
│   ├── prefs.js             # Preferenze Libadwaita
│   └── daemon/
│       ├── main.py          # Entry point daemon
│       ├── start.sh         # Script di avvio systemd
│       ├── requirements.txt # Dipendenze pip
│       └── providers/       # Motori STT pluggabili
│           ├── base.py          # Interfaccia astratta
│           ├── vosk_provider.py # Vosk (streaming)
│           └── whisper_provider.py # Whisper (batch)
├── data/
│   ├── schemas/             # GSettings schema + GResource
│   └── icons/               # Icone SVG simboliche
├── po/                      # Traduzioni (Gettext)
└── docs/                    # Documentazione tecnica
```

## 📖 Documentazione Tecnica

Per dettagli approfonditi, consulta la directory `docs/`:

| Documento | Contenuto |
|---|---|
| [Architettura](docs/architecture.md) | Diagrammi di sistema, state machine, flussi dati, ciclo di vita |
| [D-Bus Reference](docs/dbus.md) | Introspection XML, metodi, segnali, comandi CLI di debug |
| [GSettings Reference](docs/gsettings.md) | Tutte le chiavi, valori validi, comandi `gsettings` |
| [Provider STT](docs/providers.md) | Vosk vs Whisper, formato audio, come aggiungere un nuovo provider |
| [Guida Sviluppatori](docs/development.md) | Setup ambiente, workflow, debug, convenzioni di codice |

## 🔧 Comandi Utili

```bash
# Stato del daemon
systemctl --user status voice-assistant.service

# Log in tempo reale
journalctl --user -u voice-assistant -f

# Monitorare i segnali D-Bus
gdbus monitor --session --dest org.local.VoiceAssistant --object-path /org/local/VoiceAssistant

# Attivare/disattivare via CLI
gdbus call --session --dest org.local.VoiceAssistant \
  --object-path /org/local/VoiceAssistant \
  --method org.local.VoiceAssistant.ToggleListening

# Cambiare wakeword
gsettings set org.gnome.shell.extensions.voice-assistant wakeword "ehi computer"

# Cambiare provider/modello
gsettings set org.gnome.shell.extensions.voice-assistant stt-provider whisper
gsettings set org.gnome.shell.extensions.voice-assistant stt-model base
```

## 📦 Gestione Modelli

I modelli risiedono in `~/.local/share/voice-assistant/models/`:

```
~/.local/share/voice-assistant/models/
├── vosk-model-small-it-0.22/   (~48 MB)
├── vosk-model-it-0.22/         (~1.2 GB)
├── whisper-base/               (~140 MB)
└── whisper-small/              (~466 MB)
```

I modelli possono essere gestiti dalla UI delle preferenze (download e cancellazione) o manualmente dalla cartella.

## 📄 Licenza

[GNU General Public License v3.0](LICENSE)

Copyright © 2026 Giorgio Dramis
