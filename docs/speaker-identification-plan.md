# Piano di implementazione: riconoscimento del parlante (Speaker ID)

> **Destinatario:** Gemini (implementazione). **Review:** Claude.
> **Stato:** da implementare. Leggere tutto il documento prima di scrivere codice.
> **Riferimento sperimentale:** `experiments/test.py` (classe `DynamicSpeakerIdentification`, profilo di esempio `experiments/Giorgio.npz`).

---

## 0. Prima di iniziare

### 0.1 Base di partenza
Si lavora su **`main`**. Il piano è stato verificato sul commit `6200f3c` ("remove Medium-Path") **più** le modifiche non committate presenti nel checkout al 2026-09-13. Quelle modifiche contengono il lavoro multi-chat in corso:
- `services/conversation_contexts.py`;
- `process_text_input(text, speak, context_id)`;
- coda delle richieste `enqueue_request` in `assistant_runtime.py`;
- metodi `*Conversation*` in `main.py`.

Regole:
- Lavora **sopra** quelle modifiche, senza tornare alle firme precedenti.
- Il lavoro multi-chat tocca `trigger_assistant()`, `enqueue_request()` e `_process_text()`, quindi mantieni i punti di aggancio descritti qui (§4.4, §5.1) piccoli e isolati.
- Il **Medium-Path non esiste più**: la pipeline è Fast-Path → Smart-Path → percorso di riserva LLM.

### 0.2 Punti da confermare con l'utente (default proposti)
Implementa i default qui sotto. Ognuno deve restare facile da cambiare: una costante, oppure un ramo isolato del codice.

| # | Domanda | Default proposto |
|---|---|---|
| D1 | Motore di embedding | **resemblyzer**, già validato nell'esperimento con soglia 0.75. Sta dietro un'interfaccia (§4.1). ⚠️ Richiede `torch`, qualche centinaio di MB. L'alternativa leggera `sherpa_onnx.SpeakerEmbeddingExtractor` è già tra le dipendenze: la aggiungiamo in un secondo momento. |
| D2 | A quali input si applica la barriera | Tutti gli input **vocali**: wakeword, scorciatoia, microfono della GUI. Il testo **digitato** nella GUI è escluso. |
| D3 | Cosa succede quando la barriera rifiuta | Messaggio breve localizzato ("Non ho riconosciuto la tua voce."). Il testo rifiutato **non** entra nella memoria della conversazione e non arriva all'LLM. |
| D4 | Voce estranea sovrapposta (§5.3) | Modalità informativa: solo log e metadati. Modalità barriera: la richiesta viene rifiutata. |
| D5 | Barriera attiva senza profilo registrato o senza dipendenze | **Fail-closed**: le richieste vocali vengono rifiutate e parte una notifica che spiega come risolvere. La GUI impedisce di scegliere "Barriera" se non esiste un profilo. |
| D6 | Parole di stop ("stop", "basta"…) | Funzionano sempre, anche in modalità barriera: fermare l'assistente non è pericoloso. |
| D7 | Numero di profili | Il backend ne gestisce più di uno. La UI di questa versione ne gestisce **uno** (registra, registra di nuovo, elimina). In modalità barriera è autorizzato qualunque profilo registrato. |

---

## 1. Obiettivo

1. Aggiungere un'impostazione **Riconoscimento vocale** con tre modalità:
   - `disabled`: nessuna elaborazione (default);
   - `informative`: il daemon identifica chi parla e passa il nome come contesto all'LLM, **senza mai bloccare**;
   - `gate`: una richiesta vocale viene eseguita **solo** se chi parla corrisponde a un profilo registrato.
2. Mettere l'impostazione in **Generale → Filtri audio** (sottopagina `audio_subpage`), in un gruppo dedicato.
3. Creare un **layout di registrazione** del timbro vocale: una pagina con un testo da leggere, un pulsante per avviare la registrazione, l'avanzamento e l'esito.
4. Controllare le **sovrapposizioni**: se la wakeword riconosce l'utente, il riconoscimento continua anche durante l'ascolto della frase. Così si scoprono porzioni della frase pronunciate da un'altra voce.

## 2. Fuori perimetro (non implementare)
- Barriera **per singolo tool** (tool `sensitive` con override in GSettings). È la fase successiva, già decisa: vedi §9.
- Separare due voci che parlano contemporaneamente. Con gli embedding si capisce solo quale voce **domina** una finestra audio.
- Anti-spoofing o rilevamento di registrazioni riprodotte. Va scritto chiaramente nella UI (§6.2).
- Portare in produzione il Silero VAD e il filtro passa-banda dell'esperimento. La pipeline di produzione ha già i suoi filtri.

---

## 3. Come funziona oggi (vincoli reali del codice)

- **Acquisizione audio.** `main.py: audio_callback` mette in `q` blocchi `bytes` **PCM int16 mono a 16 kHz, 8000 campioni, cioè 0,5 s ciascuno** (`core/audio_runtime.py`, `blocksize=8000`).
- **Ciclo audio.** È `AssistantRuntimeController._audio_loop` in `core/assistant_runtime.py`:
  1. `raw_data = q.get()`;
  2. `data = audio_filter.process(raw_data)`, cioè passa-alto, AGC e noise gate;
  3. se `time.time() < _ignore_audio_until` salta il blocco (serve durante il suono di conferma dopo la wakeword);
  4. negli stati `idle`, `speaking` e `processing` cerca la wakeword con Vosk, OpenWakeWord o Sherpa, poi chiama `trigger_assistant()`;
  5. nello stato `listening` usa `provider.process_chunk(data)` e, alla fine, `enqueue_request(text, is_voice=True, context_id="voice")`. Si esce dall'ascolto per timeout di 2,5 s o 6 s, per trascrizione vuota o per sola wakeword.
- **Avvio dell'ascolto.** `trigger_assistant()` viene chiamato dalla wakeword, da `ToggleListening` e da `TriggerListening`, che servono alla scorciatoia, all'estensione e alla GUI. **Oggi non sa chi l'ha avviato.**
- **Elaborazione del testo.** `enqueue_request(text, is_voice, context_id)` mette la richiesta in una coda con priorità. Un worker thread la preleva e chiama `_process_text(text, is_voice, context_id)`, che a sua volta chiama `pipeline_controller.process_text_input(...)`. **`_process_text` quindi non gira nel thread audio.**
- **LLM.** `LLMServiceManager.stream_tokens(prompt, history=None)` e `get_config()` **non** hanno ancora un parametro per iniettare testo nel placeholder `{context}` del prompt di sistema: oggi `apply_prompt_template(..., context="")`. Va aggiunto (§5.2).
- **Impostazioni GUI.**
  - I file Blueprint modulari stanno in `data/ui/prefs/*.blp` e vengono uniti da `scripts/assemble_blueprints.py` nel file `data/ui/prefs.blp`, compilato poi in `data/ui/prefs.ui`.
  - La logica sta in `src/gui/components/settings/*.py`; la sottopagina Filtri è `audio.py` (`AudioSettings`).
  - `prefs.js` si limita ad avviare la GUI Python con `--open-settings`, quindi **tutta la UI va fatta in Python**.
- **D-Bus.** La definizione è in `data/dbus/org.local.VoiceAssistant.xml`, con una **copia** in `src/extension.js`, intorno alla riga 157. Il server usa dasbus (`main.py`) e il client GUI è `src/gui/components/daemon_client.py`.
- **Dipendenze opzionali.** Sono elencate in `data/dependencies/python_deps.json`. Il daemon le segnala con `notify_dependency_required(...)`, che emette il segnale `DependencyRequired`.
- **Modelli caricati in memoria.** Si usa `model_manager.register_instance(kind, instance, unload_callback)`, dove `kind` richiede un attributo `{kind}_instance` in `core/model_manager.py`.
- **Percorsi.** `core/path_utils.get_data_dir()` restituisce `~/.local/share/voice-assistant`.

---

## 4. Architettura del daemon

Crea il pacchetto `src/daemon/services/speaker_id/`. Nessun modulo del pacchetto deve importare GLib o dasbus, così resta testabile.

```
services/speaker_id/
  __init__.py          # esporta le API pubbliche
  backend.py           # interfaccia SpeakerEmbeddingBackend + create_backend()
  resemblyzer_backend.py
  profiles.py          # SpeakerProfileStore
  identifier.py        # SpeakerIdentifier (worker thread, match)
  session.py           # VoiceSpeakerSession (pre-roll, finestre, verdetto)
  enrollment.py        # EnrollmentRecorder
  policy.py            # SpeakerPolicy (disabled / informative / gate)
```

### 4.1 `backend.py`
```python
class SpeakerEmbeddingBackend(Protocol):
    name: str
    sample_rate: int            # 16000
    min_samples: int            # campioni minimi dopo il preprocessing (0,4 s nell'esperimento)
    def is_available(self) -> bool: ...        # import riuscito
    def load(self) -> None: ...                # caricamento pigro del modello
    def unload(self) -> None: ...
    def embed(self, pcm: np.ndarray) -> np.ndarray | None:
        """pcm float32 in [-1, 1] a 16 kHz. Restituisce un embedding L2-normalizzato,
        oppure None se l'audio utile (dopo il trimming del silenzio) è < min_samples."""
```
- `create_backend(name="resemblyzer")` restituisce l'istanza. Gli import di `resemblyzer` e `torch` avvengono **solo** dentro `load()` e `is_available()`, mai quando il modulo viene importato.
- `resemblyzer_backend.py`: `preprocess_wav(pcm, source_sr=16000)`, poi `VoiceEncoder().embed_utterance(...)` e infine la normalizzazione L2. È la stessa logica dell'esperimento. Il modello va caricato sulla CPU.

### 4.2 `profiles.py`: `SpeakerProfileStore`
- Cartella: `get_data_dir() / "speakers"`. Un file per profilo: `<profile_id>.npz`, con le chiavi `anchor` (256 float), `history` (N×256) e `meta` (JSON serializzato come stringa: `display_name`, `created_at`, `updated_at`, `backend`, `embedding_dim`).
- `profile_id` è uno slug sicuro per il filesystem generato dal nome: niente `/`, niente `..`, `[a-z0-9_-]+`. Il nome visibile resta nei `meta`.
- API:
  - `list() -> list[dict]`
  - `get(id)`
  - `save_enrollment(display_name, anchor)`, che **sostituisce** il profilo esistente e azzera la history
  - `delete(id)`
  - `add_history(id, emb)`
  - `reference_embedding(id)`
- `reference_embedding` fa la stessa cosa di `get_reference_embedding` nell'esperimento: media pesata della history con `np.linspace(0.4, 1.0)` e combinazione 50/50 con l'anchor, poi normalizzazione.
- `add_history` tiene al massimo `max_history=19` embedding.
- **Scrittura atomica.** Scrivi su un file temporaneo nella stessa cartella e poi usa `os.replace`. Protezione con `threading.Lock`.
- **Compatibilità.** Se `backend` nei meta è diverso dal backend attivo, il profilo va ignorato e segnalato come "da registrare di nuovo" nell'elenco (`needs_reenroll: true`).

### 4.3 `identifier.py`: `SpeakerIdentifier`
- Riceve `backend`, `store`, `threshold` (default 0.75) e `margin` (default 0.10).
- `match(embedding) -> SpeakerMatch`, un dataclass con `profile_id | None`, `display_name | None`, `score: float` e `status`. `status` può valere:
  - `recognized`: `score >= threshold`;
  - `unknown`: `score < threshold`;
  - `insufficient_audio`: `embed` ha restituito `None`;
  - `no_profiles`;
  - `unavailable`: backend mancante o in errore.
- **Worker thread** con coda (`submit(pcm, callback)`), come nell'esperimento. Il `_audio_loop` **non deve mai** calcolare embedding direttamente.
- **Coda limitata.** Se contiene già più di 2 finestre di sovrapposizione in attesa, scarta quelle nuove. Le richieste di verdetto finale non si scartano mai.
- **Adattamento della history.** Aggiungi l'embedding **solo** se il verdetto finale su una frase completa ha `score >= threshold + 0.05`. Non farlo **mai** con le finestre di sovrapposizione e **mai** con un verdetto che contiene una sovrapposizione.
- **Scaricamento dalla memoria.** Registra il modello in `model_manager` con il nuovo tipo `"speaker"`: aggiungi `speaker_instance` in `core/model_manager.py`, insieme al timeout di inattività se lo schema lo prevede per gli altri tipi.

### 4.4 `session.py`: `VoiceSpeakerSession`
Rappresenta **una** sessione di ascolto vocale.
- **Pre-roll globale.** È un ring buffer di audio **grezzo** (`raw_data`, prima di `audio_filter`) lungo 2,0 s, cioè 4 blocchi. Lo alimenta `_audio_loop` negli stati `idle`, `speaking` e `processing`, dopo il controllo su `_ignore_audio_until`.
  - **Perché grezzo?** Il noise gate (attenuazione 0.3) e l'AGC cambiano con le impostazioni dell'utente e distorcerebbero gli embedding. Registrazione e identificazione devono usare la stessa sorgente. L'AEC è di sistema, quindi è già applicato.
- **`start(origin)`.** `origin` può valere:
  - `wakeword`: la sessione copia il pre-roll, che contiene la wakeword, e manda subito al worker l'**identificazione alla wakeword**;
  - `manual`: scorciatoia, estensione o microfono della GUI. Il pre-roll **non** va usato, perché contiene audio della stanza precedente al clic.
- **`feed(raw_chunk)`.** Viene chiamato per ogni blocco nello stato `listening` e accumula l'audio della frase.
- **Controllo delle sovrapposizioni.** Si attiva **solo se** l'identificazione alla wakeword ha restituito `recognized`. Vedi §5.3.
- **`finalize(timeout_s) -> SpeakerVerdict`.** Unisce pre-roll e frase, manda al worker il verdetto finale e aspetta al massimo `timeout_s` (1,5 s). Restituisce un `SpeakerVerdict`, un dataclass con:
  - `match: SpeakerMatch` sulla frase completa;
  - `wakeword_match: SpeakerMatch | None`;
  - `overlap_detected: bool` e `foreign_windows: int`;
  - `origin`;
  - `timed_out: bool`.
- **`cancel()`.** Va chiamato in **tutti** i casi in cui si torna in idle senza elaborare: timeout di 2,5 s, trascrizione vuota o con la sola wakeword, cambio della wakeword, `ToggleListening` che interrompe, `cancel_pipeline`.

### 4.5 `policy.py`: `SpeakerPolicy`
```python
@dataclass
class SpeakerDecision:
    allow: bool
    reason: str            # "disabled" | "recognized" | "unknown" | "overlap" | "no_profiles" | "unavailable" | "insufficient_audio" | "timeout" | "typed_input" | "stop_command"
    speaker_name: str | None
    llm_context: str       # testo da iniettare nel {context} dell'LLM ("" se nulla)

def evaluate(mode, verdict: SpeakerVerdict | None, *, is_voice: bool, is_stop_command: bool) -> SpeakerDecision
```
Regole: una funzione pura, da coprire tutta con i test.

| mode | Condizione | allow | llm_context |
|---|---|---|---|
| `disabled` | qualsiasi | ✅ | "" |
| qualsiasi | `not is_voice` (testo digitato, D2) | ✅ | "" |
| qualsiasi | `is_stop_command` (D6) | ✅ | "" |
| `informative` | `recognized` | ✅ | `"Parlante identificato: {name}."` (localizzato) |
| `informative` | qualsiasi altro stato | ✅ | "" |
| `gate` | `recognized` e `not overlap_detected` | ✅ | come in informative |
| `gate` | `recognized` e `overlap_detected` (D4) | ❌ `overlap` | — |
| `gate` | `unknown`, `insufficient_audio` o `timed_out` | ❌ | — |
| `gate` | `no_profiles` o `unavailable` (D5) | ❌ | — |

Le stringhe localizzate stanno in `data/locales/responses.json`, sotto una nuova sezione `speaker_id`. Servono per il contesto e per i messaggi di rifiuto, con un messaggio per ogni `reason`. `no_profiles` e `unavailable` devono dire all'utente cosa fare, per esempio "registra la tua voce nelle impostazioni".

### 4.6 `enrollment.py`: `EnrollmentRecorder`
- La registrazione la fa il **daemon**, che possiede già il microfono. La GUI si limita a pilotarla via D-Bus.
- `start(display_name, duration_s=8.0)`:
  - il daemon imposta `owner._speaker_enrollment_active = True`;
  - `_audio_loop`, finché il flag è attivo, manda i blocchi grezzi al recorder e **salta** la wakeword e lo STT;
  - lo stato dell'assistente non cambia; se era `listening`, `processing` o `speaking`, prima si annulla la pipeline e si torna in idle.
- **Avanzamento.** A ogni blocco calcola il livello RMS normalizzato 0..1 e l'avanzamento (tempo registrato / durata) ed emette `SpeakerEnrollmentProgress`.
- **Fine della registrazione.** Si calcola l'embedding (nel worker, non nel ciclo audio). Serve almeno **4 s di parlato utile**, stimato da `min_samples` dopo il trimming del backend; altrimenti la registrazione fallisce con "Parlato insufficiente, riprova in un ambiente silenzioso". Se va bene, chiama `store.save_enrollment(...)` ed emette `SpeakerEnrollmentFinished(true, profile_id, "")`.
- **Annullamento.** `cancel()` scarta l'audio ed emette `SpeakerEnrollmentFinished(false, "", "cancelled")`.
- **Timeout di sicurezza.** Se nessun blocco arriva entro `duration_s + 3` s, cioè il microfono è fermo, la registrazione fallisce con un messaggio chiaro.

---

## 5. Come si integra con il runtime

### 5.1 Modifiche a `core/assistant_runtime.py`
1. **Punto di aggancio in `trigger_assistant(origin="manual")`.**
   - La chiamata dal ciclo della wakeword (Vosk, `_check_oww_wakeword`, `_check_sherpa_wakeword`) passa `origin="wakeword"`.
   - `ToggleListening` e `TriggerListening` in `main.py` passano `origin="manual"`.
   - Se la modalità non è `disabled`, crea `owner._speaker_session = VoiceSpeakerSession(...)` e chiama `start(origin)`.
   - ⚠️ Crea la sessione **prima** di svuotare la coda, ma copia il pre-roll dal ring buffer, non dalla coda.
2. **`_audio_loop`:**
   - alimenta il ring buffer del pre-roll con `raw_data` negli stati idle, speaking e processing;
   - nello stato `listening` chiama `session.feed(raw_data)`;
   - quando `_speaker_enrollment_active` è vero, manda i blocchi al recorder e fa `continue`;
   - in ogni ramo che torna in idle senza elaborare, chiama `session.cancel()` e poi azzera la sessione (§4.4).
3. **Consegna della sessione alla coda.** Quando `_audio_loop` chiama `enqueue_request(..., is_voice=True, ...)`, **stacca** la sessione da `owner._speaker_session` e la mette nella tupla della coda. Aggiungi a `enqueue_request` il parametro facoltativo `speaker_session=None`.
   - **Perché:** il worker elabora la richiesta più tardi, magari quando è già iniziata una nuova sessione di ascolto. Con la sessione nella tupla, verdetto e richiesta restano legati.
   - Mai chiamare `finalize()` nel thread audio.
4. **`_process_text(text, is_voice=False, context_id="voice", speaker_session=None)`**, nel thread worker:
   - Se `is_voice` e c'è una sessione:
     1. `verdict = speaker_session.finalize(1.5)`;
     2. `decision = SpeakerPolicy.evaluate(...)`.
   - Se `is_voice` ma la sessione non c'è (modalità diversa da `disabled`), passa `verdict=None`: in `gate` la policy lo tratta come `unavailable` (fail-closed).
   - Per `is_stop_command` usa la stessa lista di parole di stop di `_audio_loop`. Metti la lista in **una sola** costante condivisa, invece di duplicarla.
   - Se `not decision.allow`:
     - niente pipeline e niente memoria;
     - emetti `SpeakerRejected(reason, message)`;
     - se `is_voice` e la risposta va letta, di' il messaggio con TTS (`tts_manager.speak`);
     - torna in idle.
   - Se è permesso, passa `decision.llm_context` alla pipeline (§5.2).
   - Emetti **sempre** `SpeakerIdentified(name_or_empty, score, status, overlap)` quando esiste un verdetto, per la diagnostica e la GUI futura.
5. **`on_settings_changed`.** Gestisci `speaker-id-mode` e `speaker-id-threshold`:
   - aggiorna la soglia a caldo;
   - con `disabled`, scarica il modello con `unload()` e annulla la sessione;
   - con una modalità diversa da `disabled` e le dipendenze mancanti, chiama `notify_dependency_required("resemblyzer", ..., is_critical=False)`.

### 5.2 Iniezione nella pipeline
- **`services/llm_service.py`:**
  - `get_config(context: str = "")` passa `context` ad `apply_prompt_template`;
  - `LLMServiceManager.stream_tokens(prompt, history=None, context="")` chiama `get_config(context=context)`.
  - Il valore arriva così al prompt di sistema in tutti i rami: GGUF locale, Anthropic, OpenAI-compatibile.
- **`core/runtime_manager.py`.** La lambda `llm_streamer` deve inoltrare i kwargs: `lambda prompt, **kw: llm_service.stream_tokens(prompt, **kw)`.
- **`core/pipeline.py`.** Aggiungi `extra_context: str = ""` a `process_text_input(...)`:
  - **Smart-Path:** passalo a `execute_smart_path(..., extra_context=...)`, che lo inoltra a `llm_streamer(user_message, history=..., context=extra_context)`;
  - **percorso di riserva:** passa `context=extra_context`;
  - **Fast-Path:** lo ignora.
  - Mantieni i `try/except TypeError` già presenti per gli streamer che non accettano kwargs, aggiungendo `context` alla prima chiamata.
- **Il nome del parlante non va salvato** nel testo del messaggio utente in memoria: al massimo nei `metadata`.

### 5.3 Controllo delle sovrapposizioni
- È attivo solo se `wakeword_match.status == recognized`, e quindi solo con `origin="wakeword"`.
- **Finestre.** Sono di **1,5 s con passo di 0,5 s** (3 blocchi, avanzamento di un blocco). Ogni finestra viene inviata al worker solo se è "parlata": RMS della finestra grezza > `max(150, 2 × rumore di fondo)`. Il rumore di fondo si legge da `owner.audio_filter._noise_floor`, oppure con un getter pubblico da aggiungere, meglio.
- **Classificazione di ogni finestra** rispetto a `reference_embedding` del profilo riconosciuto:
  - `match`: sim ≥ threshold;
  - `foreign`: sim < threshold − margin;
  - `uncertain`: tutto il resto.
- **Sovrapposizione.** `overlap_detected = True` se ci sono **≥ 2 finestre `foreign` consecutive**. Metti la costante in `session.py`.
- La frase completa si valuta comunque con `finalize`. Nella modalità `gate` il verdetto richiede **sia** `recognized` sulla frase completa **sia** `not overlap_detected`.
- Dichiara il limite nel codice e nella documentazione: la tecnica rileva tratti **dominati** da un'altra voce, non voci davvero simultanee.

### 5.4 Prestazioni
- `_audio_loop` resta O(1) per blocco: solo copie di buffer e inserimento in coda.
- Il modello si carica pigramente al primo uso, oppure all'avvio se `mode != disabled`, in un thread in background.
- In modalità `gate`, finché il modello non è pronto, il verdetto è `unavailable`, cioè fail-closed, con un messaggio del tipo "riconoscimento vocale in avvio".

---

## 6. Impostazioni e UI

### 6.1 GSettings (`data/schemas/org.gnome.shell.extensions.voice-assistant.gschema.xml`)
Aggiungi le chiavi vicino alle altre `audio-*`:

| Chiave | Tipo | Default | Note |
|---|---|---|---|
| `speaker-id-mode` | `s` con `<choices>` `disabled`, `informative`, `gate` | `'disabled'` | L'elenco di scelte deve restare estendibile (§9). |
| `speaker-id-threshold` | `d` con `<range min="0.5" max="0.95">` | `0.75` | Soglia di similarità coseno. |

Documenta entrambe in `docs/gsettings.md`, con lo stesso formato a tabella delle altre chiavi.

### 6.2 Gruppo nella sottopagina Filtri (`data/ui/prefs/subpage_audio.blp`)
Aggiungi un nuovo `Adw.PreferencesGroup` **prima** del gruppo con `audio_reset_btn`:

```
Adw.PreferencesGroup speaker_id_group {
  title: _("Voice Recognition");
  description: _("Identifies who is speaking from the voice timbre. It is not a security measure: a recording of your voice can pass the check.");

  Adw.ComboRow speaker_id_mode_row {
    title: _("Mode");
    // modello: Disabled / Informative only / Gate every request
  }

  Adw.SpinRow speaker_id_threshold_row { title: _("Match threshold"); subtitle: _("Higher values reject more, including your own voice in noisy rooms"); adjustment 0.50–0.95, step 0.01, digits 2 }

  Adw.ActionRow speaker_id_profile_row {
    title: _("Voice profile");
    subtitle: // "Not recorded" | "Recorded: <name>" | "Needs re-recording"
    activatable: true;
    [suffix] Gtk.Image go-next-symbolic
  }
}
```
- **Righe dei modi nel ComboRow**, con un sottotitolo per ciascuna:
  - "Disabilitato";
  - "Solo informativo — personalizza le risposte, non blocca nulla";
  - "Barriera — esegue solo le richieste vocali della tua voce".
- **Sensibilità.** `speaker_id_threshold_row` e `speaker_id_profile_row` sono insensibili quando la modalità è `disabled`: stesso schema di `_STAGE_DEPENDENCIES` in `audio.py`.
- **Profilo mancante.** Se l'utente sceglie `gate` e non esiste un profilo valido, rimetti il valore precedente e mostra un `Adw.AlertDialog`: "Registra prima la tua voce", con i pulsanti "Annulla" e "Registra ora". "Registra ora" apre la pagina di registrazione (D5).
- **`audio_reset_btn`** **non** deve toccare le chiavi `speaker-id-*` né i profili: il testo del dialog parla solo di filtri.

### 6.3 Pagina di registrazione (nuovo `data/ui/prefs/subpage_speaker_enrollment.blp`)
Registrala in `_SUBPAGE_ORDER` di `scripts/assemble_blueprints.py`, subito dopo `subpage_audio.blp`.

```
Adw.NavigationPage speaker_enrollment_subpage {
  tag: "speaker_enrollment_subpage";
  title: _("Record Your Voice");
  child: Adw.ToolbarView {
    [top] Adw.HeaderBar {}
    content: Adw.Clamp { maximum-size: 560; child: Gtk.Box (vertical, spacing 18, margin 24) {
      Gtk.Label enrollment_intro_label        // "Leggi il testo ad alta voce, con il tono di sempre, a circa 50 cm dal microfono."
      Adw.EntryRow / Gtk.Entry enrollment_name_entry   // default: GLib.get_real_name()
      Gtk.Frame { Gtk.Label enrollment_text_label { wrap: true; selectable: false; styles ["title-3"] } }
      Gtk.LevelBar enrollment_level_bar       // livello microfono in tempo reale
      Gtk.ProgressBar enrollment_progress_bar // avanzamento sulla durata
      Gtk.Label enrollment_status_label       // "Pronto" / "Registrazione…" / esito / errore
      Gtk.Box (horizontal, homogeneous) {
        Gtk.Button enrollment_record_btn { label: _("Start Recording"); styles ["suggested-action", "pill"] }
        Gtk.Button enrollment_cancel_btn { label: _("Cancel"); visible: false; styles ["pill"] }
      }
      Adw.PreferencesGroup { Adw.ButtonRow enrollment_delete_btn { title: _("Delete Voice Profile"); styles ["destructive-action"] } }
    }}
  };
}
```
- **Testo da leggere.** Sta in `data/locales/speaker_enrollment.json`, con le chiavi `{ "it": {...}, "en": {...} }`: un paragrafo foneticamente vario di circa 60–80 parole, lettura di circa 8 s. La lingua si sceglie con la logica già usata dalla GUI (`get_language_label` / lingua di sistema).
- **Stati della pagina:**
  - `idle`: pulsante "Avvia registrazione";
  - `recording`: pulsante disabilitato, "Annulla" visibile, barre attive;
  - `processing`: "Elaborazione…";
  - `done`: esito positivo e pulsante "Registra di nuovo";
  - `error`: messaggio e pulsante "Riprova".
- **Daemon non raggiungibile.** Pulsante insensibile e messaggio "Il servizio dell'assistente non è in esecuzione".
- **Elimina profilo.** Chiede conferma con `Adw.AlertDialog`. Se la modalità attuale è `gate`, dopo l'eliminazione la reimposta a `informative` e lo comunica.
- **Chiusura della pagina durante la registrazione.** Chiama `CancelSpeakerEnrollment`.

### 6.4 Logica GUI
- **Nuovo `src/gui/components/settings/speaker_id.py`** con `SpeakerIdSettings(builder, settings, parent_window, daemon_client)`.
  - Il binding della modalità si fa a mano: da indice del ComboRow a stringa, perché `bind_setting` non converte le stringhe enum. Si può aggiungere un helper in `base.py` se serve.
  - Navigazione verso `speaker_enrollment_subpage` con `push_subpage`, come in `general.py`.
  - Aggiorna il sottotitolo del profilo con `GetSpeakerProfiles` all'apertura, dopo `SpeakerEnrollmentFinished` e dopo l'eliminazione.
- **`settings_window.py`.** Oggi non ha un `DaemonClient`: creane uno per la finestra, pigro, e chiudilo in `destroy()`. Poi istanzia `SpeakerIdSettings` in `_setup_components`, accanto ad `AudioSettings`.
- **`daemon_client.py`.** Contiene già i metodi `*_conversation*_sync` del lavoro multi-chat: non modificarli.
  - aggiungi un meccanismo generico `subscribe(signal_name, callback) -> handler_id` / `unsubscribe`, invece di altri parametri nel costruttore, e usalo per i nuovi segnali;
  - aggiungi i metodi `start_speaker_enrollment(name, duration)`, `cancel_speaker_enrollment()`, `get_speaker_profiles(callback)` e `delete_speaker_profile(id, callback)`;
  - le callback arrivano sul main loop GTK (`GLib.idle_add`, già usato da `call_async`).

---

## 7. API D-Bus

Aggiorna **tutti e tre** i punti: `data/dbus/org.local.VoiceAssistant.xml`, la copia XML in `src/extension.js` e `docs/dbus.md`.

> ⚠️ Nel checkout di `main` alla data del piano, `main.py` espone già `ListConversations`, `CreateConversation`, `DeleteConversation` e `GetConversationMessages`, ma l'XML non li dichiara ancora: è il lavoro multi-chat in corso. Aggiungi solo i membri qui sotto, senza toccare né rimuovere quelli delle conversazioni. Il test di coerenza tra le copie (§10.9) deve riguardare i membri `Speaker*`.

| Tipo | Nome | Firma | Note |
|---|---|---|---|
| metodo | `GetSpeakerProfiles` | `() → s` | JSON: `[{"id","display_name","created_at","updated_at","needs_reenroll"}]` |
| metodo | `StartSpeakerEnrollment` | `(s display_name, d duration_s) → b` | `false` se una registrazione è già in corso o il backend non è disponibile; in quel caso emette `SpeakerEnrollmentFinished(false, "", reason)` |
| metodo | `CancelSpeakerEnrollment` | `() → b` | |
| metodo | `DeleteSpeakerProfile` | `(s profile_id) → b` | |
| segnale | `SpeakerEnrollmentProgress` | `(d progress, d level)` | entrambi 0..1, circa 2 al secondo (uno per blocco) |
| segnale | `SpeakerEnrollmentFinished` | `(b success, s profile_id, s message)` | `message` è un codice (`cancelled`, `insufficient_speech`, `unavailable`, `no_audio`) che la GUI traduce |
| segnale | `SpeakerIdentified` | `(s name, d score, s status, b overlap)` | |
| segnale | `SpeakerRejected` | `(s reason, s message)` | |

- In `main.py` i metodi D-Bus delegano a un nuovo `SpeakerIdController`, per esempio in `core/speaker_runtime.py`, che tiene store, identifier, recorder e sessione. Non mettere logica nella classe D-Bus.
- Aggiungi gli attributi in `main.py.__init__` e in `core/daemon_protocol.py`.
- Crea il controller in `runtime_manager.py`, nel posto dove si inizializzano gli altri servizi.

---

## 8. Dipendenze, documentazione, dati
- **`data/dependencies/python_deps.json`.** Aggiungi `{"import_name": "resemblyzer", "package_name": "resemblyzer", "description": "Riconoscimento del parlante (timbro vocale)", "is_critical": false}`. Controlla che `torch` venga installato come dipendenza transitiva. Se l'installer lo richiede, aggiungi una voce `torch` separata.
- **Nessun import a livello di modulo** di `resemblyzer`, `torch` o `librosa` fuori da `resemblyzer_backend.py`, e lì solo dentro le funzioni.
- **Documentazione:**
  - `docs/pipeline.md`: una sezione "Speaker ID", con un diagramma mermaid del flusso wakeword → sessione → verdetto → policy;
  - `docs/architecture.md`: il nuovo pacchetto;
  - `docs/gsettings.md` e `docs/dbus.md`, come descritto sopra.
- **Stringhe GUI** dentro `_()`. Se il progetto aggiorna i file `po/`, aggiungi le nuove stringhe al POTFILES.
- **Profilo di esempio.** Non copiare `experiments/Giorgio.npz` nei dati del progetto.

---

## 9. Estendibilità verso la fase successiva (non implementare, ma non precludere)
La fase successiva, già decisa, prevede la barriera **per singolo tool**:
- ogni tool in `mcp/tools/*.py` ha una proprietà predefinita `sensitive: bool`;
- un dizionario di override in GSettings (`a{sb}`, nome del tool → bool) permette di alzare o abbassare la sensibilità di qualunque tool;
- la granularità è per tool, non per azione.

Per non doverla riscrivere dopo:
- `SpeakerPolicy.evaluate` deve poter ricevere più avanti un parametro facoltativo `tool_name`, senza cambiare il comportamento di oggi;
- il `SpeakerVerdict` deve poter essere passato alla pipeline, cioè sopravvivere fino al punto in cui si eseguono i tool, anche se oggi lo si usa solo prima;
- `speaker-id-mode` deve poter ricevere più avanti un nuovo valore, per esempio `gate-sensitive`, senza migrazioni.

---

## 10. Test (pytest, nella cartella `tests/`)
Usa un **backend finto** deterministico, per esempio un embedding ricavato da un seed e dall'intestazione dell'audio. Nessun test deve scaricare modelli o richiedere `torch`.

1. **`tests/test_speaker_profiles.py`:**
   - salvataggio e caricamento dei profili;
   - scrittura atomica (nessun file parziale se `np.savez` solleva un'eccezione);
   - `profile_id` sicuro (`"../x"`);
   - `reference_embedding` uguale alla formula dell'esperimento;
   - `max_history`;
   - `needs_reenroll` con un backend diverso.
2. **`tests/test_speaker_identifier.py`:** gli stati `recognized`, `unknown`, `insufficient_audio`, `no_profiles` e `unavailable`; la history aggiornata solo sopra threshold + 0.05 e mai con sovrapposizione; la coda delle finestre che scarta oltre il limite.
3. **`tests/test_speaker_policy.py`:** **tutta** la tabella di §4.5, una riga per caso.
4. **`tests/test_speaker_session.py`:**
   - pre-roll usato solo con `wakeword`;
   - sovrapposizione rilevata con 2 finestre `foreign` consecutive, e non con finestre alternate o `uncertain`;
   - finestre di silenzio non inviate;
   - `finalize` con timeout;
   - `cancel` idempotente.
5. **`tests/test_speaker_enrollment.py`:** avanzamento emesso, parlato insufficiente, annullamento, timeout senza blocchi.
6. **`tests/test_assistant_runtime.py`**, nuovi casi:
   - con `gate` e verdetto `unknown`, `_process_text` non chiama `process_text_input` ed emette `SpeakerRejected`;
   - `enqueue_request` porta la sessione fino a `_process_text`; una nuova `trigger_assistant()` avvenuta prima dell'elaborazione non la sostituisce;
   - con `informative` la pipeline riceve `extra_context` con il nome;
   - le parole di stop passano anche con `gate`;
   - `trigger_assistant(origin=...)` crea la sessione solo con `mode != disabled`;
   - i rami di timeout di `_audio_loop` chiamano `session.cancel()`;
   - durante la registrazione i blocchi non arrivano alla wakeword né allo STT.
7. **`tests/test_core_pipeline.py` e `tests/test_services_llm.py`:** `extra_context` arriva a `llm_streamer(..., context=...)` sia nello Smart-Path sia nel percorso di riserva; `stream_tokens(..., context="X")` mette "X" nel prompt di sistema (OpenAI-compatibile, Anthropic, locale).
8. **`tests/test_gui.py`:**
   - le righe `speaker_id_*` esistono;
   - la sensibilità segue la modalità;
   - scegliere `gate` senza profilo rimette il valore precedente;
   - la pagina di registrazione si apre;
   - i cambi di stato della pagina rispondono ai segnali simulati;
   - `test_blueprint_assembler` include la nuova sottopagina.
9. **`tests/test_schema_and_resources.py`:** chiavi e `choices` presenti; XML D-Bus e copia in `extension.js` **identici** per i nuovi membri. Se non esiste già un test di coerenza tra le due copie, aggiungilo.

Comando di verifica. Sul commit `330107b` la suite completa andava in crash (core dump) su un test GTK. Se succede ancora, fai girare almeno:
```
python -m pytest -q tests/test_speaker_*.py tests/test_assistant_runtime.py tests/test_core_pipeline.py \
  tests/test_services_llm.py tests/test_listening_loop_resilience.py tests/test_schema_and_resources.py tests/test_gui.py
```
Registra anche i nuovi file di test in `tests/meson.build`, come per gli altri.
Rigenera `data/ui/prefs.blp` con `scripts/assemble_blueprints.py` e compila `data/ui/prefs.ui` con `blueprint-compiler`. Committa entrambi i file generati, come gli altri.

---

## 11. Criteri di accettazione (verifica manuale)
1. **Modalità `disabled`:** nessun import di resemblyzer (controlla nel log o con `sys.modules`), nessun cambiamento di latenza.
2. **Registrazione:** lettura del testo, barra del livello che si muove, esito positivo, file creato in `~/.local/share/voice-assistant/speakers/`, sottotitolo del profilo aggiornato.
3. **Modalità `informative`:** "\<wakeword\>, come mi chiamo?" → la risposta usa il nome. Nel log compare `SpeakerIdentified` con `recognized`.
4. **Modalità `gate`:** un'altra persona o un video con un'altra voce → rifiuto con messaggio vocale, nessuna chiamata all'LLM (log). La voce dell'utente → eseguita.
5. **Modalità `gate` con sovrapposizione:** l'utente dice la wakeword e un'altra voce detta la richiesta → rifiuto `overlap`.
6. **"Stop"** detto da chiunque durante una risposta → interrompe.
7. **Senza profilo:** la GUI impedisce `gate`; forzando la chiave con `gsettings set`, le richieste vocali vengono rifiutate con il messaggio "registra la tua voce".
8. **Microfono della GUI e scorciatoia:** il pre-roll non viene usato (log). La barriera si applica alla frase.

## 12. Checklist per la review (cosa controllerà Claude)
- [ ] Nessun calcolo di embedding nel thread di `_audio_loop`; code limitate; nessuna crescita di memoria nelle sessioni lunghe.
- [ ] `session.cancel()` chiamato in **ogni** ramo che torna in idle; nessuna sessione rimasta da un ascolto precedente; `finalize()` mai chiamato nel thread audio.
- [ ] Fail-closed in `gate` per ogni stato che non sia `recognized` (tabella §4.5), anche per timeout ed eccezioni del worker.
- [ ] Testo rifiutato mai salvato in memoria o nel RAG, né inviato all'LLM.
- [ ] Audio grezzo usato in modo coerente sia per la registrazione sia per l'identificazione.
- [ ] Pre-roll usato solo con `origin="wakeword"`.
- [ ] History del profilo aggiornata solo con frasi complete sopra threshold + 0.05 e senza sovrapposizione.
- [ ] `profile_id` sicuro per il filesystem e scrittura atomica.
- [ ] Import pesanti solo pigri; `DependencyRequired` emesso quando serve.
- [ ] Tre copie dell'interfaccia D-Bus coerenti; `docs/*` aggiornati.
- [ ] Nessuna regressione nel lavoro multi-chat (`context_id`, coda `enqueue_request`, `history`, metodi `*Conversation*`); sessione consegnata attraverso la coda.
- [ ] `context` aggiunto a `stream_tokens`/`get_config` e arrivato al prompt di sistema in tutti i backend.
- [ ] Stringhe UI tradotte e avviso "non è una misura di sicurezza" presente.
