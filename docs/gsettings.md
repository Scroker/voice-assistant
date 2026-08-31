# GSettings Schema Reference

> Schema ID: `org.gnome.shell.extensions.voice-assistant`
> Path: `/org/gnome/shell/extensions/voice-assistant/`
> File: `data/schemas/org.gnome.shell.extensions.voice-assistant.gschema.xml`

Questo è il canale di comunicazione condiviso tra la UI delle preferenze (`prefs.js`) e il demone Python (`main.py`). Ogni modifica alle chiavi è immediatamente propagata a tutti i consumatori tramite il meccanismo `changed::` di GSettings.

---

## Chiavi

### `wakeword` — `string`

| Proprietà | Valore |
|---|---|
| **Default** | `'assistente'` |
| **Descrizione** | La parola chiave che attiva il riconoscimento vocale completo |
| **Consumato da** | `main.py` — comparazione case-insensitive con il testo parziale/finale del riconoscitore Wake Word (Vosk) |
| **Aggiornamento** | Istantaneo (nessun reload del provider) |

### `stt-provider` — `string`

| Proprietà | Valore |
|---|---|
| **Default** | `'vosk'` |
| **Valori validi** | `'vosk'`, `'whisper'` |
| **Descrizione** | Il motore STT usato per il riconoscimento dopo la wakeword |
| **Consumato da** | `main.py` → `providers/__init__.py:get_provider()` |
| **Aggiornamento** | Triggera reload debounced (500 ms) del provider in un thread dedicato |

### `stt-model` — `string`

| Proprietà | Valore |
|---|---|
| **Default** | `'vosk-model-small-it-0.22'` |
| **Valori tipici (Vosk)** | `'vosk-model-small-it-0.22'`, `'vosk-model-it-0.22'`, `'vosk-model-small-en-us-0.15'` |
| **Valori tipici (Whisper)** | `'tiny'`, `'base'`, `'small'`, `'medium'`, `'large-v3'` |
| **Descrizione** | Il modello specifico da caricare per il provider selezionato |
| **Consumato da** | Costruttore del provider (`VoskProvider.__init__` o `WhisperProvider.__init__`) |
| **Aggiornamento** | Triggera reload debounced del provider |

### `stt-hardware` — `string`

| Proprietà | Valore |
|---|---|
| **Default** | `'cpu'` |
| **Valori validi** | `'cpu'`, `'cuda'` |
| **Descrizione** | Dispositivo di esecuzione per l'inferenza. `cuda` richiede GPU NVIDIA con driver compatibili |
| **Consumato da** | `WhisperProvider.__init__` (per Vosk è ignorato, usa sempre CPU) |
| **Aggiornamento** | Triggera reload debounced del provider |

### `stt-extra` — `string`

| Proprietà | Valore |
|---|---|
| **Default** | `'{}'` |
| **Formato** | JSON serializzato |
| **Descrizione** | Configurazioni aggiuntive per il provider, passate come `dict` |
| **Consumato da** | Costruttore del provider (parametro `extra`) |
| **Aggiornamento** | Triggera reload debounced del provider |

### `enabled` — `boolean`

| Proprietà | Valore |
|---|---|
| **Default** | `true` |
| **Descrizione** | Controlla se l'assistente è abilitato (in ascolto della wakeword) o completamente disattivato |
| **Consumato da** | `main.py` — governa le transizioni `disabled ↔ idle` |
| **Aggiornamento** | Istantaneo tramite `GLib.idle_add()` |

### `models-dir` — `string`

| Proprietà | Valore |
|---|---|
| **Default** | `''` (Personalizzato o vuoto per `~/.local/share/voice-assistant/models`) |
| **Descrizione** | Cartella personalizzata per il salvataggio e recupero dei modelli STT |
| **Consumato da** | `prefs.js`, `main.py`, `VoskProvider`, `WhisperProvider` |
| **Aggiornamento** | Triggera reload debounced del provider |

---

## Lettura/Scrittura da CLI

```bash
# Leggi tutte le chiavi
gsettings list-recursively org.gnome.shell.extensions.voice-assistant

# Cambia wakeword
gsettings set org.gnome.shell.extensions.voice-assistant wakeword "ehi computer"

# Cambia provider
gsettings set org.gnome.shell.extensions.voice-assistant stt-provider whisper
gsettings set org.gnome.shell.extensions.voice-assistant stt-model base

# Disabilita l'assistente
gsettings set org.gnome.shell.extensions.voice-assistant enabled false

# Reset a valori di default
gsettings reset-recursively org.gnome.shell.extensions.voice-assistant
```

---

## Note per gli Sviluppatori

1. **Non aggiungere mai una chiave senza aggiornare il file `.gschema.xml`**: le chiavi non dichiarate causano crash fatali di GSettings.
2. **Ricompilare sempre lo schema dopo le modifiche**: `glib-compile-schemas data/schemas/` oppure rilanciare `meson install -C build`.
3. **Il daemon reagisce solo alle chiavi elencate in `on_settings_changed()`**: aggiungere un nuovo `connect("changed::nuova-chiave", ...)` se si introduce una nuova chiave.
4. **`prefs.js` non importa mai direttamente `main.py`**: la comunicazione è sempre mediata da GSettings. Non c'è alcun accoppiamento diretto.
