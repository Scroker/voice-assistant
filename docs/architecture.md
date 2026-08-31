# Architettura del Sistema

> Documento di riferimento per sviluppatori e AI agent che operano sulla codebase.

## Panoramica

Voice Assistant è un'estensione GNOME Shell che implementa un assistente vocale **completamente locale** (nessun dato lascia la macchina). L'architettura è a **tre livelli** con comunicazione bidirezionale su D-Bus.

```
┌─────────────────────────────────────────────────────────────────┐
│                       GNOME Shell (GJS)                         │
│  ┌───────────────────┐          ┌───────────────────────────┐   │
│  │   extension.js    │◄────────►│        prefs.js           │   │
│  │  (Panel Indicator) │  GSettings  │  (Libadwaita Prefs UI)  │   │
│  └────────┬──────────┘          └──────────┬────────────────┘   │
│           │ D-Bus (Session Bus)             │ GSettings          │
│           │ org.local.VoiceAssistant        │ (bind diretto)     │
└───────────┼────────────────────────────────┼────────────────────┘
            │                                │
            ▼                                ▼
┌───────────────────────────────────────────────────────────────────┐
│                     Python Daemon (systemd user service)          │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │  main.py — VoiceAssistant (dasbus @dbus_interface)       │    │
│  │   ├── audio_callback → queue.Queue → _audio_loop thread  │    │
│  │   ├── Wake Word engine (Vosk small-it, fisso)            │    │
│  │   ├── STT provider (Vosk | Whisper, selezionabile)       │    │
│  │   └── PowerInhibitor (logind + GNOME SessionManager)     │    │
│  └──────────────────────────────────────────────────────────┘    │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │  providers/                                               │    │
│  │   ├── base.py        — STTProvider (ABC)                  │    │
│  │   ├── vosk_provider  — streaming reale (KaldiRecognizer)  │    │
│  │   └── whisper_provider — batch (faster-whisper)           │    │
│  └──────────────────────────────────────────────────────────┘    │
└───────────────────────────────────────────────────────────────────┘
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

```
                 ┌──────────────────────────────┐
                 │         downloading           │
                 │ (modello non ancora pronto)   │
                 └──────────────┬───────────────┘
                                │ provider caricato
                                ▼
    ┌──────────┐         ┌──────────┐         ┌──────────────┐
    │ disabled │◄───────►│   idle   │────────►│  listening   │
    │ (microfono│ toggle  │(wakeword │ wakeword│  (STT attivo) │
    │  chiuso)  │         │ attivo)  │ rilevata│              │
    └──────────┘         └──────────┘         └──────┬───────┘
                                ▲                     │
                                │    testo/silenzio   │
                                │                     ▼
                                │              ┌──────────────┐
                                └──────────────│  processing  │
                                               │  (futuro LLM) │
                                               └──────────────┘
```

**Regole di transizione:**
- **disabled → idle**: `ToggleListening()` o GSettings `enabled=true`
- **idle → listening**: wakeword rilevata (Vosk small-it, sempre attivo)
- **listening → processing → idle**: testo riconosciuto dal provider STT selezionato
- Qualsiasi stato → **downloading**: se il provider richiede download di un modello

### Segnali D-Bus

| Segnale | Firma | Emesso quando |
|---|---|---|
| `StateChanged(s)` | `new_state: string` | Ogni transizione di stato |
| `DownloadProgress(i)` | `percent: int` | Durante il download di un modello (granularità 1%) |

### Metodi D-Bus

| Metodo | Firma | Descrizione |
|---|---|---|
| `ToggleListening() → b` | Ritorna `bool` | Alterna tra `disabled` e `idle` |

### Wake Word Engine

Il motore Wake Word è **sempre Vosk** con il modello `vosk-model-small-it-0.22`, indipendentemente dal provider STT selezionato. Questo è intenzionale: Vosk small-it ha un consumo CPU trascurabile ed è adatto al monitoraggio continuo.

Quando la wakeword (configurabile via GSettings, default: `"assistente"`) viene rilevata nel testo parziale o finale di Vosk, il daemon transisce in stato `listening` e delega il riconoscimento al provider STT configurato dall'utente.

### Gestione Settings Live

Il daemon sottoscrive individualmente ogni chiave GSettings:

```python
self.settings.connect("changed::wakeword", self.on_settings_changed)
self.settings.connect("changed::stt-provider", self.on_settings_changed)
self.settings.connect("changed::stt-model", self.on_settings_changed)
self.settings.connect("changed::stt-hardware", self.on_settings_changed)
self.settings.connect("changed::stt-extra", self.on_settings_changed)
self.settings.connect("changed::enabled", self.on_settings_changed)
```

La wakeword viene aggiornata istantaneamente. Le altre chiavi (`stt-provider`, `stt-model`, `stt-hardware`, `stt-extra`) triggerano un **reload debounced** a 500 ms tramite `_schedule_reload()` + `threading.Timer`, per evitare ricaricamenti multipli quando l'utente cambia provider e modello in rapida sequenza.

Ogni `load_provider()` opera in un thread dedicato con un `load_id` incrementale: se l'utente cambia modello *durante* il download, il vecchio thread completa il download in background ma non sovrascrive il provider attivo.

---

## 2. L'Estensione GNOME Shell (`src/extension.js`)

### Ciclo di Vita

```
enable()
  ├── Registra GResource (icone SVG)
  ├── Crea AssistantIndicator (PanelMenu.Button nella top bar)
  └── setupDaemonServices(extensionDir)
        ├── Scrive voice-assistant.service in ~/.config/systemd/user/
        ├── Scrive org.local.VoiceAssistant.service in ~/.local/share/dbus-1/services/
        ├── systemctl --user daemon-reload
        └── systemctl --user start voice-assistant.service

disable()
  ├── Distrugge l'indicatore
  └── De-registra GResource
  (Il demone systemd NON viene fermato: continua in background)
```

### D-Bus Proxy

L'estensione usa `Gio.bus_watch_name()` per monitorare l'apparizione/scomparsa del daemon sul bus. Quando appare, crea un proxy wrapper dall'introspection XML:

```xml
<interface name="org.local.VoiceAssistant">
  <method name="ToggleListening">
    <arg type="b" direction="out" name="is_listening"/>
  </method>
  <signal name="StateChanged">
    <arg type="s" name="new_state"/>
  </signal>
  <signal name="DownloadProgress">
    <arg type="i" name="percent"/>
  </signal>
</interface>
```

### Feedback Visivo

| Stato | Icona | Colore | OSD |
|---|---|---|---|
| `idle` | `vocal-assistant-symbolic` | Default | No |
| `listening` | `vocal-assistant-symbolic` | `#3584e4` (blu) | "In ascolto..." |
| `processing` | `system-run-symbolic` | `#e5a50a` (giallo) | No |
| `speaking` | `audio-volume-high-symbolic` | `#3584e4` | No |
| `downloading` | `folder-download-symbolic` | `#e5a50a` | No |
| `disabled` | `vocal-assistant-symbolic` | `#e01b24` (rosso) | No |

### OSD Nativo

Compatibilità doppia con GNOME 45-48 (`show(-1, ...)`) e GNOME 49+ (`showAll(...)`).

---

## 3. Le Preferenze (`src/prefs.js`)

Applicazione Libadwaita strutturata con **`Adw.NavigationSplitView`** e sidebar di navigazione reattiva:

- ⚙️ **Generali**: Attivazione assistente (`SwitchRow`) e Wakeword (`EntryRow`) con binding diretto a GSettings.
- 🎙️ **Motore Vocale (STT)**: Selezione Provider (`Adw.ComboRow` tra Vosk e Whisper), Download automatico modelli Vosk e configurazione taglia/accelerazione hardware per Whisper.
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
- **Download tracking**: monkey-patch globale di `tqdm` e `sys.stderr` per intercettare le progress bar di HuggingFace Hub e convertirle in callback percentuali
- **Migrazione**: converte automaticamente le vecchie cartelle HuggingFace (`models--Systran--faster-whisper-*`) nella struttura pulita `whisper-<size>`
- **Silence detection**: gestita nel `_audio_loop()` di `main.py` con soglia RMS di 500 e timeout di 2 secondi

---

## 5. Servizi Systemd e D-Bus

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

Tutti i modelli risiedono in:

```
~/.local/share/voice-assistant/models/
├── vosk-model-small-it-0.22/
├── vosk-model-it-0.22/
├── whisper-base/
├── whisper-small/
└── ...
```

Ogni modello ha una cartella dedicata con nome leggibile. La UI delle preferenze scansiona questa directory per popolare la lista dei modelli installati.

---

## 7. Build System (Meson)

Il progetto usa Meson + Ninja seguendo le convenzioni delle estensioni GNOME moderne.

### Installazione

```bash
meson setup build --prefix=$HOME/.local
meson install -C build
```

### Target principali

| Target | Output |
|---|---|
| `data/meson.build` | Compila GResource (icone SVG), copia GSchema XML, genera `metadata.json` |
| `src/meson.build` | Installa `extension.js`, `prefs.js` e la directory `daemon/` (escludendo `venv/`) |
| `po/meson.build` | Compila i file `.po` per la localizzazione |
| Post-install | `glib-compile-schemas` sullo schema nella directory di installazione |

### Directory di installazione

```
~/.local/share/gnome-shell/extensions/voice-assistant@mkswap.github.io/
├── metadata.json
├── extension.js
├── prefs.js
├── org.gnome.shell.extensions.voice-assistant.gresource
├── schemas/
│   ├── org.gnome.shell.extensions.voice-assistant.gschema.xml
│   └── gschemas.compiled
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
