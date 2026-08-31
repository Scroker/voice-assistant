# Interfaccia D-Bus Reference

> Bus: **Session Bus**
> Service Name: `org.local.VoiceAssistant`
> Object Path: `/org/local/VoiceAssistant`
> Interface: `org.local.VoiceAssistant`

---

## Introspection XML

Questo XML è definito in `src/extension.js` (lato client GJS) e implementato in `src/daemon/main.py` (lato server Python tramite `dasbus`).

```xml
<node>
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
</node>
```

---

## Metodi

### `ToggleListening() → boolean`

Alterna lo stato dell'assistente tra `disabled` e `idle`.

| Ritorno | Significato |
|---|---|
| `true` | L'assistente è stato **abilitato** (transizione a `idle`) |
| `false` | L'assistente è stato **disabilitato** (transizione a `disabled`) |

**Side effects:**
- Scrive `enabled` nel backend GSettings
- Avvia o ferma il flusso audio del microfono

**Invocazione da CLI:**

```bash
gdbus call --session \
  --dest org.local.VoiceAssistant \
  --object-path /org/local/VoiceAssistant \
  --method org.local.VoiceAssistant.ToggleListening
```

---

## Segnali

### `StateChanged(new_state: string)`

Emesso ad ogni transizione di stato della state machine.

| Stato | Significato |
|---|---|
| `"disabled"` | Assistente spento, microfono chiuso |
| `"idle"` | In attesa della wakeword |
| `"listening"` | Wakeword rilevata, STT attivo |
| `"processing"` | Testo riconosciuto, elaborazione in corso |
| `"speaking"` | Risposta audio in corso (futuro) |
| `"downloading"` | Download modello in corso |

**Monitoraggio da CLI:**

```bash
gdbus monitor --session \
  --dest org.local.VoiceAssistant \
  --object-path /org/local/VoiceAssistant
```

### `DownloadProgress(percent: int)`

Emesso durante il download di un modello STT. Il valore `percent` va da `0` a `100` con granularità di 1%.

> **Nota**: questo segnale viene emesso solo per il provider attualmente selezionato. Se l'utente cambia modello durante un download, il vecchio download continua in background ma smette di emettere il segnale.

---

## Attivazione Automatica

Il file `.service` D-Bus (`~/.local/share/dbus-1/services/org.local.VoiceAssistant.service`) abilita l'attivazione automatica: qualsiasi chiamata al bus name `org.local.VoiceAssistant` avvia il demone se non è in esecuzione, tramite il servizio systemd associato.

---

## Debugging

```bash
# Stato del servizio
systemctl --user status voice-assistant.service

# Log in tempo reale
journalctl --user -u voice-assistant -f

# Riavvio del demone
systemctl --user restart voice-assistant.service

# Introspezione live
gdbus introspect --session \
  --dest org.local.VoiceAssistant \
  --object-path /org/local/VoiceAssistant
```
