# Piano correttivo: multi-chat e riconoscimento del parlante

> **Destinatario:** Gemini 3.8 (implementazione). **Review:** Claude.
> **Base:** `main` (`6200f3c`) più le modifiche **non committate** del checkout al 2026-09-13.
> **Documenti di riferimento:**
> - `docs/speaker-identification-plan.md` (specifica del riconoscimento del parlante);
> - le decisioni dell'utente sul multi-chat, riassunte in §0.2.
>
> Questo piano **corregge** il lavoro esistente: non riscriverlo da zero. I numeri di riga sono quelli del 2026-09-13 e servono solo per orientarsi: cerca sempre il nome della funzione.

---

## 0. Regole di lavoro

### 0.1 Ordine e verifica
1. Esegui i blocchi **nell'ordine**: A (critici di sicurezza) → B (riconoscimento del parlante) → C (multi-chat) → D (GUI) → E (pulizia).
2. Ogni correzione richiede **un test che fallisce prima e passa dopo**. Se un test esistente contraddice il comportamento corretto descritto qui, aggiorna il test e scrivi il motivo nel commento.
3. **Niente mock che nascondono il bug.** Il difetto A1 non è stato visto proprio perché i test sostituivano `speaker_id_controller`. Dove il piano dice "test con Gio.Settings reale", usa uno schema compilato in una cartella temporanea (§0.3).
4. **Niente script di patch nella radice del repository.** Modifica direttamente i file sorgente.
5. Alla fine esegui i comandi di §6 e riporta l'output **completo**.

### 0.2 Decisioni dell'utente, già prese: non cambiarle
- **Contesti separati.** Il contesto `voice` (wakeword, scorciatoia, estensione) è separato dalle chat della GUI. Ogni chat ha la sua memoria.
- **Le interazioni vocali non si vedono nella GUI.** Nella GUI non esiste una chat "Comandi Vocali".
- **Chat GUI salvate su disco.** La voce avviata dalla GUI risponde **solo in chat**, senza TTS.
- **RAG separato per chat.** Eliminare una chat cancella anche i suoi documenti.
- **Approfondimento vocale:**
  - parte solo quando l'utente lo **chiede esplicitamente** ("approfondisci", "spiegami nel dettaglio", …) **e** la risposta supera una soglia di circa 400 caratteri, configurabile;
  - il TTS legge **la prima frase** della risposta e poi una frase breve localizzata ("Ti ho aperto i dettagli in una chat");
  - se la GUI è chiusa, compare una **notifica cliccabile** che apre la GUI sulla nuova chat.
- **Riconoscimento del parlante:** modalità `disabled` / `informative` / `gate`. In `gate` il sistema è **fail-closed** (nel dubbio blocca). Le parole di stop sono sempre ammesse, **ma solo come comando di interruzione** (vedi A2).

### 0.3 Schema GSettings reale nei test
```python
import subprocess, tempfile, os
schema_dir = tempfile.mkdtemp()
subprocess.run(["glib-compile-schemas", "--targetdir", schema_dir, "data/schemas"], check=True)
os.environ["GSETTINGS_SCHEMA_DIR"] = schema_dir
os.environ["GSETTINGS_BACKEND"] = "memory"
from gi.repository import Gio
settings = Gio.Settings.new("org.gnome.shell.extensions.voice-assistant")
```
Metti questo codice in un helper condiviso, per esempio `tests/helpers_gsettings.py`, e usalo in tutti i test che toccano le impostazioni reali.

---

## A. Critici (sicurezza e funzionalità a vuoto)

### A1. Il `SpeakerIdController` non viene mai creato
**Dove:** `src/daemon/core/runtime_manager.py`, blocco `SpeakerIdController`, circa righe 1150–1185.

**Difetto:** `self.owner.settings.get_property("speaker-id-mode")` solleva `TypeError`, perché `Gio.Settings` non ha proprietà GObject con quel nome. L'eccezione viene catturata e il controller resta `None`. In produzione quindi il riconoscimento del parlante non fa nulla:
- nessuna barriera;
- la registrazione della voce risponde sempre "unavailable".

**Correzione:**
```python
from core.settings import get_boolean_setting  # già importato
def _get_str(key, default):
    try:
        return self.owner.settings.get_string(key) or default
    except Exception:
        return default
def _get_double(key, default):
    try:
        return float(self.owner.settings.get_double(key))
    except Exception:
        return default
speaker_mode = _get_str("speaker-id-mode", "disabled")
speaker_th = _get_double("speaker-id-threshold", 0.75)
```
- Se `core/settings.py` ha già helper equivalenti (`get_string_setting` o simili), usa quelli invece di crearne di nuovi.
- Il `try/except` che avvolge la creazione deve registrare l'errore con `logger.error(..., exc_info=True)`, non con un `warning` generico.

**Test (con Gio.Settings reale, §0.3):** crea `DaemonRuntimeManager` con un owner minimo e chiama `initialize_services()`, oppure estrai la lettura in una funzione e testa quella. Poi verifica:
- `owner.speaker_id_controller is not None`;
- `mode == "gate"` dopo `settings.set_string("speaker-id-mode", "gate")` fatto prima dell'inizializzazione.

---

### A2. La barriera si aggira con le parole di stop
**Dove:** `src/daemon/core/assistant_runtime.py`, `_process_text`, circa riga 1146:
```python
is_stop = any(w in STOP_WORDS for w in text.lower().split())
```
**Difetto (verificato):** con una voce **sconosciuta** in modalità `gate`, queste frasi vengono eseguite perché contengono una parola di `STOP_WORDS`:
- "blocca lo schermo"
- "cancella la cartella documenti"
- "annulla la riunione di domani"

**Correzione:**
1. Crea una funzione pura in `assistant_runtime.py`, oppure in `services/speaker_id/policy.py` come helper:
   ```python
   _STOP_FILLERS = {"ok", "ehi", "dai", "per", "favore", "ora", "subito", "adesso"}
   def is_pure_stop_command(text: str, wakeword_variants: Iterable[str] = ()) -> bool:
       words = [w.strip(".,!?;:") for w in (text or "").lower().split()]
       words = [w for w in words if w and w not in _STOP_FILLERS and w not in set(wakeword_variants)]
       return bool(words) and len(words) <= 2 and all(w in STOP_WORDS for w in words)
   ```
2. `is_stop_command=True` **solo se** `is_pure_stop_command(...)` è vero **e** al momento della cattura l'assistente era in `speaking` o `processing`. Aggiungi a `enqueue_request` il parametro `was_interrupting: bool` e passalo fino a `_process_text`.
3. Quando la policy restituisce `reason == "stop_command"`, la pipeline **non deve essere eseguita**. Si chiama `pipeline_controller.cancel_pipeline()`, si torna in idle e non si manda nulla all'LLM. Oggi il testo di stop continua fino a `process_text_input`: così un comando di stop resta un'interruzione e non diventa mai una richiesta eseguita.
4. Togli `"cancella"`, `"annulla"` e `"blocca"` da `STOP_WORDS` **se** servono come verbi di comando normali. Tieni solo parole che non hanno altro uso: `stop`, `basta`, `zitto`, `fermati`, `silenzio`, `interrompi`. Aggiorna anche il ramo della wakeword Vosk in `reset_wakeword_recognizer` e `_audio_loop`, che usa la stessa costante.

**Test** (`tests/test_assistant_runtime.py`, nuovi casi):
- **Rifiutati:** in `gate` con verdetto `unknown`, "blocca lo schermo", "cancella la cartella documenti" e "annulla la riunione" **non** chiamano `process_text_input` ed emettono `SpeakerRejected`.
- **Ammessi:** in `gate` con verdetto `unknown`, "stop" e "basta!" mentre lo stato è `speaking` chiamano `cancel_pipeline` e **non** chiamano `process_text_input`.
- **Stop senza nulla da interrompere:** "stop" in stato `idle` con voce sconosciuta viene rifiutato (non c'è nulla da interrompere).
- **Funzione pura:** `is_pure_stop_command` va testata con una tabella di casi.

---

### A3. Path traversal sugli id delle conversazioni
**Dove:** `src/daemon/services/conversation_contexts.py`, `_get_file_path`, `delete`, `save`, `create`, `clear`, `_load_all`.

**Difetto (verificato):** `DeleteConversation("../victim")` cancella `victim.json` fuori dalla cartella `conversations/`.

**Correzione:**
```python
import re
_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

@staticmethod
def is_valid_chat_id(context_id: str) -> bool:
    return isinstance(context_id, str) and bool(_ID_RE.match(context_id))
```
- **`_get_file_path`:** solleva `ValueError` se l'id non è valido. Prima di restituire il percorso verifica anche `os.path.realpath(path).startswith(os.path.realpath(self.store_dir) + os.sep)`.
- **`delete`, `clear`, `save`:** con un id non valido o `"voice"` restituiscono `False`, senza toccare il disco. `delete` restituisce `True` solo se il contesto esisteva.
- **`_load_all`:** ignora, con un warning, i file il cui nome non è un UUID valido.
- **`clear`:** oggi va in crash se `get()` restituisce `None`. Gestisci quel caso.
- **`save`:**
  - usa `os.replace` e fai `f.flush(); os.fsync(f.fileno())` prima di sostituire il file;
  - serializza una **copia** dei messaggi (`list(ctx.messages)` sotto `ctx.lock`), non la lista viva.
- **`main.py`:**
  - `DeleteConversation` restituisce il valore di `context_manager.delete(...)`;
  - `GetConversationMessages` e `ProcessTextInContext` rifiutano gli id non validi. Con un id non valido `ProcessTextInContext` registra un warning e **non** accoda nulla.

**Test** (`tests/test_conversation_contexts.py`, nuovo):
- `delete("../victim")`, `delete("voice")`, `delete("x/../../y")` restituiscono `False`, e un file sentinella fuori dalla cartella resta intatto;
- create, salvataggio, ricaricamento con un nuovo manager e cancellazione funzionano;
- un file `pippo.json` nella cartella viene ignorato al caricamento;
- `clear("inesistente")` non solleva eccezioni.

---

## B. Riconoscimento del parlante

### B1. Cambi di modalità e soglia applicati a caldo
**Dove:** `runtime_manager.py`, nel blocco `self.owner.settings.connect("changed::…")` (circa righe 332–367).

**Correzione:**
- Aggiungi `changed::speaker-id-mode` e `changed::speaker-id-threshold`.
- In `assistant_runtime.on_settings_changed`, per `speaker-id-mode`:
  - chiama `spk_ctrl.set_mode(...)`;
  - se la nuova modalità è `disabled`, chiama `_cancel_speaker_session()`;
  - se è diversa da `disabled`, avvia il **precaricamento** (B2);
  - se il backend non è disponibile e la modalità non è `disabled`, chiama `owner.notify_dependency_required("resemblyzer", "Riconoscimento del parlante", False)`.

**Test:** con Gio.Settings reale, cambiare la chiave dopo l'inizializzazione aggiorna `speaker_id_controller.mode` e `identifier.threshold`.

### B2. Precaricamento del modello (evitare timeout e rifiuti)
**Difetto:** il modello si carica solo al primo `embed()`, nel thread worker, mentre `finalize(timeout_s=1.5)` aspetta. Il model manager poi scarica il tipo `speaker` dopo il timeout di inattività. In modalità `gate`, la prima richiesta dopo l'avvio o dopo una pausa viene quindi rifiutata per `timeout`.

**Correzione:**
1. **`SpeakerIdController.warm_up()`:** avvia in un thread `backend.load()` e poi un `embed()` su 1 s di silenzio con rumore leggero, per compilare il grafo. Imposta `self.ready = True` quando finisce, `False` in caso di errore (con log).
2. **Quando chiamarlo:**
   - all'avvio, se `mode != "disabled"`;
   - su `set_mode` verso una modalità diversa da `disabled`;
   - in `trigger_assistant`, se `mode != "disabled"` e `not ready`, in modo non bloccante.
3. **Tempo di attesa nella barriera:** se `not ready` quando si arriva a `finalize`, aspetta fino a `warm_up_timeout_s = 8.0` che il modello sia pronto (con `threading.Event`), poi applica il timeout normale di 1,5 s al verdetto. Se il caricamento fallisce, il verdetto è `unavailable` e la barriera rifiuta.
4. **Scaricamento per inattività:**
   - con `mode == "gate"` il modello **non** va scaricato: nel callback registrato nel model manager, ignora lo scarico se `mode == "gate"`;
   - con `informative` lo scarico è ammesso, e il primo uso successivo richiama `warm_up()`;
   - dopo ogni identificazione chiama `model_manager.update_active_timestamp()`.
5. **Avviso nella GUI:** mostra nella riga del profilo o in un sottotitolo che, con la barriera attiva, il modello resta sempre in memoria.

**Test:** con un backend finto il cui `load()` dura 0,5 s e `finalize` chiamato subito dopo `create_session`, il verdetto **non** è `timeout`.

### B3. La registrazione della voce può restare bloccata
**Dove:** `assistant_runtime._audio_loop` (circa righe 905–918) e `services/speaker_id/enrollment.py`.

**Difetti:**
- `check_timeout()` non viene mai chiamato;
- se lo stato è `disabled`, il `continue` iniziale salta anche l'invio dei blocchi alla registrazione;
- se il microfono non produce blocchi (`raw_data is None`), la registrazione non riceve nulla e resta attiva per sempre, mentre wakeword e STT restano fermi.

**Correzione:**
1. In `_audio_loop`, **prima** del controllo `self.owner._state == "disabled"`:
   ```python
   if spk_ctrl and spk_ctrl.is_enrollment_active:
       if raw_data:
           spk_ctrl.feed_enrollment_audio(raw_data)
       spk_ctrl.enrollment_recorder.check_timeout()
       continue
   ```
2. **`StartSpeakerEnrollment` in `main.py`:** se lo stato è `disabled` o lo stream audio non è aperto, restituisci `False` ed emetti `SpeakerEnrollmentFinished(False, "", "no_audio")`.
3. **`EnrollmentRecorder`:**
   - **`check_timeout`:** usa `self._last_chunk_time`. Fallisci con `no_audio` se non arriva nessun blocco da più di 3 s, e anche se `now - start > duration + 3`.
   - **Lock:** `_notify_finished` e `_notify_progress` non devono essere chiamati mentre si tiene `self._lock`. Raccogli i dati sotto lock e notifica fuori.
   - **Annullamento durante l'elaborazione:** aggiungi un flag `_cancel_requested`, che `_process_completion` controlla prima di `save_enrollment`. Se è stato richiesto l'annullamento, non salvare e non notificare il successo. `cancel()` notifica `cancelled` una sola volta.

**Test** (`tests/test_speaker_enrollment.py`):
- senza blocchi, `check_timeout()` dopo 3 s simulati (patch di `time.monotonic`) chiude con `no_audio`;
- un annullamento durante `_process_completion` (backend lento) non crea il file del profilo e non emette successo;
- con stato `disabled`, `StartSpeakerEnrollment` restituisce `False`.

### B4. Rilevamento delle sovrapposizioni conforme al piano
**Dove:** `services/speaker_id/session.py`, `_check_overlap_incremental`.

**Correzioni:**
1. **Conteggio delle finestre.** Una finestra è:
   - `foreign` se `sim < th - margin`: incrementa il contatore;
   - `match` se `sim >= th`: azzera il contatore;
   - `uncertain` in tutti gli altri casi: **azzera** anche lei il contatore.

   Solo due finestre `foreign` **consecutive** attivano la sovrapposizione.
2. **Ordine dei risultati.** Le finestre vengono elaborate in modo asincrono, quindi i risultati possono arrivare fuori ordine. Assegna a ogni finestra un indice progressivo e nel callback elaborale in ordine: tieni un buffer `pending` e avanza solo sull'indice atteso. Le finestre scartate dalla coda (`submit` restituisce `False`) valgono come `uncertain` e azzerano il contatore.
3. **Rumore di fondo reale.** `SpeakerIdController.create_session` deve passare `noise_floor_getter`. Aggiungi ad `AudioFilter` un metodo pubblico `get_noise_floor()` e passalo in `trigger_assistant` con `lambda: owner.audio_filter.get_noise_floor()`.
4. **Letture da disco.** Oggi `match_pcm` chiama `list_profiles()` e `reference_embedding()` a ogni finestra, rileggendo i file `.npz`. Aggiungi a `SpeakerProfileStore` una cache in memoria dei riferimenti, invalidata da `save_enrollment`, `add_history` e `delete_profile` e controllata tramite `mtime`. Il lavoro per ogni finestra deve essere solo il prodotto scalare.

**Test** (`tests/test_speaker_session.py`):
- sequenze `foreign, uncertain, foreign` → nessuna sovrapposizione; `foreign, foreign` → sovrapposizione; `foreign, match, foreign` → nessuna sovrapposizione;
- callback che arrivano in ordine inverso producono lo stesso risultato;
- `list_profiles` non viene chiamato a ogni finestra (spy sullo store).

### B5. Coerenza della policy
- **Sessione mancante.** In `_process_text`, se `is_voice` e `mode != "disabled"` ma `speaker_session is None`, il verdetto è `None`. Oggi è già così: aggiungi un test esplicito per `gate`, che deve rifiutare, e per `informative`, che deve lasciar passare.
- **Nome del parlante in memoria.** Salvalo nei `metadata` del messaggio utente, non nel testo.

---

## C. Multi-chat (daemon)

### C1. Messaggio corrente duplicato nello Smart-Path
**Dove:** `core/smart_path_controller.py`, `execute_smart_path` (circa righe 205–260).

**Difetto (verificato):** `add_user_message(user_message, context)` viene eseguito **prima** di leggere la cronologia, quindi la cronologia contiene già il messaggio corrente e l'LLM lo riceve due volte. Nel percorso di riserva è già corretto: usalo come riferimento.

**Correzione:**
1. Leggi `history_msgs` **prima** di `add_user_message`.
2. Chiama `build_smart_prompt(...)` (per il RAG) **prima** di `add_user_message`, così il RAG non trova come primo risultato il messaggio stesso.
3. Rimuovi la costruzione di `messages` se non viene usata. Oggi `build_smart_prompt` produce un prompt di sistema che nessuno legge: il RAG va passato come `context` all'LLM, concatenato con `extra_context`.
   ```python
   rag_text = self.prompt_builder.format_context(rag_results=...)  # crea format_context se manca, estraendo la parte "Memoria rilevante" da build_prompt
   llm_context = "\n\n".join(p for p in (rag_text, extra_context) if p)
   stream = llm_streamer(user_message, history=history_msgs, context=llm_context)
   ```

**Test:** in `tests/test_smart_path_controller.py` e `tests/test_core_pipeline.py`, il secondo messaggio in una chat riceve una cronologia che **non** contiene il messaggio corrente e che contiene il turno precedente completo (utente e assistente).

### C2. Trascrizione della chat con la memoria disattivata
**Difetto (verificato):** con `memory-enabled = false` la trascrizione di una chat resta vuota, perché `add_user_message` e `add_assistant_message` escono subito.

**Correzione:** separa "memoria per l'LLM" da "storico della chat".
- `ConversationContext.add_message` viene chiamato **sempre** per le chat GUI, così la trascrizione viene salvata e mostrata.
- `memory_enabled` controlla solo:
  - se `history_msgs` viene passato all'LLM;
  - se si scrive nel RAG.
- Per il contesto `voice`, con memoria disattivata non si conserva nulla (comportamento di privacy).
- Rendi coerente il Fast-Path, che oggi salva sempre: stessa regola.

**Test:**
- con memoria disattivata, una chat GUI ha la trascrizione completa ma l'LLM riceve `history=None` e nessun documento RAG;
- il contesto `voice` resta vuoto.

### C3. RAG: cancellazione per chat e duplicati
**Dove:** `services/rag_store.py` e `main.py:DeleteConversation`.

**Difetti (verificati):**
- `delete_by_metadata` va in crash con `AttributeError: '_lock'` e usa `modified_since_sync`, che non esiste;
- non viene mai invocato, perché `DeleteConversation` cerca `assistant_runtime.smart_path`, che non esiste (il percorso giusto è `pipeline_controller.smart_path`);
- non rimuove i documenti da `doc_vectors` né da SQLite, quindi ricompaiono al riavvio;
- il controllo dei duplicati usa solo il contenuto, quindi la stessa frase in una seconda chat resta legata alla prima.

**Correzione:**
1. In `VectorStore.__init__` aggiungi `self._lock = threading.RLock()` e usalo in `add_document`, `remove_document`, `search` (copia gli elementi sotto lock) e `_sync_to_db`.
2. **`delete_by_metadata(key, value)`:**
   - rimuove da `documents` **e** da `doc_vectors`;
   - esegue `DELETE FROM documents WHERE json_extract(metadata, '$.' || ?) = ?` sul database;
   - restituisce il numero di documenti rimossi.
3. **Duplicati.** `add_document(content, metadata, doc_id=None)` usa `doc_id or _hash_content(content)` come chiave. `SmartPathController` passa `doc_id = md5(f"{context_id}\0{text}")[:16]`.
4. **`DeleteConversation`:** usa `self.pipeline_controller.smart_path.vector_store.delete_by_metadata("context_id", context_id)`, dentro un `try` con log.
5. **Ricerca.** `search(context_id=...)`: i documenti con `context_id` mancante (legacy o globali) restano visibili a tutti, come oggi, ma vanno documentati.

**Test** (`tests/test_hybrid_rag_store.py`):
- la cancellazione rimuove i documenti in memoria e nel database: un nuovo `VectorStore` sullo stesso file non li ricarica;
- la stessa frase in due chat produce due documenti, ciascuno trovabile solo nella propria chat;
- `DeleteConversation` via `main.py` (owner finto) chiama la cancellazione.

### C4. Approfondimento vocale conforme alle decisioni
**Dove:** `core/pipeline.py`, `_on_sentence_ready` e `_trigger_deep_dive` (circa righe 378–425), più `process_text_input`.

**Difetti:**
- parte per **qualunque** risposta sopra i 400 caratteri, invece che solo su richiesta esplicita;
- copia il contesto `voice` **mentre la risposta è ancora in corso**, quindi la risposta lunga non c'è;
- legge frasi fino a 400 caratteri invece della prima frase sola;
- la frase finale è fissa in italiano;
- la notifica non apre la chat;
- `_needs_deep_dive` e `_current_tts_sentences` non vengono mai usate.

**Correzione:**
1. **Riconoscere la richiesta.** Crea `data/nlu/deep_dive_patterns.json`, con pattern `it` e `en` come `approfondisci`, `spiegami (meglio|nel dettaglio)`, `dimmi di più`, `in dettaglio`, `tell me more`, `explain in detail`. In `process_text_input`, `self._needs_deep_dive = speak and context_id == "voice" and <match pattern>`.
2. **Soglia.** Nuova chiave GSettings `deep-dive-threshold-chars` di tipo `i`, default `400`, `range 150..2000`, documentata in `docs/gsettings.md`.
3. **Durante lo streaming**, se `_needs_deep_dive`:
   - la **prima** frase va al TTS normalmente;
   - le frasi successive vengono solo accumulate, **non** lette;
   - si conta la lunghezza totale.
   Se non è una richiesta di approfondimento, il comportamento resta quello di oggi, senza taglio.
4. **A fine risposta**, dopo il salvataggio del messaggio dell'assistente nel contesto:
   - **Sopra la soglia:**
     - crea la chat con titolo localizzato e contenuto: gli ultimi 6 messaggi di `voice` **inclusi** domanda e risposta completa;
     - leggi con TTS la frase localizzata `responses.json → deep_dive.opened_chat`;
     - emetti il segnale D-Bus `ConversationCreated(s id, s reason)` con `reason = "deep_dive"`;
     - mostra la notifica (punto 5).
   - **Sotto la soglia:** leggi con TTS le frasi accumulate, cioè il comportamento normale.
5. **Notifica cliccabile.** Usa l'azione `default`, sul modello di quella delle dipendenze mancanti in `runtime_manager.py`. Il clic chiama `owner._launch_gui("--open-conversation", chat_id)`. In `src/gui/main.py`, gestisci l'argomento `--open-conversation <id>`: la finestra si apre sulla chat. Se la GUI è già aperta, `ConversationCreated` aggiorna la lista delle chat.
6. **Pulizia.** Rimuovi le variabili inutilizzate e il `threading.Thread` lanciato dentro `_on_sentence_ready`.
7. **D-Bus.** Aggiungi `ConversationCreated` all'XML, alla copia in `extension.js` e in `docs/dbus.md`.

**Test** (`tests/test_core_pipeline.py`):
- "spiegami nel dettaglio X" con una risposta di 900 caratteri: TTS chiamato con la prima frase **e** con la frase "aperto chat"; una nuova chat che contiene domanda e risposta completa; segnale emesso;
- la stessa domanda senza pattern esplicito: nessuna chat creata e TTS normale;
- richiesta esplicita con risposta di 200 caratteri: nessuna chat e tutte le frasi lette;
- richiesta da chat GUI (`speak=False`): nessuna chat creata.

---

## D. GUI

### D1. `assistant_window.ui` e `assistant_window.blp` disallineati
**Difetti:**
- **Il `.ui` nel repository non contiene** `chats_list`, `new_chat_btn`, `addons_sidebar` e `toolbar_view`. Oggi la GUI funziona solo perché la gresource installata localmente è stata compilata a mano.
- **Il `.blp` non contiene** le pagine MCP e Skills (`mcp_page`, `skills_page`, `skills_nav_view`, `skill_editor_*`, `skill_type_*`, `sidebar_item_mcp`, `sidebar_item_skills`, `toast_overlay`, …), che invece stanno nel `.ui`.

**Correzione:**
1. Il **`.blp` è la fonte**. Porta nel `.blp` tutte le parti presenti nel `.ui` e assenti nel `.blp`: pagine MCP e Skills, editor delle skill, `toast_overlay`, voci della sidebar. Mantieni le parti multi-chat.
2. Rigenera `data/ui/assistant_window.ui` con `blueprint-compiler compile --output data/ui/assistant_window.ui data/ui/assistant_window.blp`.
3. In `data/meson.build` aggiungi un `custom_target` di compilazione del Blueprint anche per `assistant_window`, come già esiste per `prefs`, così i due file non possono più divergere.
4. Aggiungi in `tests/test_schema_and_resources.py` un test che, se `blueprint-compiler` è disponibile, compila entrambi i `.blp` in una cartella temporanea e confronta il risultato con i `.ui` nel repository. Il test **fallisce** se i file sono diversi.

### D2. Nessuna interazione vocale nella GUI
**Dove:** `src/gui/assistant_window.py`.

**Correzione:**
- Rimuovi la riga "Comandi Vocali" (circa riga 189) e il salto automatico alla vista vocale in `_on_transcript_received` (circa riga 294).
- **Chat all'apertura:**
  - se non esistono chat, all'apertura se ne crea una;
  - la chat corrente di default è la più recente;
  - `_current_context_id` non vale mai `"voice"`.
- **Filtro dei segnali.** La GUI **ignora** `TranscriptReceived` e `ResponseTokenStreamed` quando la richiesta non è partita da lei. Il daemon deve dire a quale contesto appartengono i token.
  - Aggiungi i segnali `ConversationToken(s context_id, s token, b is_complete)` e `ConversationTranscript(s context_id, s text, b is_final)`, emessi dalla pipeline con il `context_id` della richiesta.
  - I segnali vecchi restano, per l'estensione, solo per il contesto `voice`.
  - La GUI ascolta solo i segnali nuovi e aggiorna la chat con quell'id, anche se non è quella visibile.
- **Daemon.** `GetConversationMessages("voice")` restituisce `[]`. `ProcessTextInput` (vecchio metodo, senza id) va in una chat GUI di default, non in `voice`: la più recente, oppure una nuova.
- Togli i due `print(...)` di debug in `_on_new_chat` (riga 262).

**Test** (`tests/test_gui.py`):
- la lista delle chat non contiene "Comandi Vocali";
- un `TranscriptReceived` di `voice` non cambia la chat corrente;
- un `ConversationToken` per una chat non visibile non viene scritto nella chat visibile.

### D3. Sintesi vocale non disattivabile
**Dove:** `data/ui/prefs/page_tts.blp`, `src/gui/components/settings/tts.py`.

**Correzione:** ripristina il gruppo con `Adw.SwitchRow tts_enable_row` legato a `tts-enabled` (`bind_setting`), come nel commit `6200f3c`. Rigenera `prefs.blp` e `prefs.ui` (§6). Aggiorna o aggiungi il test in `tests/test_gui.py`.

---

## E. Pulizia e contratti

1. **Script temporanei.** Elimina dalla radice del repository: `fix_activatable.py fix_clean_chat.py fix_dbus_xml.py fix_dead.py fix_debug_chat.py fix_deepdive.py fix_del_conv.py fix_gui_chat.py fix_icons_and_selection.py fix_indent.py fix_limit.py fix_listen_state.py fix_ollama.py fix_rag_hash.py fix_runtime2.py fix_runtime.py fix_sidebar.py fix_skill.py fix_tts.py patch_rag.py test_box_remove.py test_listbox.py test_sidebar.py test_sidebar_signal.py trigger_click.py`. Controlla anche che non ne siano rimasti altri: `git status --short | grep '^??'`.
2. **D-Bus.** L'XML, la copia in `src/extension.js` e `docs/dbus.md` devono avere **gli stessi membri**. Oggi mancano:
   - nella copia di `extension.js`: `GetSkills`, `SaveSkill`, `DeleteSkill`, `DeleteModel`;
   - nell'XML: `GetInstalledModels`.

   Aggiungi i nuovi segnali previsti da questo piano: `ConversationCreated`, `ConversationToken`, `ConversationTranscript`. Aggiungi un test che confronta automaticamente l'XML con la copia in `extension.js` e con i metodi e segnali pubblici di `main.py` (il nome inizia con una maiuscola).
3. **Test registrati in meson.** `tests/meson.build` deve includere i nuovi file di test (`test_conversation_contexts.py` e ogni altro file creato).
4. **Documentazione.** Aggiorna `docs/pipeline.md` (approfondimento, stop, precaricamento), `docs/gsettings.md` (`deep-dive-threshold-chars`), `docs/dbus.md` e `docs/architecture.md`.
5. **Nessun commit.** Lascia le modifiche non committate: il commit lo fa l'utente dopo la review.

---

## 6. Verifica finale (riporta l'output completo)

```bash
# 1. Blueprint e UI allineati
python3 scripts/assemble_blueprints.py
blueprint-compiler compile --output data/ui/prefs.ui data/ui/prefs.blp
blueprint-compiler compile --output data/ui/assistant_window.ui data/ui/assistant_window.blp
git diff --stat data/ui/

# 2. Test daemon
python -m pytest -q -p no:cacheprovider tests --ignore=tests/test_gui.py

# 3. Test GUI (con display)
GSETTINGS_BACKEND=memory GSETTINGS_SCHEMA_DIR=data/schemas python -m pytest -q -p no:cacheprovider tests/test_gui.py

# 4. Nessun file spurio
git status --short | grep '^??'
```

**Verifiche manuali da riportare:**
- **Daemon avviato con `speaker-id-mode = gate`:**
  - nel log compare la creazione del controller, senza il warning "Inizializzazione SpeakerIdController";
  - "blocca lo schermo" detto da un'altra persona o riprodotto da un video viene rifiutato.
- **Registrazione con assistente disattivato:** errore immediato `no_audio`, e la GUI non resta bloccata.
- **Approfondimento vocale:** "\<wakeword\>, spiegami nel dettaglio la fotosintesi" → prima frase letta, chat creata con la risposta completa, notifica che apre la chat.
- **GUI:** nessuna chat vocale nella lista; due chat con richieste in coda mostrano ciascuna solo le proprie risposte.

## 7. Checklist per la review (Claude verificherà)
- [ ] A1 controller creato con Gio.Settings reale (test senza mock).
- [ ] A2 parole di stop solo come interruzione pura; nessuna esecuzione della pipeline per `stop_command`.
- [ ] A3 id validati come UUID; niente accesso fuori da `conversations/`.
- [ ] B1–B5 cambi a caldo, precaricamento, timeout della registrazione, sovrapposizioni ordinate con azzeramento su `uncertain`, cache dei profili.
- [ ] C1 nessun duplicato del messaggio corrente; RAG passato come `context`.
- [ ] C2 trascrizione salvata con memoria disattivata.
- [ ] C3 cancellazione RAG in memoria e su SQLite; `doc_id` per chat.
- [ ] C4 approfondimento solo su richiesta esplicita, a fine risposta, con prima frase letta e notifica cliccabile.
- [ ] D1 `.ui` rigenerati dai `.blp`, test di allineamento, target meson.
- [ ] D2 nessuna chat vocale nella GUI; token instradati per `context_id`.
- [ ] D3 interruttore TTS ripristinato.
- [ ] E script temporanei eliminati, contratti D-Bus allineati con test automatico, documentazione aggiornata, nessun commit.
