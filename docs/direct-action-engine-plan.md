# Fusione di Fast-Path e Medium-Path: Unified Direct Action Engine

> Stato: piano rivisto dopo verifica sul codice attuale (2026-09-12). Le correzioni
> rispetto alla bozza originale sono segnalate con **[CORREZIONE]**.

## Panoramica e Obiettivo

Attualmente la pipeline di `PipelineController` presenta due stadi prima dello Smart-Path:

1. **Fast-Path** (`FastPathDispatcher.dispatch`, `src/daemon/core/pipeline.py:92`): regex
   deterministici (`INTENT_PATTERNS`, volume, skill `.md`) più, in coda, un fallback
   semantico già esistente — vedi **[CORREZIONE 1]**.
2. **Medium-Path** (`_try_llm_tool_select`, `src/daemon/core/pipeline.py:309`): interpella
   l'LLM chiedendo un JSON `{"tool": ...}`. Su modelli locali leggeri (1B) fallisce
   sistematicamente (il modello risponde a parole tipo `"Calendario"` invece del JSON) e
   aggiunge 1-2 secondi di latenza inutile. Questa diagnosi è confermata a codice.

Il prototipo in `experiments/test-2.py` (presente solo nel checkout principale, non
tracciato da git) ha dimostrato che la classificazione per similarità coseno su embedding
di frase è efficace e a bassa latenza percepita per instradare frasi colloquiali verso
l'intento corretto — vedi **[CORREZIONE 2]** per i limiti di questo prototipo.

**Obiettivo**: sostituire il Medium-Path LLM e potenziare il fallback semantico del
Fast-Path con un **Direct Action Engine unificato** che:

* Riconosce la macro-intenzione tramite Intent Routing a due livelli (Regex immediata +
  Embedding semantico ONNX, in sostituzione dell'attuale matcher bag-of-words).
* Estrae i parametri tramite Slot Filling specializzato (fuzzy matching via `RapidFuzz`
  sui file `.desktop` per le app, parsing numerico per il volume).
* Esegue direttamente i comandi tramite `mcp_manager.execute_tool(...)`.
* Elimina la chiamata LLM di selezione tool ed esegue fallback trasparente allo
  Smart-Path (LLM conversazionale) solo quando lo score di confidenza è sotto soglia.

---

## Correzioni rispetto alla bozza iniziale

### [CORREZIONE 1] — Il fallback semantico esiste già, non è "regex puro"

`FastPathDispatcher.dispatch()` (`pipeline.py:157-270`) non è solo regex: allo step
2c chiama già `VectorIntentMatcher.match()` (`src/daemon/skills/vector_intent_matcher.py`),
un matcher a **token-overlap / cosine su bag-of-words** (nessun modello di embedding,
nessuna libreria ML) sui trigger definiti nelle skill `.md` caricate da `SkillRegistry`
(`src/daemon/skills/skill_registry.py`). Esistono già test dedicati:
`tests/test_semantic_dispatch.py` e `tests/test_skill_markdown_loader.py`.

**Impatto sul piano**: il lavoro non è "aggiungere un livello semantico ex novo", ma
**sostituire l'implementazione bag-of-words di `VectorIntentMatcher` con vere embedding
MiniLM/ONNX**, mantenendo l'interfaccia (`match(text) -> {"intent", "score", ...}`) così
i test esistenti restano un baseline di regressione valido durante la migrazione.

### [CORREZIONE 2] — Il prototipo ONNX non è ancora ONNX

`experiments/test-2.py` usa `sentence_transformers.SentenceTransformer('all-MiniLM-L6-v2')`
(PyTorch), non `onnxruntime` puro. `sentence-transformers` applica automaticamente
**mean pooling + normalizzazione L2** sull'output del transformer prima di restituire il
vettore finale. Se si passa a `onnxruntime` grezzo (obiettivo dichiarato, per evitare
PyTorch nel daemon), questo pooling **va reimplementato a mano in numpy**: non è un
semplice "export del modello e via". La soglia di confidenza calibrata nel prototipo
(0.80) non è automaticamente valida sull'implementazione ONNX manuale: va ricalibrata
empiricamente con lo stesso set di frasi di test dopo la migrazione.

**Impatto sul piano**: aggiungere un task esplicito "conversione MiniLM → ONNX +
reimplementazione mean-pooling in numpy + ricalibrazione soglia" prima di dichiarare il
router pronto per la produzione.

### [CORREZIONE 3] — I file dati di supporto non esistono

`data/locales/number_words.json` e `data/locales/responses.json`, citati nella bozza
originale come sorgenti dati già presenti, **non esistono nel repository**. Oggi:

* I numeri italiani sono hardcoded in `FastPathDispatcher.IT_NUMBERS` (`pipeline.py:98`).
* Le risposte vocali sono f-string inline nel codice (es. `pipeline.py:177`,
  `pipeline.py:245`).

**Impatto sul piano**: creare effettivamente questi file come parte del lavoro (non solo
"attingere" da essi), oppure rinunciare all'esternalizzazione e continuare a usare le
costanti Python esistenti se l'esternalizzazione non è strettamente necessaria per questa
fase.

### [CORREZIONE 4] — Le GSettings `fast-path-enabled` / `medium-path-enabled` non esistono

Verificato su `data/schemas/org.gnome.shell.extensions.voice-assistant.gschema.xml`:
nessuna chiave con questi nomi è presente oggi. Non c'è quindi "retrocompatibilità" da
mantenere — sono chiavi **nuove** da progettare e aggiungere allo schema, oltre che alla UI
delle preferenze (vedi sezione Grafica).

### Verifica dipendenze (nessuna correzione, solo conferma)

`onnxruntime`, `tokenizers`, `rapidfuzz`, `numpy` sono già installati nel venv reale del
daemon (`~/.local/share/gnome-shell/extensions/voice-assistant@scroker.github.io/daemon/venv`).
`tokenizers` e `rapidfuzz` non sono però elencati esplicitamente in
`src/daemon/requirements.txt` (arrivano come dipendenze transitive di `sherpa-onnx` /
`openwakeword`): da diventare dipendenze dirette del progetto vanno aggiunti esplicitamente
al file, per non fare affidamento implicito su un albero di dipendenze transitive che può
cambiare.

> Nota a margine (non bloccante): il path del venv installato usa l'UUID estensione
> `voice-assistant@scroker.github.io`, mentre questa working directory è
> `voice-assistant@mkswap.github.io`. Verificare che sia l'estensione attesa prima del
> test manuale su sistema live.

---

## Architettura del Direct Action Engine Unificato

```
Testo trascritto (STT)
        │
        ▼
┌───────────────────────┐
│ 1. Regex deterministici│  (FastPathDispatcher.INTENT_PATTERNS, invariati)
└───────────┬───────────┘
        no match
        ▼
┌────────────────────────────────┐
│ 2. Semantic Intent Router       │  (sostituisce VectorIntentMatcher bag-of-words)
│    MiniLM ONNX + cosine (numpy) │
└───────────┬─────────────────────┘
   score < soglia (0.75, da ricalibrare)
        │                       │
        ▼                       ▼
┌────────────────┐     ┌──────────────────────┐
│ Fallback        │     │ 3. Slot Filling       │
│ Smart-Path (LLM)│     │ (AppSlotMatcher/      │
│                 │     │  numero/volume)       │
└─────────────────┘     └──────────┬────────────┘
                                    ▼
                         ┌──────────────────────┐
                         │ mcp_manager.execute_  │
                         │ tool(tool, args)       │
                         └──────────────────────┘
```

`_try_llm_tool_select` (Medium-Path LLM) viene **rimosso** solo dopo che il router
semantico ha dimostrato, sui test di regressione, di non peggiorare la copertura rispetto
al bag-of-words attuale.

---

## Dettaglio dei Componenti

### 1. Intent Semantic Router (sostituisce `VectorIntentMatcher`)

* Modello: `all-MiniLM-L6-v2` esportato in ONNX (~80MB), con tokenizer HuggingFace via
  `tokenizers`.
* Runtime: `onnxruntime` + `numpy`, nessuna dipendenza da PyTorch/`sentence-transformers`
  nel daemon.
* Pooling: mean pooling + normalizzazione L2 reimplementati manualmente in numpy
  (vedi [CORREZIONE 2]) — da validare contro l'output di `sentence-transformers` sullo
  stesso set di frasi prima di considerarlo equivalente.
* Prototipi di intenti: derivati dalle skill `.md` esistenti in `SkillRegistry`, estesi con
  esempi colloquiali/STT-rumorosi per: `quick_settings` (wifi, bluetooth, dark_style,
  night_light, dnd), `media_control` (play/pause/stop/next/previous), `set_volume`,
  `launch_application`.
* Soglia di confidenza: parametro calibrabile, default provvisorio 0.75 — **da
  ricalibrare** dopo la migrazione a ONNX (vedi [CORREZIONE 2]), non riusare direttamente
  lo 0.80 del prototipo PyTorch.
* Interfaccia compatibile con `VectorIntentMatcher.match()` per riuso dei test esistenti.

### 2. Slot Filling Specializzato

* **`launch_application`**: nuovo modulo `app_slot_matcher.py`. Indicizza i `.desktop` in
  `/usr/share/applications` e `~/.local/share/applications`, matching fuzzy con
  `RapidFuzz` (da aggiungere esplicitamente a `requirements.txt`). Oggi questo matching
  non esiste: il catch-all attuale (`pipeline.py:225-239`) è un semplice regex
  "apri/avvia + testo libero" passato as-is a `intent_handler("launch_app", ...)`, senza
  alcuna risoluzione contro le app realmente installate.
* **`set_volume`**: estrazione percentuali/numeri (oggi via `IT_NUMBERS` hardcoded, vedi
  [CORREZIONE 3] per l'eventuale esternalizzazione) o step relativo (+10%/-10%, già
  presente per `volume_up`/`volume_down`).
* **`quick_settings` / `media_control`**: parametri impliciti nell'intento matchato, come
  già avviene per `set_theme_dark`/`set_theme_light` nel codice attuale.

### 3. Esecuzione Unificata MCP

Invariata rispetto alla bozza originale: sia il match da regex (Tier 1) sia quello dal
Semantic Router (Tier 2) eseguono via `self.mcp_manager.execute_tool(tool_name, args)`.
Il dizionario di ritorno di `process_text_input` resta compatibile con la struttura
esistente (`fast_path`, `intent`, `params`, `transcription`, `response`), aggiungendo un
flag `direct_action: True` per distinguere il match semantico da quello regex puro nei log
e nella UI diagnostica (vedi sezione Grafica).

### 4. Gestione GSettings

Da **creare** (non da mantenere, vedi [CORREZIONE 4]) nello schema
`org.gnome.shell.extensions.voice-assistant.gschema.xml`:

* `direct-action-engine-enabled` (bool, default `true`): abilita l'intero dispatcher
  diretto (regex + semantico), sostituendo concettualmente il vecchio
  `fast-path-enabled` mai esistito.
* `semantic-router-confidence-threshold` (double, default `0.75`): soglia calibrabile
  esposta in UI.
* Il vecchio Medium-Path LLM (`_try_llm_tool_select`) viene rimosso dal codice, quindi non
  necessita di un proprio flag GSettings permanente — se serve un flag temporaneo per il
  rollout, va marcato esplicitamente come rimovibile a fine migrazione.

---

## Sezione Grafica (UI delle Preferenze)

La UI delle preferenze è definita in Blueprint (`data/ui/prefs.blp`), compilata in
GResource e caricata da `src/gui/settings_window.py` (`_SettingsWindow`) tramite
`Gtk.Builder`. La navigazione è a pagine (`Adw.PreferencesPage`) elencate in
`_setup_navigation()` (`settings_window.py:104-114`): General, Wake Word, STT, LLM, TTS,
**MCP** (`mcp_page`, oggi solo una riga di stato `pipeline.py`-agnostica), Models,
Bug Reporting.

### Modifiche proposte

1. **Nuovo gruppo in `mcp_page`** (`data/ui/prefs.blp:642-652`), sotto la riga di stato MCP
   esistente:
   * `Adw.PreferencesGroup` "Motore di Azione Diretta" (Direct Action Engine):
     * `Adw.SwitchRow` collegata a `direct-action-engine-enabled` — abilita/disabilita il
       dispatcher diretto (regex + semantico). Se disattivata, ogni frase passa sempre
       per lo Smart-Path/LLM, utile per debug o per hardware più lento dove il modello
       ONNX non è desiderato.
     * `Adw.SpinRow` (o `Adw.ActionRow` con `Gtk.Scale`) collegata a
       `semantic-router-confidence-threshold`, range 0.50–0.95, step 0.05 — permette
       all'utente di rendere il router più permissivo o più conservativo senza rilasciare
       una nuova build.
     * `Adw.ActionRow` diagnostica read-only "Ultimo instradamento": mostra intento e
       score dell'ultima classificazione semantica (utile in fase di tuning; popolata via
       D-Bus/segnale dal daemon, non persistita in GSettings).
2. **Binding**: seguire il pattern già usato in `_setup_bindings()` per
   `enable_switch_row` (bind diretto `Gio.Settings.bind()` verso lo schema), così i nuovi
   controlli si comportano in modo coerente con il resto della finestra.
3. **Schema**: aggiungere le due chiavi (`direct-action-engine-enabled`,
   `semantic-router-confidence-threshold`) a
   `data/schemas/org.gnome.shell.extensions.voice-assistant.gschema.xml`, con summary e
   description in stile coerente alle chiavi esistenti (vedi `docs/gsettings.md` per la
   convenzione di documentazione da aggiornare in parallelo).
4. **Nessuna nuova pagina**: il gruppo va dentro `mcp_page` esistente perché
   concettualmente è "un tool di sistema in più", non una nuova area di configurazione —
   evita di aggiungere una voce alla sidebar per una singola feature.
5. **Traduzioni**: tutte le stringhe nuove nel Blueprint vanno marcate `_("...")` come le
   esistenti, per restare compatibili con il meccanismo di traduzione già in uso
   (cartella `po/`).

### Fuori scope per questa fase

* Nessuna UI per la gestione manuale dei prototipi di intenti (resta responsabilità dei
  file skill `.md` esistenti, gestiti da `SkillRegistry`).
* Nessuna UI per la gestione dei `.desktop` indicizzati da `AppSlotMatcher`: l'indicizzazione
  è automatica e trasparente, senza pannello di configurazione dedicato.

---

## File Coinvolti e Modifiche Proposte

### Nuovi Moduli

* **`src/daemon/skills/semantic_router.py`** (o refactor di `vector_intent_matcher.py`):
  `SemanticIntentRouter` — carica ONNX + tokenizer, calcola embedding, similarità coseno
  via numpy, espone interfaccia compatibile con `VectorIntentMatcher.match()`.
* **`src/daemon/skills/app_slot_matcher.py`**: `AppSlotMatcher` — indicizzazione `.desktop`
  e matching fuzzy via RapidFuzz.

### Modifiche a Moduli Esistenti

* **`src/daemon/core/pipeline.py`**:
  * `FastPathDispatcher` usa `SemanticIntentRouter` al posto di `VectorIntentMatcher` allo
    step 2c.
  * Rimozione di `_try_llm_tool_select` e dello step "1.5 Medium Path" in
    `process_text_input` (righe 309-435), solo dopo che i test di regressione confermano
    parità o miglioramento di copertura.
  * `launch_app`/catch-all (righe 224-239) delega ad `AppSlotMatcher` invece del regex
    libero attuale.
* **`src/daemon/core/assistant_runtime.py`**: aggiornare `_handle_fast_path_intent` per il
  nuovo flag `direct_action` nel dizionario di ritorno.
* **`src/daemon/core/runtime_manager.py`**: inizializzazione di `SemanticIntentRouter`
  (caricamento modello ONNX all'avvio del daemon, non lazy, per evitare latenza al primo
  comando).
* **`src/daemon/services/downloader.py`**: download/verifica presenza del modello ONNX in
  `~/.local/share/voice-assistant/models/embeddings/` (percorso da confermare con la
  convenzione già usata per gli altri modelli scaricati, es. STT/wake-word).
* **`data/schemas/org.gnome.shell.extensions.voice-assistant.gschema.xml`**: nuove chiavi
  (vedi sezione GSettings).
* **`data/ui/prefs.blp`** + **`src/gui/settings_window.py`**: nuovo gruppo UI (vedi sezione
  Grafica).
* **`src/daemon/requirements.txt`**: aggiungere esplicitamente `tokenizers`, `rapidfuzz`
  (oggi presenti solo come transitive).

### File Dati (da creare, non da "aggiornare")

* `data/locales/number_words.json` — solo se si decide di esternalizzare `IT_NUMBERS`
  (opzionale, vedi [CORREZIONE 3]).
* `data/locales/responses.json` — solo se si decide di esternalizzare le risposte inline
  (opzionale, vedi [CORREZIONE 3]).

---

## Sequenziamento Raccomandato

1. **Conversione modello**: MiniLM → ONNX, reimplementazione mean-pooling in numpy,
   validazione output contro `sentence-transformers` sullo stesso set di frasi.
2. **`SemanticIntentRouter`** drop-in al posto di `VectorIntentMatcher`, con interfaccia
   compatibile: far girare `tests/test_semantic_dispatch.py` come baseline di
   regressione e ricalibrare la soglia.
3. **`AppSlotMatcher`** per `launch_application`, con test dedicati su app GNOME standard.
4. **UI + GSettings**: nuovo gruppo in `mcp_page`, nuove chiavi schema.
5. **Rimozione Medium-Path LLM** (`_try_llm_tool_select`) solo dopo che i punti 2-3 sono
   stabili in produzione e non mostrano regressioni di copertura.

---

## Piano di Verifica

### Test Unitari Automatizzati

* `tests/test_semantic_dispatch.py` (esistente): estendere con frasi colloquiali e
  varianti con errori STT ("o aprì calendario", "accendi il wifi", "fammi ascoltare un po'
  di musica", "alza un po' il volume") e verificare che frasi non di comando ("spiegami la
  relatività", "chi era Napoleone") restino sotto soglia.
* **Nuovo** `tests/test_app_slot_matcher.py`: matching fuzzy su app GNOME standard
  (Calendar, Calculator, Terminal, Firefox, Files, Settings).
* `tests/test_core_pipeline.py`: aggiornare per il flusso unificato regex + semantico,
  senza più il ramo Medium-Path LLM.

### Test Prestazionali (Latenza)

* Misurare il tempo end-to-end del `SemanticIntentRouter`: target < 15ms su CPU, incluso
  il tempo di tokenizzazione.

### Test Manuale su Sistema Live

* `systemctl --user restart voice-assistant` (verificare prima quale estensione è
  effettivamente installata, vedi nota su `scroker.github.io` vs `mkswap.github.io`).
* "Apri il calendario" / "o aprì calendario" → avvio GNOME Calendar.
* "Attiva il wifi" / "Disattiva il wifi" → toggle via MCP Quick Settings.
* Domanda conversazionale: "Cosa c'è di bello a Roma?" → passaggio a Smart-Path LLM.
* Verifica UI: toggle "Motore di Azione Diretta" in `mcp_page` disabilita correttamente il
  dispatcher diretto; slider soglia cambia il comportamento osservabile su frasi borderline.
