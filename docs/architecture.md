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
        main <--> gui["gui/assistant_window.py<br/>(GTK4 Chat GUI Window)"]
    end

    ext <-->|D-Bus Session Bus| main
    prefs -->|GSettings Direct Bind| main
```

---

## 1. Il Demone Python (`src/daemon/`)

### Struttura attuale del demone

Il processo viene avviato da `start.sh` tramite systemd e si registra sul Session Bus D-Bus come **`org.local.VoiceAssistant`**. La parte operativa del demone è stata separata in componenti dedicati per ridurre la complessità del punto di ingresso.

| Componente | Ruolo |
|---|---|
| `main.py` | Entry point del daemon; avvia il bootstrap e lascia la logica operativa ai moduli `core/` |
| `core/power.py` | Gestisce la sospensione del sistema e gli inhibitor logind/GNOME durante i download e lo stato attivo |
| `core/audio_runtime.py` | Verifica e inizializza l’AEC PipeWire, il dispositivo audio e la stream di input |
| `core/lifecycle.py` | Concentra gestione dello stato, notifiche e emissione dei segnali D-Bus |
| `core/model_manager.py` | Registra modelli in-process, applica la policy idle e coordina il reclaim RAM/VRAM |
| `core/provider_manager.py` | Gestisce caricamento provider, download e cleanup dei modelli |
| `core/service_bootstrap.py` | Pubblica l’oggetto D-Bus e avvia il loop di eventi |
| `core/runtime_manager.py` | Inizializza settings, wakeword, servizi, pipeline e avvia i thread background |
| `core/assistant_runtime.py` | Gestisce wakeword, audio loop, trigger assistant e processing del testo |
| `VoiceAssistant` (classe) | Oggetto D-Bus principale; coordina i componenti e espone i metodi e i segnali |

### Entry Point — `main.py`

Il punto di ingresso è oggi molto più leggero: crea l’istanza della classe principale, inizializza il runtime e registra il servizio sul bus tramite `service_bootstrap.py`.

| Componente | Ruolo |
|---|---|
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

**Regole di transizione:**
- **disabled → idle**: `ToggleListening()` o GSettings `enabled=true`
- **idle → listening**: wakeword rilevata (Vosk small-it, sempre attivo)
- **listening → processing → idle**: testo riconosciuto dal provider STT selezionato
- Qualsiasi stato → **downloading**: se il provider richiede il download di un modello

### Segnali D-Bus

| Segnale | Firma | Emesso quando |
|---|---|---|
| `StateChanged(s)` | `new_state: string` | Ogni transizione di stato |
| `DownloadProgress(s, s, i)` | `provider: string, model: string, percent: int` | Durante il download di un modello (granularità 1%) |

### Metodi D-Bus

| Metodo | Firma | Descrizione |
|---|---|---|
| `ToggleListening() → b` | Ritorna `bool` | Alterna tra `disabled` e `idle` |
| `GetAvailableModels(s) → s` | `provider: string` | Restituisce il JSON dei modelli installati e disponibili |
| `GetDownloadingModels() → s` | N/A | Restituisce il JSON dei download in corso |
| `DownloadModel(s, s)` | `provider: string, model: string` | Avvia il download in background di un modello |
| `CancelDownload(s, s)` | `provider: string, model: string` | Annulla un download in corso e pulisce i file parziali |

### Wake Word Engine

Il motore Wake Word è **sempre Vosk** con il modello `vosk-model-small-it-0.22`, indipendentemente dal provider STT selezionato per la trascrizione completa. Questo garantisce un consumo di risorsa CPU trascurabile durante il monitoraggio continuo.

Il runtime del wakeword e dell’interazione vocale è oggi gestito da `core/assistant_runtime.py`, che raccoglie l’event loop audio, il trigger dell’assistente e la logica di riconoscimento del comando.

Quando la wakeword (configurabile via GSettings, default: `"assistente"`) viene rilevata nel testo parziale o finale di Vosk, il daemon transisce nello stato `listening` e delega il riconoscimento completo al provider STT configurato dall'utente.

### Sistema di Pulizia Audio a Runtime & AEC (`AudioFilter` + PipeWire)

L’operazione di setup audio è allocata in `core/audio_runtime.py`, mentre la parte applicativa del filtraggio del segnale resta in `audio/filter.py`. Questo separa il bootstrap del sistema audio dal processamento del flusso PCM.

Per garantire la massima accuratezza di riconoscimento durante la riproduzione audio e in ambienti rumorosi, il sistema applica un'elaborazione audio a due livelli:

1. **Livello Sistema — PipeWire WebRTC AEC (`module-echo-cancel`)**:
   All'avvio, il demone verifica ed attiva il modulo nativo PipeWire `module-echo-cancel` con algoritmo `aec_method=webrtc` ed imposta la sorgente predefinita a `echo-cancel-source`. Se la scheda audio o il driver PortAudio richiedono un sample rate nativo (es. 48kHz), `_create_stream()` effettua un fallback trasparente sul dispositivo predefinito mantenendo la pulizia a runtime.
2. **Livello Applicativo — Dynamic Audio Filter (`src/daemon/audio/filter.py`)**:
   I chunk PCM grezzi passano attraverso la classe `AudioFilter`:
   - **Filtro Passo-Alto IIR (Biquad 80Hz cutoff)**: Rimuove vibrazioni meccaniche, rumble e il fruscio continuo delle ventole del laptop.
   - **Adaptive Noise Gate**: Calcola il rumore di fondo della stanza ed attenua dell'80% i segnali al di sotto della soglia per prevenire l'invio di rumore ambientale a Vosk/Whisper.

### Gestione Settings Live

Il daemon sottoscrive individualmente le chiavi GSettings:

```python
self.settings.connect("changed::wakeword", self.on_settings_changed)
self.settings.connect("changed::stt-provider", self.on_settings_changed)
self.settings.connect("changed::stt-model", self.on_settings_changed)
self.settings.connect("changed::stt-hardware", self.on_settings_changed)
self.settings.connect("changed::stt-extra", self.on_settings_changed)
self.settings.connect("changed::enabled", self.on_settings_changed)
self.settings.connect("changed::models-dir", self.on_settings_changed)
```

La wakeword viene aggiornata istantaneamente. Le altre chiavi triggerano un **reload debounced** a 500 ms tramite `_schedule_reload()` + `threading.Timer`, per evitare ricaricamenti multipli quando l'utente cambia opzioni in rapida sequenza. Ogni `load_provider()` opera in un thread dedicato con un `load_id` incrementale per isolare le concorrenze.

### Lifecycle dei modelli e reclaim memoria

`VoiceAssistant` crea un solo `ModelManager` e lo passa ai servizi che possiedono risorse in-process. Il manager registra il provider STT selezionato, il runner GGUF locale e la voce Piper solo quando sono effettivamente caricati. A ogni stato attivo (`listening`, `processing`, `speaking`) il timer di inattività viene aggiornato.

Un timer GLib esegue il controllo ogni 30 secondi. Dopo 300 secondi senza attività, il manager richiama le callback di unload dei proprietari, esegue la garbage collection, svuota le cache CUDA/XPU se disponibili e tenta `malloc_trim(0)` su Linux. I file dei modelli restano su disco: vengono liberati soltanto gli handle in RAM/VRAM.

- **STT**: il riferimento del daemon viene rilasciato e il provider viene ricaricato in background alla richiesta di ascolto successiva.
- **LLM GGUF locale**: vengono azzerati l'handle `llama.cpp` e il path attivo; il caricamento successivo resta lazy.
- **Piper TTS**: vengono azzerati la voce ONNX e il relativo nome; la voce viene caricata al prossimo `speak()`.
- **Wakeword Vosk**: resta residente per mantenere l'ascolto continuo.
- **EmbeddingService**: usa vettori sparsi in memoria, non un modello neurale, e non richiede questa policy.

---

## 2. L'Estensione GNOME Shell (`src/extension.js`)

### Ciclo di Vita

```mermaid
flowchart TD
    subgraph enable ["enable()"]
        A1["1. Registra GResource<br/>(Icone SVG, D-Bus XML, UI prefs compilata, Servizi)"] --> A2["2. Crea VoiceAssistantSystemIndicator<br/>(QuickSettings.SystemIndicator + QuickToggle)"]
        A2 --> A3["3. Registra Keybinding Nativa<br/>(toggle-shortcut -> Super+V via Main.wm.addKeybinding)"]
        A3 --> A4["4. setupDaemonServices()<br/>(Inietta unit Systemd & D-Bus da GResource)"]
        A4 --> A5["5. Avvia Servizio Systemd"]
    end

    subgraph disable ["disable()"]
        B1["1. Rimuovi Keybinding Nativa"] --> B2["2. Distruggi Indicatore QuickSettings"]
        B2 --> B3["3. Deregistra GResource"]
    end
```

### Integrazione Quick Settings

L'estensione estende `QuickSettings.SystemIndicator` e si registra nel pannello di sistema tramite `Main.panel.statusArea.quickSettings.addExternalIndicator(this._quickIndicator)`.
- **Icona di Stato**: Inserita nell'area di stato di sistema nella barra superiore (accanto a Volume/Batteria/Rete). Il click sul gruppo apre il menu Quick Settings di GNOME.
- **Quick Toggle**: Interruttore dedicato (`VoiceAssistantQuickToggle`) presente all'interno del menu dei Quick Settings per attivare/disattivare l'ascolto e accedere direttamente al pannello preferenze.

### D-Bus Proxy

L'estensione legge la definizione XML D-Bus da GResource (`/org/gnome/shell/extensions/voice-assistant/dbus/org.local.VoiceAssistant.xml`) tramite `Gio.resources_lookup_data()` e crea il proxy wrapper con `Gio.DBusProxy.makeProxyWrapper()`.

### Feedback Visivo

Gli stili grafici e i colori dell'indicatore sono gestiti in modo dinamico sia sull'icona di sistema che sul toggle nei Quick Settings:

| Stato | Icona | Colore / Stile CSS | OSD |
|---|---|---|---|
| `idle` | `vocal-assistant-symbolic` | Default | No |
| `listening` | `vocal-assistant-symbolic` | `#3584e4` (blu GNOME) | "In ascolto..." |
| `processing` | `brain-augmented-symbolic` | `#e5a50a` (giallo GNOME) | No |
| `speaking` | `vocal-assistant-symbolic` | `#2ec27e` (verde GNOME) | No |
| `downloading` | `folder-download-symbolic` | `#e5a50a` (giallo GNOME) | No |
| `disabled` | Icona nascosta | - | No |
| `unavailable` | `vocal-assistant-symbolic` | `#e01b24` (rosso GNOME) | No |

### OSD Nativo

L'OSD visivo ("In ascolto...") supporta sia GNOME 45-48 (`show(-1, ...)`) che GNOME 49+ (`showAll(...)`).

### Scorciatoia da Tastiera Nativa

L'estensione registra la scorciatoia da tastiera globale configurabile tramite la chiave GSettings `toggle-shortcut` (default: `<Super>v`) mediante la funzione nativa di GNOME Shell `Main.wm.addKeybinding()`.

---

## 3. Le Preferenze (`src/prefs.js` & `data/ui/prefs.blp`)

L'interfaccia delle preferenze utilizza un'architettura **dichiarativa separata**:

- **Definizione Strutturale (`data/ui/prefs.blp`)**: Scritta in sintassi **Blueprint**, compilata in `prefs.ui` durante la build ed inclusa nella risorsa binaria `.gresource`.
- **Logica e Binding (`src/prefs.js`)**: Carica la vista tramite `Gtk.Builder.new_from_resource()`, gestisce i collegamenti D-Bus, le reazioni agli eventi ed i binding reattivi con **GSettings**.

Applicazione Libadwaita strutturata con **`Adw.NavigationSplitView`** e pagine:
- ⚙️ **Generali**: Attivazione assistente (`SwitchRow`) e Wakeword (`EntryRow`) con binding diretto a GSettings.
- 🎙️ **Motore Vocale (STT)**: Selezione Provider (`Adw.ComboRow` tra Vosk e Whisper), Download automatico/manuale modelli e configurazione accelerazione hardware per Whisper.
- 📁 **Archiviazione & Modelli**: Selezione e gestione della cartella di salvataggio modelli (`models-dir`) con selettore nativo GTK, apertura file manager e ripristino cartella di default. Gestione spazio occupato su disco e rimozione modelli.
- ℹ️ **Informazioni**: Dettagli sull'estensione, versione, servizio D-Bus e collegamenti al repository.

Il cambio di provider resetta automaticamente il modello ai valori predefiniti.

---

## 4. Provider STT

### Interfaccia Base (`providers/base.py`)

```python
class STTProvider(abc.ABC):
    def __init__(self, model: str, hardware: str, extra: dict): ...
    def process_chunk(self, data: bytes) -> tuple[str, str]: ...
    def flush_and_transcribe(self) -> str: ...
    def reset(self): ...
```

| Metodo | Descrizione |
|---|---|
| `process_chunk(data)` | Processa un chunk PCM int16. Ritorna `(text, partial_text)`. `text` non vuoto = frase completata |
| `flush_and_transcribe()` | Forza la trascrizione del buffer accumulato (usato per Whisper batch) |
| `reset()` | Resetta lo stato interno del riconoscitore |

### Factory (`providers/__init__.py`)

```python
get_provider(provider_name, model, hardware, extra, progress_callback) -> STTProvider
```

### VoskProvider (`providers/vosk_provider.py`)

- **Streaming reale**: `KaldiRecognizer.AcceptWaveform()` processa ogni chunk e ritorna testo finale/parziale
- **Download automatico**: se il modello non è presente, lo scarica da `alphacephei.com` con resume su interruzione (fino a 10 retry)
- **Migrazione**: supporta la vecchia posizione `~/.cache/vosk/`
- **Alias modelli**: mappa alias come `"it"`, `"small-it"`, `"large-en"` ai nomi ufficiali

### WhisperProvider (`providers/whisper_provider.py`)

- **Batch processing**: accumula l'audio in un `bytearray` e trascrive solo quando `flush_and_transcribe()` viene chiamato
- **Backend**: `faster-whisper` (CTranslate2) con supporto CPU (`int8`) e CUDA (`float16`)
- **Download tracking**: monitoraggio thread-safe della dimensione del filesystem per calcolare l'avanzamento dei download da HuggingFace Hub senza blocchi
- **Silence detection**: gestita nel `_audio_loop()` di `main.py` con soglia RMS di 500 e timeout di 2 secondi

---

## 5. Servizi Systemd e D-Bus

### Template di Servizio (`data/services/`)

I file di configurazione sono memorizzati come template in GResource:
- `data/services/voice-assistant.service.in`
- `data/services/org.local.VoiceAssistant.service.in`

### Systemd Service (`~/.config/systemd/user/voice-assistant.service`)

```ini
[Unit]
Description=Local Voice Assistant Daemon
After=graphical-session.target

[Service]
Type=dbus
BusName=org.local.VoiceAssistant
ExecStart=<extension_dir>/daemon/start.sh
Restart=on-failure
```

### D-Bus Service (`~/.local/share/dbus-1/services/org.local.VoiceAssistant.service`)

```ini
[D-BUS Service]
Name=org.local.VoiceAssistant
Exec=<extension_dir>/daemon/start.sh
SystemdService=voice-assistant.service
```

Il `Type=dbus` garantisce che systemd consideri il servizio "avviato" solo quando il nome D-Bus viene acquisito. La combinazione con il `.service` D-Bus abilita l'**attivazione automatica**: qualsiasi chiamata al bus name avvia il demone se non è in esecuzione.

### Script di Avvio (`start.sh`)

1. Crea un virtualenv con `--system-site-packages` (per accedere a PyGObject di sistema)
2. Installa le dipendenze da `requirements.txt`
3. Crea una copia reale del binario Python rinominata `VoiceAssistant` per far apparire il nome corretto nelle impostazioni audio di GNOME (Pipewire/ALSA usano il nome dell'eseguibile)
4. `exec` del processo Python per rimpiazzare lo script bash

---

## 6. Storage dei Modelli

Tutti i modelli risiedono in `~/.local/share/voice-assistant/models/` (o percorso configurato in `models-dir`):

```
~/.local/share/voice-assistant/models/
├── vosk-model-small-it-0.22/
├── vosk-model-it-0.22/
├── whisper-base/
├── whisper-small/
└── ...
```

Ogni modello ha una cartella dedicata con nome leggibile. La UI delle preferenze ed il daemon scansionano dinamicamente questa directory per elencare i modelli installati ed utilizzabili.

---

## 7. Build System e Packaging (Meson & Blueprint)

Il progetto usa Meson + Ninja integrato con `blueprint-compiler`.

### Target principali

| Target | Output |
|---|---|
| `compile-prefs-blueprint` | Compila `data/ui/prefs.blp` → `build/data/prefs.ui` tramite `blueprint-compiler` |
| `voice-assistant-gresource` | Compila `org.gnome.shell.extensions.voice-assistant.gresource` includendo icone, D-Bus XML, servizi e `prefs.ui` |
| `zip` (`meson compile zip`) | Genera il pacchetto installabile `.shell-extension.zip` pulito (escludendo `venv` e `__pycache__`) |
| Post-install | Compila gli schemi GSettings nella directory di installazione dell'estensione |

### Directory di installazione

```
~/.local/share/gnome-shell/extensions/voice-assistant@scroker.github.io/
├── metadata.json
├── extension.js
├── prefs.js
├── stylesheet.css
├── org.gnome.shell.extensions.voice-assistant.gresource
├── schemas/
│   ├── org.gnome.shell.extensions.voice-assistant.gschema.xml
│   └── gschemas.compiled
├── dbus/
│   └── org.local.VoiceAssistant.xml
├── services/
│   ├── voice-assistant.service.in
│   └── org.local.VoiceAssistant.service.in
└── daemon/
    ├── main.py
    ├── start.sh
    ├── requirements.txt
    └── providers/
        ├── __init__.py
        ├── base.py
        ├── vosk_provider.py
        └── whisper_provider.py
```

---

## 7. Model Context Protocol (MCP) & Tool Nativi

Il Voice Assistant integra un'architettura **MCP (Model Context Protocol)** gestita da `MCPManager` (`src/daemon/mcp/manager.py`) per estendere le capacità del modello LLM e consentire l'esecuzione di comandi su GNOME Desktop.

### Architettura MCP

1. **Tool Nativi (8 Tool)**:
   - `system_volume`: Regolazione del volume audio (`wpctl` / `pactl`).
   - `dark_mode`: Gestione tema chiaro/scuro GNOME Desktop.
   - `app_launcher`: Avvio di applicazioni e browser.
   - `date_time`: Lettura dinamica dell'orologio e della data di sistema.
   - `system_media`: Controllo riproduzione multimediale (Play/Pause/Next/Prev) via MPRIS.
   - `screen_brightness`: Regolazione luminosità schermo (`brightnessctl` / D-Bus).
   - `system_power`: Gestione sessione (lock, suspend, logout, reboot, shutdown).
   - `clipboard`: Lettura e scrittura negli appunti (`wl-clipboard` / `xclip`).

2. **Fast-Path Dispatcher (<10ms)**:
   - Intercetta intenti deterministici ed esegue i tool MCP direttamente senza chiamare l'LLM per una risposta immediata a bassissima latenza.

3. **Dynamic Prompt & Context Injection**:
   - Inserimento automatico degli schemi JSON dei tool e del timestamp di sistema aggiornato ad ogni richiesta dell'LLM in `LLMServiceManager`.

Per i dettagli completi sul funzionamento dei tool, consultare la [Guida MCP](mcp-guide.md). Per approfondire il funzionamento della pipeline di streaming e del Fast-Path, consultare la [Guida alla Pipeline](pipeline.md).
