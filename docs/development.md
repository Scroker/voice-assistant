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
meson test -C build

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

La suite di test è integrata direttamente in **Meson** ed esegue automaticamente la verifica di sintassi, risorse, provider e gestione thread dei download.

### Comando Rapido per i Test
```bash
# Esecuzione standard di tutti i test
meson test -C build

# Esecuzione in modalità prolissa (mostra l'output dettagliato di ogni test)
meson test -C build --verbose
```

### Moduli di Test (`tests/`)

| Test File | Scopo e Verifiche |
|---|---|
| `test_js_syntax.py` | Verifica la sintassi JavaScript dei file `src/extension.js` e `src/prefs.js` tramite Node.js e controlla che non vi siano chiamate deprecate a `initGettext()`. |
| `test_schema_and_resources.py` | Verifica la validità e compilazione dello schema GSettings (`gschema.xml`) e del bundle GResource (`prefs.ui`, icone SVG). |
| `test_providers.py` | Verifica l'inizializzazione dei provider STT (Vosk, Whisper) ed il recupero dinamico della lista dei modelli online. |
| `test_download_progress.py` | Verifica la thread-safety e la correttezza del monitoraggio indipendente della percentuale di download dei modelli sul file system. |

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

---

## Workflow di Sviluppo Iterativo

### Modifiche all'Interfaccia Preferenze (`data/ui/prefs.blp`)

L'interfaccia delle preferenze è scritta in **Blueprint**. Non modificare file XML `.ui` direttamente in `data/ui/`.

```bash
# Ricompila, testa ed installa l'estensione
meson compile -C build && meson test -C build && meson install -C build
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
