# Voice Assistant — GNOME Extension

Un'estensione per GNOME Shell che integra un assistente vocale **completamente locale e offline**. Nessun dato lascia la macchina: tutto il riconoscimento vocale avviene on-device tramite Vosk o Whisper.

## ✨ Funzionalità

- 🎤 **Wakeword personalizzabile** — attivazione vocale hands-free (default: *"assistente"*)
- 🤖 **Model Context Protocol (MCP)** — suite native di 8 tool per il controllo di volume, tema, luminosità, app, orologio, media, alimentazione e appunti
- ⚡ **Fast-Path Offline (<10ms)** — esecuzione immediata dei comandi frequenti a bassissima latenza senza attendere l'LLM
- ⌨️ **Scorciatoia da tastiera nativa** — attivazione rapida tramite `<Super>v` configurabile
- 🧠 **Due motori STT** — Vosk (leggero, streaming reale) e Whisper (preciso, batch via faster-whisper)
- 🔇 **100% Offline** — nessuna connessione cloud, piena privacy
- 🔊 **AEC & Noise Suppression a Runtime** — filtro DSP integrato (Biquad Passo-Alto + Noise Gate) e integrazione PipeWire WebRTC AEC per un Barge-In nitido
- ⚡ **Download automatico** dei modelli con progress tracking, notifica ed annullamento
- 🎨 **Interfaccia Blueprint & Libadwaita** — layout moderno e pulito (.blp) caricato nativamente con Gtk.Builder
- 🔋 **Inibizione sospensione** — blocco automatico di sleep/idle durante il download dei modelli
- 🌍 **Localizzazione** — supporto i18n con Gettext (attualmente: italiano)

## 📋 Requisiti

| Componente | Versione minima |
|---|---|
| GNOME Shell | 45+ (testato su 50) |
| Python | 3.10+ |
| Meson | 0.56+ |
| `blueprint-compiler` | Qualsiasi |
| `python3-gi` (PyGObject) | Qualsiasi |
| `libportaudio2` | Qualsiasi |

## 🚀 Compilazione, Test e Installazione

```bash
# Clona il repository
git clone https://github.com/Scroker/voice-assistant.git
cd voice-assistant

# Configura l'ambiente Meson
meson setup build --prefix=$HOME/.local

# Compila l'estensione
meson compile -C build

# Esegui la suite di test automatizzati (opzionale)
meson test -C build

# Installa l'estensione
meson install -C build
```

Riavvia la sessione GNOME (log out/in su Wayland, oppure `Alt+F2` → `r` su X11), poi abilita l'estensione:

```bash
gnome-extensions enable voice-assistant@scroker.github.io
```

Il daemon Python viene avviato automaticamente via D-Bus activation. I modelli vengono scaricati automaticamente al primo utilizzo.

### 📦 Creazione pacchetto ZIP

Per generare un file `.zip` dell'estensione installabile tramite **Extension Manager**:

```bash
meson compile -C build zip
```

---

## 🏗️ Architettura

Il progetto è composto da tre componenti:

| Componente | Tecnologia | Ruolo |
|---|---|---|
| **Extension** (`extension.js`) | GJS / GNOME Shell | Indicatore nei Quick Settings (area di sistema), keybinding nativi, orchestrazione daemon |
| **Preferences** (`prefs.js` + `prefs.blp`) | GJS / Blueprint / Libadwaita | Pannello impostazioni nativo con binding GSettings live |
| **Daemon** (`daemon/main.py`) | Python / dasbus | Cattura audio, wake word detection, riconoscimento vocale |

```mermaid
graph LR
    Extension["GNOME Extension<br/>(extension.js)"] <-->|D-Bus Session Bus| Daemon["Daemon Python<br/>(main.py)"]
    Extension <-->|GSettings| SharedConfig[("GSettings")]
    Prefs["Preferences UI<br/>(prefs.js + prefs.blp)"] <-->|GSettings| SharedConfig
    Prefs <-->|D-Bus| Daemon
```

---

## 📁 Struttura del Progetto

```
voice-assistant@scroker.github.io/
├── meson.build              # Build system root
├── stylesheet.css           # Stili CSS per l'indicatore GNOME Shell
├── src/
│   ├── extension.js         # Estensione GNOME Shell (QuickSettings integration)
│   ├── prefs.js             # Logic & Binding preferenze Libadwaita
│   └── daemon/              # Daemon Python background
├── data/
│   ├── ui/prefs.blp         # Layout dell'interfaccia preferenze in Blueprint
│   ├── dbus/                # XML Introspezione D-Bus
│   ├── services/            # Template unit Systemd & D-Bus
│   ├── schemas/             # GSettings schema + GResource
│   └── icons/
│       └── hicolor/         # Icone organizzate secondo lo standard GNOME Icon Theme Spec
├── po/                      # Traduzioni (Gettext)
└── docs/                    # Documentazione tecnica
    ├── architecture.md      # Architettura di sistema
    ├── pipeline.md          # Architettura della Pipeline, Fast-Path e Streaming
    └── mcp-guide.md         # Guida completa ai tool MCP nativi ed esterni
```

## 📄 Licenza

[GNU General Public License v3.0](LICENSE)

Copyright © 2026 Giorgio Dramis
