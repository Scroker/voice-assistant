# Interfaccia D-Bus Reference

> Bus: **Session Bus**  
> Service Name: `org.local.VoiceAssistant`  
> Object Path: `/org/local/VoiceAssistant`  
> Interface: `org.local.VoiceAssistant`  
> Introspection File: `data/dbus/org.local.VoiceAssistant.xml`

---

## Introspection XML

L'XML di introspezione è memorizzato in `data/dbus/org.local.VoiceAssistant.xml` e compilato all'interno delle risorse `gresource`.

```xml
<!DOCTYPE node PUBLIC "-//freedesktop//DTD D-BUS Object Introspection 1.0//EN"
"http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd">
<node>
  <interface name="org.local.VoiceAssistant">
    <method name="ToggleListening">
      <arg type="b" direction="out" name="is_listening"/>
    </method>
    <method name="GetAvailableModels">
      <arg type="s" direction="in" name="provider"/>
      <arg type="s" direction="out" name="models_json"/>
    </method>
    <method name="GetDownloadingModels">
      <arg type="s" direction="out" name="models_json"/>
    </method>
    <method name="DownloadModel">
      <arg type="s" direction="in" name="provider"/>
      <arg type="s" direction="in" name="model"/>
    </method>
    <method name="CancelDownload">
      <arg type="s" direction="in" name="provider"/>
      <arg type="s" direction="in" name="model"/>
    </method>
    <signal name="StateChanged">
      <arg type="s" name="new_state"/>
    </signal>
    <signal name="DownloadProgress">
      <arg type="s" name="provider"/>
      <arg type="s" name="model"/>
      <arg type="i" name="percent"/>
    </signal>
  </interface>
</node>
```

---

## Metodi

### `ToggleListening() → boolean`
Alterna lo stato dell'assistente tra `disabled` e `idle`.

### `GetAvailableModels(provider: string) → string (JSON)`
Ritorna la lista dei modelli supportati ed installati per il provider specificato (`vosk` o `whisper`).

### `GetDownloadingModels() → string (JSON)`
Ritorna una mappa dei modelli attualmente in fase di download ed il relativo progresso percentuale.

### `DownloadModel(provider: string, model: string)`
Avvia in background lo scaricamento del modello specificato.

### `CancelDownload(provider: string, model: string)`
Annulla il download in corso del modello specificato e rimuove i file parziali dal disco.

---

## Segnali

### `StateChanged(new_state: string)`
Emesso ad ogni transizione di stato della state machine (`disabled`, `idle`, `listening`, `processing`, `speaking`, `downloading`).

### `DownloadProgress(provider: string, model: string, percent: int)`
Emesso periodicamente durante lo scaricamento di un modello per aggiornare la percentuale di progresso in tempo reale nella UI.
