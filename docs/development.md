# Guida Sviluppatori

> Setup dell'ambiente di sviluppo, comandi di build con Meson, suite di test automatizzati, workflow di debug e convenzioni del progetto.

---

## Prerequisiti di Sistema

### Installazione Dipendenze di Sistema

#### Fedora / RHEL
```bash
sudo dnf install meson ninja-build blueprint-compiler python3 python3-devel python3-gobject portaudio-devel gettext
```

#### Ubuntu / Debian
```bash
sudo apt install meson ninja-build blueprint-compiler python3 python3-venv python3-gi libportaudio2 gettext
```

#### Arch Linux
```bash
sudo pacman -S meson ninja blueprint-compiler python python-gobject portaudio gettext
```

---

## Setup Ambiente e Workflow di Build con Meson

### 1. Clonazione e Configurazione Iniziale

```bash
# Clone del repository
git clone https://github.com/Scroker/voice-assistant.git
cd voice-assistant

# Configura l'ambiente Meson nella directory 'build'
meson setup build --prefix=$HOME/.local
```

### 2. Compilazione, Test ed Installazione

```bash
# Compila le risorse (Blueprint -> UI, GResource, Schemi GSettings)
meson compile -C build

# Esegue la suite completa di Unit Test automatizzati
dbus-run-session -- python3 -m pytest -q

# Installa l'estensione e i servizi nella home utente (~/.local)
meson install -C build

# Abilita l'estensione in GNOME Shell
gnome-extensions enable voice-assistant@scroker.github.io
```

### 3. Generazione del Pacchetto ZIP per la Distribuzione

Per impacchettare l'estensione per la distribuzione o l'installazione su altri sistemi via **Extension Manager**:

```bash
# Compila ed impacchetta in build/voice-assistant@scroker.github.io.shell-extension.zip
meson compile -C build zip
```

---

## 🧪 Esecuzione e Struttura dei Test Automatizzati

La suite di test viene eseguita tramite **pytest** isolata dal demone di sistema tramite `dbus-run-session`.

> [!WARNING]
> Run the suite with a memory cap while developing: `systemd-run --user --scope -p MemoryMax=3G -p MemorySwapMax=0 timeout 180 python3 -m pytest -q`

### Comandi Rapidi per i Test
```bash
# Esecuzione standard di tutti i test (isolati dal bus D-Bus reale)
dbus-run-session -- python3 -m pytest -q

# Esecuzione con limite di memoria
systemd-run --user --scope -p MemoryMax=3G -p MemorySwapMax=0 timeout 180 dbus-run-session -- python3 -m pytest -q

# Esecuzione mirata di un singolo modulo di test (es. test_gui)
dbus-run-session -- python3 -m pytest -q tests/test_gui.py

# Esecuzione in ambienti headless o CI (con display virtuale Xvfb)
xvfb-run -a dbus-run-session -- python3 -m pytest -q
```

### Moduli di Test (`tests/`)

| Test File | Scopo e Verifiche |
|---|---|
| `test_gui.py` | Verifica la suite completa dell'interfaccia grafica GTK4/Libadwaita: importazioni, creazione di `AssistantWindow` e `SettingsWindow`, gestione chiusura finestra e tasto ESC, deduplicazione dell'eco dei messaggi utente, componenti riutilizzabili `ChatBubble` e `ChatView`, logica dual-mode (Locale/Cloud) per STT, LLM e TTS, client D-Bus asincrono `DaemonClient` e assemblatore dei blueprint. |
| `test_dependency_installer.py` | Verifica il wizard e la logica di installazione automatica delle dipendenze di sistema/pip mancanti (`vosk`, `faster-whisper`, `llama-cpp-python`, `piper-tts`). |
| `test_js_syntax.py` | Verifica la sintassi JavaScript dei file `src/extension.js` e `src/prefs.js` tramite Node.js e controlla che non vi siano chiamate deprecate a `initGettext()`. |
| `test_schema_and_resources.py` | Verifica la validità e compilazione dello schema GSettings (`gschema.xml`) e del bundle GResource (`prefs.ui`, icone SVG). |
| `test_providers.py` | Verifica l'inizializzazione dei provider STT (Vosk, Whisper) ed il recupero dinamico della lista dei modelli online. |
| `test_download_progress.py` | Verifica la thread-safety e la correttezza del monitoraggio indipendente della percentuale di download dei modelli sul file system. |
| `test_core_state.py` | Verifica le transizioni della state machine del demone (`disabled`, `idle`, `listening`, `processing`, `downloading`). |
| `test_audio.py` | Verifica la pipeline audio, i filtri DSP (High-Pass IIR e Noise Gate adattivo) e la gestione dei chunk PCM. |
| `test_services_downloader.py` | Verifica il gestore asincrono dei download e il calcolo del progresso. |
| `test_core_pipeline.py` | Verifica l'orchestrazione interna della pipeline STT -> LLM -> TTS. |
| `test_pipeline_integration_adapter.py` | Verifica l'adattamento e l'interoperabilità dei contratti tra i vari componenti della pipeline. |
| `test_services_tts.py` | Verifica il servizio di sintesi vocale (Piper neurale offline e fallback). |
| `test_services_llm.py` | Verifica il servizio LLM (motore GGUF locale `llama.cpp` e provider cloud). |
| `test_logger.py` | Verifica il sottosistema di logging strutturato. |
| `test_core_runtime.py` | Verifica l'inizializzazione dei controller del demone, GSettings e thread di runtime. |
| `test_assistant_runtime.py` | Verifica l'event loop della wakeword e l'elaborazione dei comandi vocali. |
| `test_listening_loop_resilience.py` | Verifica la tolleranza ai guasti e la riconnessione automatica del loop di ascolto audio. |
| `test_mcp.py` | Verifica `MCPManager` (config, registry, installer, credential store, client), incluso l'adapter di retrocompatibilità verso i tool di `gnome-mcp-server`. |
| `test_mcp_llm_integration.py` | Verifica l'iniezione del contesto MCP e degli schemi dei tool nei prompt LLM. |
| `test_e2e_pipeline_integration.py` | Test di integrazione end-to-end simulato per l'intero flusso di assistenza. |
| `test_performance_metrics.py` | Verifica la misurazione di RSS, VRAM e latenze del ciclo di vita dei modelli. |
| `test_data_loader.py` | Verifica il caricamento e parsing dei dati statici. |
| `test_cloud_config.py` | Verifica la lettura di configurazione/credenziali per i provider cloud (LLM/STT/TTS). |
| `test_defaults_and_locales.py` | Verifica i valori di default e i dati di localizzazione (`data/config/defaults.json`, `data/locales/`). |
| `test_hybrid_rag_store.py` | Verifica il `VectorStore` ibrido in-memory + SQLite (persistenza, sincronizzazione periodica, ricerca). |
| `test_locale_utils.py` | Verifica le utility di localizzazione in `core/locale_utils.py`. |
| `test_model_catalog.py` | Verifica il catalogo centralizzato dei modelli (`services/catalog_manager.py`). |
| `test_model_manager.py` | Verifica `ModelManager`: policy idle-unload e reclaim RAM/VRAM. |
| `test_model_registry.py` | Verifica `core/model_registry.py`: scansione modelli installati/disponibili per provider. |
| `test_ollama_fixes.py` | Verifica correzioni specifiche all'integrazione con Ollama (locale/cloud). |
| `test_semantic_dispatch.py` | Verifica il matching semantico degli intenti (`VectorIntentMatcher`). |
| `test_skill_executor.py` | Verifica l'esecuzione delle skill Markdown (`skills/skill_executor.py`). |
| `test_skill_markdown_loader.py` | Verifica il parsing dei file SKILL.md con frontmatter. |
| `test_smart_path_components.py` | Verifica i singoli componenti dello SMART PATH (memoria, RAG, prompt builder, parser). |
| `test_smart_path_controller.py` | Verifica `SmartPathController.execute_smart_path()` end-to-end. |
| `test_streaming_pipeline.py` | Verifica `StreamingPipelineEngine`/`core/pipeline_integration.py` — codice non collegato al daemon in esecuzione (vedi [`docs/streaming-pipeline-guide.md`](streaming-pipeline-guide.md)), testato solo in isolamento. |
| `test_wakeword.py` | Verifica i motori wakeword alternativi (OpenWakeWord, Sherpa-ONNX) oltre a Vosk. |

> [!NOTE]
> Tutti i file di test in `tests/` vengono eseguiti automaticamente tramite `pytest`. Per eseguire i test in modo sicuro durante lo sviluppo, usare sempre `dbus-run-session -- python3 -m pytest -q` con un limite di memoria.

---

## Technical Gotchas e Scelte Architetturali Note

### 1. D-Bus Activation & Systemd Lifecycle
Il servizio background `voice-assistant.service` viene avviato **on-demand via D-Bus activation** quando l'estensione GNOME viene abilitata (`Gio.BusNameWatcherFlags.AUTO_START`). Non richiede l'autostart manuale in systemd, risparmiando memoria RAM se l'estensione è disattivata.

### 2. ALSA / Pipewire Process Name Fix (`start.sh`)
Quando il daemon Python si registra come client audio Pipewire/PulseAudio, il server audio mostra il nome dell'eseguibile Python generico (`python3`). `start.sh` risolve questo creando `venv/bin/VoiceAssistant` come copia reale del binario (`readlink -f` + `cp`, non symlink) ed eseguendo `exec venv/bin/VoiceAssistant main.py`. Vengono usati percorsi assoluti (`$DIR/venv/bin/python3`) ovunque: `python3` dopo `source activate` può ancora puntare al Python di sistema su alcune distribuzioni (Ubuntu, openSUSE).

### 3. Blueprint & GResource Multi-Directory Resolution
`blueprint-compiler` genera il file `prefs.ui` all'interno della directory di build (`build/data/prefs.ui`). In `data/meson.build`, `glib-compile-resources` viene eseguito con i flag:
`--sourcedir=meson.current_source_dir()` e `--sourcedir=meson.current_build_dir()`. Questo permette a GResource di trovare sia i file sorgente in `data/` che i file compilati in `build/data/`.

### 4. Thread-Safety in PyGObject e Python Daemon
GLib richiede che le modifiche allo stato dell'applicazione o all'emissione dei segnali D-Bus avvengano nel Main Thread. Quando i worker thread in background (es. cattura audio `_audio_loop` o download dei modelli) completano un'operazione, la mutazione dello stato deve sempre essere delegata con:
```python
GLib.idle_add(self._update_state, new_state)
```

### 5. Interfaccia Tipizzata dei Controller (`core/daemon_protocol.py`)
I cinque controller del daemon (`AssistantRuntimeController`, `ProviderManager`, `DaemonLifecycle`, `DaemonRuntimeManager`, `AudioRuntimeController`) accedono all'istanza `VoiceAssistant` tramite `self.owner`. Questo riferimento è annotato con il `typing.Protocol` `DaemonOwner` definito in `core/daemon_protocol.py`, che dichiara tutti gli attributi e metodi esposti.

Regola: **ogni attributo aggiunto a `VoiceAssistant` e acceduto da un controller deve essere dichiarato nel Protocol**. Questo rende gli errori di battitura rilevabili da mypy/pyright a compile-time invece che come `AttributeError` a runtime.

### 6. Bridging Async→Sync (`core/async_bridge.py`)
Gli strumenti MCP (`mcp_manager.execute_tool()`) sono coroutine async. I controller chiamano questi metodi in contesti sincroni (thread STT, thread pipeline). Il modulo `core/async_bridge` espone `run_async(coro)` che usa un **background event loop persistente** + `asyncio.run_coroutine_threadsafe()`. Non usare `asyncio.run()` nei thread del daemon: crea e distrugge un loop ad ogni chiamata e fallisce se eseguito da dentro un loop già in esecuzione.

### 7. GUI Text Echo Suppression & Deduplicazione Messaggi
Quando l'utente invia un messaggio tramite la casella di input della chat (`AssistantWindow`), l'interfaccia aggiunge immediatamente la bolla utente (`ChatBubble`) alla vista prima di chiamare il metodo D-Bus `ProcessTextInput(text)`. Quando il demone riceve il comando ed emette il segnale broadcast `TranscriptReceived(text, is_final=True)`, `AssistantWindow` traccia l'ultimo testo inviato (`self._last_sent_text`) ed ignora l'evento D-Bus se corrisponde al testo appena inviato dall'utente. Questo previene la visualizzazione sgradevole di messaggi duplicati nella chat.

### 8. Esecuzione Test Headless per GUI GTK4/Libadwaita
I test grafici in `tests/test_gui.py` utilizzano `Gtk.init_check()` all'avvio: se nessun display server è attivo (`Gdk.Display.get_default() is None`), i test che istanziano finestre GTK vengono saltati in modo pulito con `@unittest.skipIf`. Per eseguire l'intera suite GUI anche su server CI o ambienti senza sessione desktop grafica, è sufficiente avvolgere l'esecuzione con `xvfb-run` e forzare il backend X11:
```bash
GDK_BACKEND=x11 HOME=/tmp/test_home xvfb-run -a python3 -m unittest tests/test_gui.py
```

---

## 🧩 Architettura e Componenti UI (`src/gui/components/`)

L'interfaccia grafica (GUI) dell'assistente è stata ingegnerizzata come un'applicazione **GTK4 + Libadwaita** modulare, conforme ai pattern GNOME moderni e completamente disaccoppiata dalla logica del demone di sistema.

### 1. Struttura del Package `src/gui/components/`

```
src/gui/
├── main.py                  # Entry point Adw.Application (single-instance)
├── assistant_window.py      # Finestra principale di chat (Adw.ApplicationWindow)
├── settings_window.py       # Finestra/dialog impostazioni (Adw.PreferencesDialog)
├── dependency_installer.py  # Dialogo e wizard di installazione dipendenze
├── components/
│   ├── resources.py         # Caricamento centralizzato GResource con fallback locale
│   ├── daemon_client.py     # Client D-Bus asincrono tipizzato per la GUI
│   ├── chat/                # Componenti dedicati alla chat
│   │   ├── chat_bubble.py   # Singola bolla messaggio (user/assistant) con stili e avatar
│   │   └── chat_view.py     # Vista cronologia con autoscroll, streaming e benvenuto
│   └── settings/            # Componenti modulari per le schede impostazioni
│       ├── base.py          # Classe base BaseSettingsPage con helper GSettings
│       ├── general.py       # GeneralSettings (abilitazione, avvio, lingua)
│       ├── wakeword.py      # WakeWordSettings (modello Vosk, trigger phrase, sensibilità)
│       ├── stt.py           # STTSettings (dual-mode Locale/Cloud, Vosk/Whisper, API)
│       ├── llm.py           # LLMSettings (dual-mode Locale/Cloud, GGUF/llama.cpp, provider)
│       ├── tts.py           # TTSSettings (dual-mode Locale/Cloud, Piper ONNX, voci)
│       ├── models.py        # ModelsSettings (storage disco, pulizia, selettore directory)
│       ├── bugreport.py     # BugReportSettings (raccolta log diagnostici di sistema)
│       └── about.py         # AboutSettings (informazioni versione, licenza, crediti)
```

#### Moduli Chiave

- **`resources.py` (`ResourceManager`)**:
  - Fornisce un caricatore centralizzato per file GTK Builder XML e icone: `ResourceManager.load_builder(resource_path, local_filename)`.
  - Implementa un meccanismo trasparente di **fallback su file system locale**: se il bundle GResource compilato non è ancora presente o aggiornato (ad esempio durante sessioni di sviluppo rapido o test unitari), il modulo carica automaticamente la definizione da `data/ui/`.

- **`daemon_client.py` (`DaemonClient`)**:
  - Incapsula la connessione asincrona a `org.local.VoiceAssistant` tramite `Gio.DBusProxy`.
  - Disaccoppia i widget grafici dalla gestione a basso livello di D-Bus.
  - Sottoscrive i segnali broadcast `StateChanged`, `TranscriptReceived`, `ResponseTokenStreamed` e `DownloadProgress`, distribuendoli ai componenti UI tramite callback registrabili.
  - Espone chiamate di alto livello: `send_text(text)`, `toggle_listening()`, `get_models(provider)`, `download_model()`, ecc.

- **Componenti Chat (`src/gui/components/chat/`)**:
  - **`ChatBubble`**: Widget atomico derivato da `Gtk.Box`. Modella il singolo messaggio, supporta il rendering differenziato per utente (allineato a destra con stile accentato) e assistente (allineato a sinistra con avatar dedicato), formattazione Markdown/Pango e pulsanti per copiare il testo negli appunti.
  - **`ChatView`**: Container di alto livello (`Gtk.ScrolledWindow` con `Gtk.Box` e `Gtk.Clamp`). Gestisce l'intero flusso della conversazione: visualizzazione della schermata di benvenuto iniziale con prompt rapidi ("empty state"), accumulo progressivo dei token durante lo streaming LLM in tempo reale, e autoscroll intelligente (scorrendo in basso solo se l'utente non sta consultando messaggi precedenti).

- **Componenti Impostazioni (`src/gui/components/settings/`)**:
  - Ogni pagina delle preferenze è implementata come classe autonoma figlia di `BaseSettingsPage`.
  - Mantiene il codice delle preferenze modulare, testabile singolarmente e indipendente dalla struttura monolitica della finestra principale.

### 2. Pattern Grafico Dual-Mode ("Locale" e "Cloud")

I pannelli delle impostazioni per **STT (Riconoscimento Vocale)**, **LLM (Intelligenza Artificiale)** e **TTS (Sintesi Vocale)** adottano un'architettura grafica consistente basata su due checkbox indipendenti:

1. **Checkbox "Locale (Offline)"**: attiva e mostra i controlli per l'elaborazione completamente locale a bordo macchina (modelli Vosk/Whisper per STT, modelli GGUF/llama.cpp con offload GPU per LLM, voci Piper ONNX per TTS).
2. **Checkbox "Cloud (Online)"**: attiva e mostra i controlli per l'elaborazione remota tramite API (endpoint, credenziali e modelli remoti OpenAI, Anthropic, Ollama, ecc.).

#### Meccanica di Sincronizzazione e Reattività
- **Visibilità Dinamica**: Attivando o disattivando una modalità, i corrispondenti `Adw.PreferencesGroup` vengono mostrati o nascosti istantaneamente (`set_visible(True/False)`).
- **Mutua Consistenza**: I controller garantiscono che l'utente non possa deselezionare entrambe le checkbox lasciando il sistema senza alcun motore attivo; se si tenta di deselezionare l'unica modalità attiva, la checkbox si riattiva automaticamente o l'altra viene accesa.
- **Persistenza**: Le scelte di modalità e le relative configurazioni vengono salvate immediatamente nelle corrispondenti chiavi GSettings.

---

## 🛠️ Workflow Modulare Blueprint (`data/ui/prefs/` e `assemble_blueprints.py`)

L'interfaccia delle preferenze (`prefs.blp`) è stata riorganizzata in componenti atomici per garantire manutenibilità e prevenire conflitti nei repository.

### 1. Architettura dei Moduli in `data/ui/prefs/`

Poiché `blueprint-compiler` non dispone ancora di un'istruzione nativa `@import` o `@include`, i singoli componenti dell'interfaccia sono definiti in file `.blp` separati all'interno della directory `data/ui/prefs/`:

| Modulo | Contenuto e Responsabilità |
|---|---|
| `window.blp` | Struttura portante con `Adw.PreferencesDialog`, ricerca nativa (`search-enabled: true`), dimensioni di default e i placeholder `// PAGES_PLACEHOLDER` e `// SUBPAGES_PLACEHOLDER`. |
| `page_general.blp` | Pagina impostazioni generali: toggle assistente, lingua, e righe di navigazione per le subpage Storage e Bug Reporting. |
| `page_wakeword.blp` | Pagina motore wakeword: personalizzazione frase di attivazione, motore (Vosk/Sherpa/OpenWakeWord) e sensibilità. |
| `page_stt.blp` | Pagina Speech-To-Text: selettori dual-mode Locale/Cloud, modelli Vosk/Whisper e API Cloud. |
| `page_llm.blp` | Pagina LLM: selettori dual-mode Locale (GGUF, Ollama) / Cloud (OpenAI, Anthropic, DeepSeek, ecc.) e riga di navigazione per la subpage Tools (MCP). |
| `page_tts.blp` | Pagina Text-To-Speech: selettori dual-mode Locale (Piper) / Cloud, velocità e pitch. |
| `subpage_models.blp` | Sottopagina Storage e Modelli: monitoraggio spazio totale occupato, directory modelli, pulizia modelli non usati ed elenco modelli scaricati. |
| `subpage_bugreport.blp` | Sottopagina Bug Reporting: configurazione automatica crash reporting Bugzilla e test di connessione. |
| `subpage_mcp.blp` | Sottopagina Tools (MCP): configurazione registry e server Model Context Protocol. |
| `subpages.blp` | Sottopagine di dettaglio: `model_selector_page` (con download Hugging Face GGUF integrato condizionale) e `lang_nav_page` (selettore lingua). |

### 2. Script di Assemblaggio Automatico (`scripts/assemble_blueprints.py`)

Lo script Python `scripts/assemble_blueprints.py` unisce automaticamente i moduli parziali nel file consolidato `data/ui/prefs.blp`:

```bash
# Assemblaggio manuale dei moduli Blueprint
python3 scripts/assemble_blueprints.py
```

Caratteristiche dello script:
- Verifica l'integrità e la presenza di tutti i file dei moduli.
- Riformatta e indenta dinamicamente ciascun modulo con la corretta profondità di spaziatura (2 spazi per le pagine all'interno di `Adw.PreferencesDialog`, top-level per le subpages).
- Sostituisce i placeholder in `window.blp` e scrive il file consolidato `data/ui/prefs.blp`.

### 3. Ciclo di Compilazione Blueprint e Risorse GResource

Quando si modifica l'interfaccia grafica in `data/ui/prefs/`:

```bash
# 1. Assembla i moduli parziali in prefs.blp
python3 scripts/assemble_blueprints.py

# 2. Compila il file .blp nel file XML .ui
blueprint-compiler compile --output data/ui/prefs.ui data/ui/prefs.blp

# 3. Ricompila il bundle GResource e riesegui i test
meson compile -C build && dbus-run-session -- python3 -m pytest -q
```

> [!NOTE]
> Il file compilato `data/ui/prefs.ui` è tracciato nel repository Git. Questo assicura che sviluppatori, distributori di pacchetti o sistemi di Continuous Integration possano compilare ed installare l'estensione anche su macchine sprovviste del binario `blueprint-compiler`. Ogni modifica apportata a `data/ui/prefs/*.blp` deve essere seguita dall'aggiornamento di `data/ui/prefs.blp` e dalla ricompilazione di `data/ui/prefs.ui`.

---

## Workflow di Sviluppo Iterativo

### Modifiche all'Interfaccia Preferenze (`data/ui/prefs/`)

L'interfaccia delle preferenze è scritta in **Blueprint** nei moduli `data/ui/prefs/*.blp`. Non modificare direttamente il file XML consolidato `data/ui/prefs.ui`.

```bash
# Assembla i blueprint, ricompila, testa ed installa l'estensione
python3 scripts/assemble_blueprints.py
blueprint-compiler compile --output data/ui/prefs.ui data/ui/prefs.blp
meson compile -C build && dbus-run-session -- python3 -m pytest -q && meson install -C build
```

### Modifiche al Daemon Python (`src/daemon/`)

```bash
# Reinstalla e riavvia il servizio D-Bus / systemd utente
meson install -C build
systemctl --user restart voice-assistant.service

# Seguire i log del daemon in tempo reale
journalctl --user -u voice-assistant -f
```

### Test delle Chiamate D-Bus da Terminale

```bash
# Invocazione metodo per la lista modelli disponibili
gdbus call --session --dest org.local.VoiceAssistant --object-path /org/local/VoiceAssistant --method org.local.VoiceAssistant.GetAvailableModels vosk
```

---

## Localizzazione (i18n con Gettext)

Le stringhe traducibili sono estratte da `extension.js`, `prefs.js` e `prefs.blp`.

```bash
# Aggiornare il template POT nella directory di build
cd build
meson compile voice-assistant-pot

# Aggiornare il file PO della traduzione italiana
cd ../po
msgmerge -U it.po voice-assistant.pot
```

Per aggiungere una nuova lingua (es. Francese `fr`):
1. Aggiungere `fr` a `po/LINGUAS`.
2. Eseguire `msginit -i po/voice-assistant.pot -o po/fr.po --locale=fr`.
3. Tradurre le stringhe con Poedit o editor di testo.
