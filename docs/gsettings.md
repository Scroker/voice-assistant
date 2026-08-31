# GSettings Schema Reference

> Schema ID: `org.gnome.shell.extensions.voice-assistant`  
> Path: `/org/gnome/shell/extensions/voice-assistant/`  
> File: `data/schemas/org.gnome.shell.extensions.voice-assistant.gschema.xml`

Questo è il canale di configurazione condiviso tra la UI delle preferenze (`prefs.js`), l'estensione GNOME Shell (`extension.js`) e il demone Python (`main.py`).

---

## Chiavi

### `wakeword` — `string`
- **Default**: `'assistente'`
- **Descrizione**: Parola chiave che attiva il riconoscimento vocale completo.

### `stt-provider` — `string`
- **Default**: `'vosk'`
- **Valori validi**: `'vosk'`, `'whisper'`
- **Descrizione**: Motore STT per il riconoscimento vocale.

### `stt-model` — `string`
- **Default**: `'vosk-model-small-it-0.22'`
- **Descrizione**: Modello selezionato per il provider corrente.

### `stt-hardware` — `string`
- **Default**: `'cpu'`
- **Valori validi**: `'cpu'`, `'cuda'`
- **Descrizione**: Dispositivo di esecuzione dell'inferenza.

### `enabled` — `boolean`
- **Default**: `true`
- **Descrizione**: Controlla se l'assistente è abilitato o disattivato.

### `models-dir` — `string`
- **Default**: `''`
- **Descrizione**: Cartella personalizzata per il salvataggio dei modelli STT.

### `toggle-shortcut` — `array of strings` (`as`)
- **Default**: `['<Super>v']`
- **Descrizione**: Scorciatoia da tastiera nativa GNOME per attivare/disattivare manualmente l'ascolto dell'assistente.
- **Consumata da**: `extension.js` via `Main.wm.addKeybinding()`.

---

## Lettura/Scrittura da CLI

```bash
# Leggi tutte le chiavi
gsettings list-recursively org.gnome.shell.extensions.voice-assistant

# Cambia scorciatoia da tastiera
gsettings set org.gnome.shell.extensions.voice-assistant toggle-shortcut "['<Super><Shift>v']"
```
