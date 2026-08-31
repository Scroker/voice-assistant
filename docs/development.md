# Guida Sviluppatori

> Setup dell'ambiente di sviluppo, comandi utili, workflow di debug, insidie note (gotchas) e convenzioni del progetto.

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

## Setup Ambiente e Workflow di Build

### 1. Clonazione e Build Iniziale

```bash
# Clone del repository
git clone https://github.com/mkswap/voice-assistant.git
cd voice-assistant

# Configura l'ambiente Meson nella directory 'build'
meson setup build --prefix=$HOME/.local

# Compila (Blueprint -> UI, GResource, Schemi) ed installa
meson install -C build

# Abilitare l'estensione
gnome-extensions enable voice-assistant@mkswap.github.io
```

### 2. Generazione del Pacchetto ZIP per la Distribuzione

Per impacchettare l'estensione per la distribuzione o l'installazione su altri sistemi via **Extension Manager**:

```bash
# Compila ed impacchetta in build/voice-assistant@mkswap.github.io.shell-extension.zip
meson compile -C build zip
```

---

## Technical Gotchas e Scelte Architetturali Note

### 1. ALSA / Pipewire Process Name Fix (`start.sh`)
Quando il daemon Python si registra come client audio Pipewire/PulseAudio, il server audio mostra il nome dell'eseguibile Python generico (`python3`). Per far apparire l'applicazione correttamente come **"Voice Assistant"** nelle impostazioni audio di sistema di GNOME, `start.sh` crea un symlink o una copia del binario eseguibile chiamata `VoiceAssistant` ed esegue `exec VoiceAssistant main.py`.

### 2. Blueprint & GResource Multi-Directory Resolution
`blueprint-compiler` genera il file `prefs.ui` all'interno della directory di build (`build/data/prefs.ui`). In `data/meson.build`, `glib-compile-resources` viene eseguito con i flag:
`--sourcedir=meson.current_source_dir()` e `--sourcedir=meson.current_build_dir()`. Questo permette a GResource di trovare sia i file sorgente in `data/` che i file compilati in `build/data/`.

### 3. Thread-Safety in PyGObject e Python Daemon
GLib richiede che le modifiche allo stato dell'applicazione o all'emissione dei segnali D-Bus avvengano nel Main Thread. Quando i worker thread in background (es. cattura audio `_audio_loop` o download dei modelli) completano un'operazione, la mutazione dello stato deve sempre essere delegata con:
```python
GLib.idle_add(self._update_state, new_state)
```

### 4. Tracking dei Download dei Modelli
L'intercettazione dei log da `tqdm` / `sys.stderr` causava blocchi e inaccuratezze (es. stallo al 3%) durante download concorrenti. Il sistema utilizza invece un monitoraggio thread-safe indipendente della crescita delle dimensioni dei file sul filesystem, isolando ciascun modello scaricato.

---

## Workflow di Sviluppo Iterativo

### Modifiche all'Interfaccia Preferenze (`data/ui/prefs.blp`)

L'interfaccia delle preferenze è scritta in **Blueprint**. Non modificare file XML `.ui` direttamente in `data/ui/`.

```bash
# Ricompila ed installa l'estensione
meson install -C build
```

### Modifiche al Daemon Python (`src/daemon/`)

```bash
# Reinstalla e riavvia il servizio systemd utente
meson install -C build
systemctl --user restart voice-assistant.service

# Seguire i log del daemon in tempo reale
journalctl --user -u voice-assistant -f
```

### Modifiche allo Schema GSettings

Dopo aver modificato `data/schemas/org.gnome.shell.extensions.voice-assistant.gschema.xml`:

```bash
# Ricompila ed installa
meson install -C build
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
