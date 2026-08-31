# Architettura del Sistema

> Documento di riferimento per sviluppatori e AI agent che operano sulla codebase.

## Panoramica

Voice Assistant è un'estensione GNOME Shell che implementa un assistente vocale **completamente locale** (nessun dato lascia la macchina). L'architettura è a **tre livelli** con comunicazione bidirezionale su D-Bus.

```mermaid
graph TD
    subgraph GNOME_Shell ["GNOME Shell (GJS)"]
        ext["extension.js<br/>(Panel Indicator)"]
        prefs["prefs.js + data/ui/prefs.blp<br/>(Libadwaita Declarative UI)"]
        ext <-->|GSettings| prefs
    end

    subgraph Python_Daemon ["Python Daemon (systemd user service)"]
        main["main.py — VoiceAssistant<br/>(dasbus @dbus_interface)"]
        subgraph Providers ["providers/"]
            base["base.py — STTProvider"]
            vosk["vosk_provider (Kaldi)"]
            whisper["whisper_provider (faster-whisper)"]
            base --> vosk
            base --> whisper
        end
        main --> Providers
    end

    ext <-->|D-Bus Session Bus| main
    prefs -->|GSettings Direct Bind| main
```

---

## 1. Il Demone Python (`src/daemon/`)

### Entry Point — `main.py`

Il processo viene avviato da `start.sh` tramite systemd e si registra sul Session Bus D-Bus come **`org.local.VoiceAssistant`**.

| Componente | Ruolo |
|---|---|
| `VoiceAssistant` (classe) | Oggetto D-Bus principale. Gestisce stato, audio, provider e inibitori di sospensione |
| `audio_callback()` | Callback `sounddevice` che inserisce i chunk PCM in una `queue.Queue` thread-safe |
| `_audio_loop()` | Thread daemon che consuma la coda e distribuisce i chunk al Wake Word engine o al provider STT |
| `PowerInhibitor` | Doppio lock (logind FD + GNOME SessionManager cookie) durante il download dei modelli |

### State Machine

```mermaid
stateDiagram-v2
    [*] --> disabled
    disabled --> idle : ToggleListening() / GSettings enabled=true
    idle --> disabled : ToggleListening() / GSettings enabled=false
    
    idle --> listening : Wakeword rilevata
    listening --> processing : Testo/Silenzio
    processing --> idle : Completato

    state downloading {
        [*] --> ScaricamentoModello
        ScaricamentoModello --> [*] : Modello Pronto
    }

    idle --> downloading : Richiesta Download
    downloading --> idle : Modello caricato
```

---

## 2. L'Estensione GNOME Shell (`src/extension.js`)

### Ciclo di Vita

```mermaid
flowchart TD
    subgraph enable ["enable()"]
        A1["1. Registra GResource<br/>(Icone SVG, D-Bus XML, UI prefs compilata)"] --> A2["2. Crea AssistantIndicator<br/>(PanelMenu.Button + stylesheet.css)"]
        A2 --> A3["3. Registra Keybinding Nativa<br/>(toggle-shortcut -> Super+V)"]
        A3 --> A4["4. setupDaemonServices()<br/>(Inietta unit Systemd & D-Bus da GResource)"]
        A4 --> A5["5. Avvia Servizio Systemd"]
    end

    subgraph disable ["disable()"]
        B1["1. Rimuovi Keybinding Nativa"] --> B2["2. Distruggi Indicatore Top Bar"]
        B2 --> B3["3. Deregistra GResource"]
    end
```

### D-Bus Proxy

L'estensione legge la definizione XML D-Bus nativa da GResource (`/org/gnome/shell/extensions/voice-assistant/dbus/org.local.VoiceAssistant.xml`) e crea il proxy wrapper via `Gio.DBusProxy.makeProxyWrapper()`.

---

## 3. Le Preferenze (`src/prefs.js` & `data/ui/prefs.blp`)

L'interfaccia delle preferenze utilizza un'architettura **dichiarativa separata**:

- **Definizione Strutturale (`data/ui/prefs.blp`)**: Scritta in sintassi **Blueprint**, compilata durante la build in XML GTK (`prefs.ui`) ed inclusa nella risorsa binaria `.gresource`.
- **Logica e Binding (`src/prefs.js`)**: Carica la vista tramite `Gtk.Builder.new_from_resource()`, gestisce i collegamenti D-Bus, le reazioni agli eventi ed i binding reattivi con **GSettings**.

Pagine dell'interfaccia:
- ⚙️ **Generali**: Attivazione assistente (`SwitchRow`) e Wakeword (`EntryRow`).
- 🎙️ **Motore Vocale (STT)**: Selezione Provider (`Adw.ComboRow`), gestione download e opzioni avanzate.
- 📁 **Archiviazione & Modelli**: Gestione cartella salvataggio modelli (`models-dir`) e pulizia spazio su disco.
- ℹ️ **Informazioni**: Dettagli su versione, D-Bus ed autore.

---

## 4. Servizi Systemd e D-Bus

I file di unit systemd e di servizio D-Bus sono memorizzati come template in `data/services/`:
- `data/services/voice-assistant.service.in`
- `data/services/org.local.VoiceAssistant.service.in`

Durante l'avvio dell'estensione, `setupDaemonServices()` sostituisce il segnaposto `@startScript@` ed inietta i file nelle cartelle dell'utente (`~/.config/systemd/user/` e `~/.local/share/dbus-1/services/`).

---

## 5. Build System e Packaging (Meson & Blueprint)

Il progetto usa **Meson** integrato con `blueprint-compiler`.

### Comandi Principali:
- `meson setup build --prefix=$HOME/.local`: Configurazione ambiente.
- `meson install -C build`: Compilazione Blueprint, GResource, schemi e installazione nell'estensione locale.
- `meson compile -C build zip`: Generazione del pacchetto `.shell-extension.zip` pronto per la distribuzione.
