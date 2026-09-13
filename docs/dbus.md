# Interfaccia D-Bus Reference

> Bus: **Session Bus**  
> Service Name: `org.local.VoiceAssistant`  
> Object Path: `/org/local/VoiceAssistant`  
> Interface: `org.local.VoiceAssistant`  
> Introspection File: `data/dbus/org.local.VoiceAssistant.xml`

L'interfaccia D-Bus è il canale di comunicazione primaria per l'orchestrazione dello stato dell'assistente vocale, l'avvio ed annullamento dei download dei modelli STT e il monitoraggio degli eventi in tempo reale tra la GNOME Shell, il pannello delle preferenze ed il daemon Python.

> [!WARNING]
> `VoiceAssistant` (in `src/daemon/main.py`) usa il decoratore `@dbus_interface` di **dasbus**, che genera l'interfaccia D-Bus reale a runtime a partire dai metodi Python con nome **CamelCase** e annotazioni di tipo. Il file statico `data/dbus/org.local.VoiceAssistant.xml` (riportato sotto) può quindi disallinearsi dal comportamento reale — è esattamente il caso dei 5 metodi marketplace rimossi da questa pagina (vedi nota dopo il blocco XML) e delle firme corrette più sotto. In caso di dubbio, verificare direttamente le annotazioni di tipo su `src/daemon/main.py`.

```mermaid
sequenceDiagram
    autonumber
    actor User as Utente / GNOME Shell
    participant Ext as extension.js / prefs.js
    participant Bus as D-Bus Session Bus
    participant Daemon as main.py (Daemon)

    User->>Ext: Clicca su toggle / Premi <Super>v
    Ext->>Bus: Call ToggleListening()
    Bus->>Daemon: Invocazione ToggleListening()
    Daemon-->>Daemon: Transizione stato (idle <-> disabled)
    Daemon->>Bus: Emit StateChanged("idle")
    Bus-->>Ext: Signal StateChanged("idle")
    Ext-->>User: Aggiorna icona & OSD

    User->>Ext: Seleziona Download Modello
    Ext->>Bus: Call DownloadModel("whisper", "small")
    Bus->>Daemon: Invocazione DownloadModel()
    Daemon-->>Daemon: Avvia Thread Download & File Monitor
    loop Ogni 1% avanzamento
        Daemon->>Bus: Emit DownloadProgress("whisper", "small", percent)
        Bus-->>Ext: Signal DownloadProgress
        Ext-->>User: Aggiorna ProgressBar UI
    end
```

---

## Introspection XML (`data/dbus/org.local.VoiceAssistant.xml`)

```xml
<!DOCTYPE node PUBLIC "-//freedesktop//DTD D-BUS Object Introspection 1.0//EN"
"http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd">
<node>
  <interface name="org.local.VoiceAssistant">
    <method name="ToggleListening">
      <arg type="b" direction="out" name="is_listening"/>
    </method>
    <method name="TriggerListening">
      <arg type="b" direction="out" name="is_listening"/>
    </method>
    <method name="ToggleListeningInContext">
      <arg type="s" direction="in" name="context_id"/>
      <arg type="b" direction="out" name="is_listening"/>
    </method>
    <method name="TriggerListeningInContext">
      <arg type="s" direction="in" name="context_id"/>
      <arg type="b" direction="out" name="is_listening"/>
    </method>
    <method name="GetState">
      <arg type="s" direction="out" name="state"/>
    </method>
    <method name="GetAvailableModels">
      <arg type="s" direction="in" name="provider"/>
      <arg type="s" direction="out" name="models_json"/>
    </method>
    <method name="GetInstalledModels">
      <arg type="s" direction="in" name="provider"/>
      <arg type="s" direction="out" name="models_json"/>
    </method>
    <method name="GetDownloadingModels">
      <arg type="s" direction="out" name="models_json"/>
    </method>
    <method name="GetResourceMetrics">
      <arg type="s" direction="out" name="metrics_json"/>
    </method>
    <method name="DownloadModel">
      <arg type="s" direction="in" name="provider"/>
      <arg type="s" direction="in" name="model"/>
    </method>
    <method name="CancelDownload">
      <arg type="s" direction="in" name="provider"/>
      <arg type="s" direction="in" name="model"/>
    </method>
    <method name="DeleteModel">
      <arg type="s" direction="in" name="provider"/>
      <arg type="s" direction="in" name="model"/>
      <arg type="b" direction="out" name="success"/>
    </method>
    <method name="GetErrorReports">
      <arg type="s" direction="out" name="reports_json"/>
    </method>
    <method name="ClearErrorReports">
    </method>
    <method name="GenerateDiagnosticBundle">
      <arg type="s" direction="out" name="bundle_path"/>
    </method>
    <method name="GetMarketplaceFeatured">
      <arg type="s" direction="out" name="servers_json"/>
    </method>
    <method name="SearchMarketplace">
      <arg type="s" direction="in" name="query"/>
      <arg type="s" direction="out" name="results_json"/>
    </method>
    <method name="GetServerDetails">
      <arg type="s" direction="in" name="server_name"/>
      <arg type="s" direction="out" name="details_json"/>
    </method>
    <method name="GetMarketplaceCategories">
      <arg type="s" direction="out" name="categories_json"/>
    </method>
    <method name="FilterMarketplaceByCategory">
      <arg type="s" direction="in" name="category"/>
      <arg type="s" direction="out" name="results_json"/>
    </method>
    <method name="InstallMCPServer">
      <arg type="s" direction="in" name="server_name"/>
      <arg type="s" direction="in" name="server_config_json"/>
      <arg type="s" direction="in" name="env_vars_json"/>
      <arg type="b" direction="out" name="success"/>
      <arg type="s" direction="out" name="message"/>
    </method>
    <method name="StartMCPServer">
      <arg type="s" direction="in" name="server_name"/>
      <arg type="b" direction="out" name="success"/>
      <arg type="s" direction="out" name="message"/>
    </method>
    <method name="UninstallMCPServer">
      <arg type="s" direction="in" name="server_name"/>
      <arg type="b" direction="out" name="success"/>
      <arg type="s" direction="out" name="message"/>
    </method>
    <method name="TestMCPServer">
      <arg type="s" direction="in" name="server_name"/>
      <arg type="b" direction="out" name="success"/>
      <arg type="s" direction="out" name="message"/>
    </method>
    <method name="UpdateServerConfig">
      <arg type="s" direction="in" name="server_name"/>
      <arg type="s" direction="in" name="env_vars_json"/>
      <arg type="b" direction="in" name="enabled"/>
      <arg type="b" direction="out" name="success"/>
      <arg type="s" direction="out" name="message"/>
    </method>
    <method name="GetInstalledServers">
      <arg type="s" direction="out" name="servers_json"/>
    </method>
    <method name="GetSkills">
      <arg type="s" direction="out" name="skills_json"/>
    </method>
    <method name="SaveSkill">
      <arg type="s" direction="in" name="skill_json"/>
      <arg type="b" direction="out" name="success"/>
      <arg type="s" direction="out" name="message"/>
    </method>
    <method name="DeleteSkill">
      <arg type="s" direction="in" name="intent"/>
      <arg type="b" direction="out" name="success"/>
      <arg type="s" direction="out" name="message"/>
    </method>
    <method name="ShowWindow">
    </method>
    <method name="OpenSettings">
    </method>
    <method name="CreateConversation">
      <arg type="s" direction="in" name="title"/>
      <arg type="s" direction="out" name="context_id"/>
    </method>
    <method name="ListConversations">
      <arg type="s" direction="out" name="conversations_json"/>
    </method>
    <method name="DeleteConversation">
      <arg type="s" direction="in" name="context_id"/>
      <arg type="b" direction="out" name="success"/>
    </method>
    <method name="GetConversationMessages">
      <arg type="s" direction="in" name="context_id"/>
      <arg type="s" direction="out" name="messages_json"/>
    </method>
    <method name="ProcessTextInContext">
      <arg type="s" direction="in" name="text"/>
      <arg type="s" direction="in" name="context_id"/>
    </method>
    <method name="ProcessTextInput">
      <arg type="s" direction="in" name="text"/>
    </method>
    <method name="GetMissingDependencies">
      <arg type="s" direction="out" name="deps_json"/>
    </method>
    <method name="GetSpeakerStatus">
      <arg type="s" direction="out" name="status_json"/>
    </method>
    <method name="GetSpeakerProfiles">
      <arg type="s" direction="out" name="profiles_json"/>
    </method>
    <method name="StartSpeakerEnrollment">
      <arg type="s" direction="in" name="display_name"/>
      <arg type="d" direction="in" name="duration_s"/>
      <arg type="b" direction="out" name="success"/>
    </method>
    <method name="CancelSpeakerEnrollment">
      <arg type="b" direction="out" name="success"/>
    </method>
    <method name="DeleteSpeakerProfile">
      <arg type="s" direction="in" name="profile_id"/>
      <arg type="b" direction="out" name="success"/>
    </method>
    <signal name="StateChanged">
      <arg type="s" name="new_state"/>
    </signal>
    <signal name="TranscriptReceived">
      <arg type="s" name="text"/>
      <arg type="b" name="is_final"/>
    </signal>
    <signal name="ResponseTokenStreamed">
      <arg type="s" name="token"/>
      <arg type="b" name="is_complete"/>
    </signal>
    <signal name="DownloadProgress">
      <arg type="s" name="provider"/>
      <arg type="s" name="model"/>
      <arg type="i" name="percent"/>
    </signal>
    <signal name="DependencyRequired">
      <arg type="s" name="package"/>
      <arg type="s" name="description"/>
      <arg type="b" name="is_critical"/>
    </signal>
    <signal name="SpeakerEnrollmentProgress">
      <arg type="d" name="progress"/>
      <arg type="d" name="level"/>
    </signal>
    <signal name="SpeakerEnrollmentFinished">
      <arg type="b" name="success"/>
      <arg type="s" name="profile_id"/>
      <arg type="s" name="message"/>
    </signal>
    <signal name="SpeakerIdentified">
      <arg type="s" name="profile_name"/>
      <arg type="d" name="score"/>
      <arg type="s" name="status"/>
      <arg type="b" name="overlap_detected"/>
    </signal>
    <signal name="SpeakerRejected">
      <arg type="s" name="reason"/>
      <arg type="s" name="message"/>
    </signal>
    <signal name="ConversationCreated">
      <arg type="s" name="context_id"/>
      <arg type="s" name="reason"/>
    </signal>
    <signal name="ConversationToken">
      <arg type="s" name="context_id"/>
      <arg type="s" name="token"/>
      <arg type="b" name="is_complete"/>
    </signal>
    <signal name="ConversationTranscript">
      <arg type="s" name="context_id"/>
      <arg type="s" name="text"/>
      <arg type="b" name="is_final"/>
    </signal>
  </interface>
</node>
```

---

## Dettaglio Metodi

### `ToggleListening() → boolean`
Alterna lo stato dell'assistente tra `disabled` e `idle`.
- **Ritorno**: `true` se attivo/in ascolto, `false` se disattivato.
- **Side effects**: Avvia o arresta lo stream del microfono ed aggiorna la chiave GSettings `enabled`.

### `ToggleListeningInContext(context_id: string) → boolean`
Alterna lo stato dell'assistente associando la sessione di ascolto vocale e il successivo riconoscimento/risposta alla conversazione specificata da `context_id`. Se `context_id` è vuoto o `"voice"`, opera nel contesto vocale predefinito.
- **Input**: `context_id: string` (es. UUID della chat attiva nella GUI).
- **Ritorno**: `true` se l'assistente è passato in ascolto / attivo, `false` se disattivato.

### `GetAvailableModels(provider: string) → string (JSON)`
Ritorna la lista dei modelli supportati (scaricabili e già installati) per il provider specificato (`vosk`, `whisper`, `openai_cloud`, `groq_cloud`, `cloud_stt`).
- **Input**: uno degli identificatori provider gestiti da `providers/__init__.py::get_provider()`.
- **Output JSON**:
  ```json
  [
    {
      "id": "vosk-model-small-it-0.22",
      "name": "Italian Small (0.22)",
      "downloaded": true,
      "size": "48 MB"
    }
  ]
  ```

### `GetDownloadingModels() → string (JSON)`
Ritorna una mappa dei modelli attualmente in fase di scaricamento ed il relativo progresso percentuale.
- **Output JSON**:
  ```json
  {
    "whisper:small": 45
  }
  ```

### `GetInstalledModels(provider: string = "") → string (JSON)`
Ritorna solo i modelli già presenti su disco (a differenza di `GetAvailableModels`, che elenca anche quelli scaricabili). Se `provider` è vuoto, ritorna i modelli installati per tutti i provider.

  ### `GetResourceMetrics() → string (JSON)`
  Restituisce le metriche del processo daemon e lo stato dei modelli in-process. `rss_bytes` e `vms_bytes` provengono da `/proc/self/status`; le metriche GPU sono disponibili per CUDA/XPU quando PyTorch le supporta.

  ```json
  {
    "rss_bytes": 314572800,
    "vms_bytes": 1073741824,
    "gpu_allocated_bytes": 0,
    "gpu_reserved_bytes": 0,
    "loaded_models": {"stt": true, "llm": false, "tts": true, "embedding": false},
    "idle_timeouts": {"stt": 300, "llm": 180, "tts": 300}
  }
  ```

### `DownloadModel(provider: string, model: string) → boolean`
Avvia in un thread dedicato in background lo scaricamento del modello specificato, attivando l'inibitore di sospensione del sistema.
- **Ritorno**: `true` se il download è stato avviato.

### `CancelDownload(provider: string, model: string) → boolean`
Annulla il download in corso per il modello indicato, sblocca gli inibitori di sospensione e rimuove le cartelle parziali dal disco.
- **Ritorno**: `true` se un download corrispondente era in corso ed è stato annullato.

### `ShowWindow()`
Avvia la GUI standalone (`gui/start.sh`) come subprocess separato. Se la GUI è già aperta, GApplication la porta in primo piano automaticamente senza aprire una seconda finestra.

### `ProcessTextInput(text: string)`
Elabora il testo inviato dalla GUI in modalità silenziosa (`speak=False`): la pipeline esegue Fast-Path → Smart-Path → LLM senza TTS. I token vengono trasmessi in tempo reale via il segnale `ResponseTokenStreamed`.

### `TriggerListening()`
Simile a `ToggleListening()`, ma forza la transizione allo stato `listening` senza toggle. Utile per attivazioni programmatiche (es. da keybinding).
- **Ritorno**: `true` se l'assistente è passato in ascolto.

### `TriggerListeningInContext(context_id: string) → boolean`
Forza la transizione allo stato `listening` associando la trascrizione e l'elaborazione del comando vocale al `context_id` indicato.
- **Input**: `context_id: string` (es. UUID della chat attiva).
- **Ritorno**: `true` se l'assistente è passato in ascolto.

### `GetState() → string`
Ritorna lo stato corrente del daemon come stringa.
- **Valori possibili**: `"disabled"`, `"idle"`, `"listening"`, `"processing"`, `"speaking"`, `"downloading"`, `"unavailable"`.

### `DeleteModel(provider: string, model: string) → boolean`
Cancella dal disco un modello precedentemente scaricato.
- **Ritorno**: `true` se la cancellazione è avvenuta con successo.

### `GetErrorReports() → string (JSON)`
Ritorna la lista dei report di errore raccolti dal daemon in formato JSON.

### `ClearErrorReports() → boolean`
Cancella tutti i report di errore memorizzati.
- **Ritorno**: `true` se la cancellazione è andata a buon fine.

### `GenerateDiagnosticBundle() → string`
Genera un bundle diagnostico compresso (`.tar.gz`) contenente log, report di errore, configurazione e informazioni ambientali.
- **Ritorno**: il percorso assoluto del file `.tar.gz` generato.

> [!NOTE]
> `GetMarketplaceFeatured`, `SearchMarketplace`, `GetServerDetails`, `GetMarketplaceCategories`, `FilterMarketplaceByCategory` **non sono metodi D-Bus reali** (vedi nota dopo il blocco XML sopra) — rimossi da questa sezione.

### `InstallMCPServer(server_name: string, server_config: string, env_vars: string = "")`
Installa un server MCP esterno, verificandone la disponibilità del comando e testandone l'avvio. `server_config`/`env_vars` sono stringhe JSON.
- **Ritorno**: nessuno (il metodo Python non ha un'annotazione di tipo di ritorno, quindi dasbus non genera un `out` argument nonostante l'implementazione calcoli internamente un booleano di successo).

### `StartMCPServer(server_name: string)`
Avvia un server MCP precedentemente installato.
- **Ritorno**: nessuno (stesso motivo di `InstallMCPServer`).

### `UninstallMCPServer(server_name: string)`
Disinstalla un server MCP. I server built-in (es. `gnome-mcp-server`) sono protetti dalla disinstallazione.
- **Ritorno**: nessuno.

### `TestMCPServer(server_name: string)`
Verifica che un server MCP installato sia funzionante avviandolo con timeout di test.
- **Ritorno**: nessuno.

### `UpdateServerConfig(server_name: string, env_vars: string, enabled: boolean)`
Aggiorna la configurazione di un server MCP (variabili d'ambiente JSON e stato abilitato/disabilitato).
- **Ritorno**: nessuno.

### `GetInstalledServers() → string (JSON)`
Ritorna la lista dei server MCP installati e la loro configurazione in formato JSON.

### `OpenSettings()`
Apre la finestra delle impostazioni dell'assistente.

### `GetMissingDependencies() → string (JSON)`
Ritorna la lista delle dipendenze mancanti (pacchetti di sistema, pip e MCP) in formato JSON, usata dalla GUI per mostrare il dialogo di installazione.

### `GetSpeakerProfiles() → string (JSON)`
Ritorna in formato JSON la lista dei profili vocali registrati (`[{"id": "...", "display_name": "...", "created_at": "...", "updated_at": "...", "needs_reenroll": false}]`).

### `StartSpeakerEnrollment(display_name: string, duration_s: double) → boolean`
Avvia una sessione di registrazione dell'impronta vocale per il parlante specificato. Ritorna `false` se una registrazione è già in corso o il backend di embedding non è disponibile (emettendo `SpeakerEnrollmentFinished(false, "", reason)`).

### `CancelSpeakerEnrollment() → boolean`
Annulla la registrazione dell'impronta vocale in corso ed emette `SpeakerEnrollmentFinished(false, "", "cancelled")`.

### `DeleteSpeakerProfile(profile_id: string) → boolean`
Elimina definitivamente un profilo vocale memorizzato su disco. Ritorna `true` se il profilo è stato eliminato con successo.

---

## Dettaglio Segnali

### `StateChanged(new_state: string)`
Emesso ad ogni transizione di stato del daemon.
- **Valori possibili**: `"disabled"`, `"idle"`, `"listening"`, `"processing"`, `"speaking"`, `"downloading"`, `"unavailable"`.

### `DownloadProgress(provider: string, model: string, percent: int)`
Emesso in tempo reale dal thread di monitoraggio durante il download di un modello.
- **Range**: `percent` compreso tra `0` e `100`.

### `TranscriptReceived(text: string, is_final: bool)`
Emesso durante il riconoscimento vocale (STT).
- `is_final=False`: testo parziale instabile (in aggiornamento mentre l'utente parla).
- `is_final=True`: frase completa riconosciuta.
- La GUI mostra il messaggio dell'utente nella chat solo quando `is_final=True`.

### `ResponseTokenStreamed(token: string, is_complete: bool)`
Emesso durante la generazione della risposta.
- `is_complete=False`: token LLM intermedio da appendere nella bolla di risposta.
- `is_complete=True, token=""`: fine dello stream LLM — chiude la bolla aperta.
- `is_complete=True, token!=""`: risposta fast-path completa in un singolo segnale (nessuno stream precedente).

### `DependencyRequired(package: string, description: string, is_critical: boolean)`
Emesso quando il daemon rileva una dipendenza mancante nel sistema.
- `package`: nome del pacchetto mancante.
- `description`: descrizione leggibile della dipendenza.
- `is_critical`: `true` se la dipendenza è essenziale per il funzionamento base.

### `SpeakerEnrollmentProgress(progress: double, level: double)`
Emesso periodicamente durante la registrazione vocale dell'utente.
- `progress`: avanzamento temporale normalizzato (0.0–1.0).
- `level`: ampiezza RMS istantanea dell'audio del microfono normalizzata (0.0–1.0).

### `SpeakerEnrollmentFinished(success: boolean, profile_id: string, message: string)`
Emesso al termine della sessione di registrazione vocale.
- `success`: `true` se l'impronta vocale è stata registrata con successo.
- `profile_id`: ID univoco del profilo registrato o aggiornato.
- `message`: codice esito (`"enrolled"`, `"cancelled"`, `"insufficient_speech"`, `"unavailable"`, `"no_audio"`, `"timeout"`).

### `SpeakerIdentified(name: string, score: double, status: string, overlap: boolean)`
Emesso ad ogni verifica del parlante completata con successo o informativamente.
- `name`: nome visualizzato del parlante riconosciuto.
- `score`: punteggio di similarità del coseno rispetto al profilo (0.0–1.0).
- `status`: esito della classificazione (`"recognized"`, `"unknown"`, `"insufficient_audio"`, `"no_profiles"`, `"unavailable"`).
- `overlap`: `true` se è stata rilevata una sovrapposizione di voci concorrenti durante l'enunciato.

### `SpeakerRejected(reason: string, message: string)`
Emesso in modalità `gate` quando una richiesta vocale viene respinta dalle policy di sicurezza.
- `reason`: motivazione tecnica del rifiuto (`"unknown"`, `"overlap"`, `"insufficient_audio"`, `"no_profiles"`, `"unavailable"`, `"timeout"`).
- `message`: messaggio localizzato esplicativo inviato al motore TTS.

### `ConversationCreated(context_id: string, reason: string)`
Emesso quando viene creata una nuova conversazione.
- `context_id`: UUID della nuova conversazione creata.
- `reason`: motivo della creazione (`"user"`, `"deep_dive"`).

### `ConversationToken(context_id: string, token: string, is_complete: boolean)`
Emesso per indirizzare lo streaming di token alla chat specifica di appartenenza.
- `context_id`: UUID della conversazione cui appartengono i token.
- `token`: frammento di testo generato dall'LLM (o risposta fast-path se `is_complete=True` e `token!=""`).
- `is_complete`: `true` al termine della generazione dello stream.

### `ConversationTranscript(context_id: string, text: string, is_final: bool)`
Emesso per registrare la trascrizione dell'utente nella conversazione specifica.
- `context_id`: UUID della conversazione o `"voice"`.
- `text`: testo del messaggio inviato dall'utente.
- `is_final`: `true` se la trascrizione è definitiva.

---

## Test e Invocazione da CLI

```bash
# Invocare ToggleListening
gdbus call --session \
  --dest org.local.VoiceAssistant \
  --object-path /org/local/VoiceAssistant \
  --method org.local.VoiceAssistant.ToggleListening

# Ottenere i modelli Vosk installati
gdbus call --session \
  --dest org.local.VoiceAssistant \
  --object-path /org/local/VoiceAssistant \
  --method org.local.VoiceAssistant.GetAvailableModels "vosk"

# Ottenere le metriche runtime di memoria
gdbus call --session \
  --dest org.local.VoiceAssistant \
  --object-path /org/local/VoiceAssistant \
  --method org.local.VoiceAssistant.GetResourceMetrics

# Monitorare i segnali D-Bus in tempo reale
gdbus monitor --session \
  --dest org.local.VoiceAssistant \
  --object-path /org/local/VoiceAssistant
```

### Conversazioni
- `CreateConversation(title: String) -> String (context_id)`
- `ListConversations() -> String (JSON)`
- `DeleteConversation(context_id: String) -> Boolean`
- `GetConversationMessages(context_id: String) -> String (JSON)`
- `ProcessTextInContext(text: String, context_id: String)`
- `ToggleListeningInContext(context_id: String) -> Boolean`
- `TriggerListeningInContext(context_id: String) -> Boolean`
