# Piano di stabilizzazione architetturale (review 2026-09-13/14)

> **Destinatario:** Gemini (implementazione). **Review:** Claude.
> **Stato:** da implementare, **una fase alla volta**, nell'ordine di §3.
> **Origine:** review architetturale fatta argomento per argomento con l'utente il 2026-09-13 e 2026-09-14. Ogni decisione è **già confermata dall'utente**: non rimetterla in discussione e non "migliorarla" di tua iniziativa. I punti ancora aperti sono in §11.
> **Come leggere questo documento:** ogni passo ha la forma *File → Cosa fare → Codice di riferimento → Come verificare*. Se un passo non è chiaro, **fermati e chiedi** invece di interpretare.

---

## 0. Prima di iniziare

### 0.1 Base di partenza
- Branch: **`main`**. Commit di partenza: `9b6757d` ("add speaker identification and multi-chat conversation contexts").
- Controlla prima di iniziare: `git status` deve essere pulito e `git log -1 --oneline` deve mostrare `9b6757d` o un commit successivo fatto seguendo questo piano.
- Il lavoro Speaker ID segue anche `docs/speaker-identification-plan.md` e `docs/fix-plan-multichat-speaker-id.md`. **Dove c'è conflitto vale questo documento** (§10.7 elenca le differenze).

### 0.2 Regole obbligatorie (valgono per tutte le fasi)

**R1 — Mai lanciare tutta la suite di test senza limite di memoria.**
`tests/test_gui.py` oggi entra in un ciclo infinito e consuma circa 100 MB al secondo fino a esaurire la RAM della macchina (causa in §4.1). Finché la Fase 0 non è finita:
- non eseguire `pytest` senza argomenti;
- non eseguire `meson test`;
- esegui **un file di test alla volta**, dentro un cgroup con limite di memoria:

```bash
systemd-run --user --scope -p MemoryMax=1500M -p MemorySwapMax=0 \
  timeout 180 python3 -m pytest -q tests/test_NOME.py
```

Se il processo viene ucciso (exit 137) hai superato il limite: **non alzarlo**, cerca la causa.
Attenzione: `systemd-run` espande da solo le variabili `$VAR` scritte nella riga di comando. Se ti servono variabili, mettile in uno script e passa lo script.

**R2 — Una fase = uno o più commit piccoli.** Ogni commit deve lasciare verdi i test dei file toccati. Non mescolare fasi diverse nello stesso commit. Messaggi di commit in inglese, formato `tipo(ambito): descrizione` (es. `fix(tests): stop MagicMock idle source loop in test_gui`).

**R3 — Non rimuovere motori o runtime.** Vosk, Whisper (faster-whisper), Piper, eSpeak, OpenWakeWord, resemblyzer, Ollama e i provider cloud **restano**. Si cambiano solo i *predefiniti* e il *modo di installazione*. Rimuovi solo ciò che §5 elenca esplicitamente.

**R4 — Lingua.** Codice, nomi, commenti, docstring, messaggi di log e descrizioni dello schema GSettings: **inglese**. Testi mostrati all'utente: **inglese nel codice, marcati per gettext** (`_("...")`), con la traduzione italiana nei file `.po` (§6.7). Questo documento è in italiano solo perché è destinato a te.

**R5 — Nessuna rete e nessun modello nei test.** Nessun test può scaricare file, chiamare API esterne o richiedere `torch`. Usa oggetti finti.

**R6 — Non inventare API.** Se usi una libreria (sherpa-onnx, llama-cpp-python, GLib…), verifica il nome esatto di classi e parametri sulla versione installata prima di scrivere codice, per esempio con:

```bash
python3 -c "import sherpa_onnx as s; print([n for n in dir(s) if 'Speaker' in n])"
```

EGO (§0.3) rifiuta esplicitamente codice con "imaginary API usage".

**R7 — Dopo ogni passo, esegui i comandi di "Verifica" indicati.** Se una verifica fallisce, non passare al passo successivo.

### 0.3 Vincolo di distribuzione: extensions.gnome.org (EGO)
L'estensione verrà pubblicata su **extensions.gnome.org**. Il revisore legge **tutto** lo zip, incluso il daemon Python (~27.000 righe). Regole citate testualmente dalle linee guida ufficiali, da rispettare in **ogni** fase:

| Regola EGO (testuale) | Conseguenza pratica |
|---|---|
| "Extensions **MUST NOT** import `Gdk`, `Gtk` or `Adw` in the GNOME Shell process" | `src/extension.js` non importa né usa Gtk/Gdk/Adw (§7.4) |
| "Don't create or modify anything before `enable()` is called" | Niente lavoro al caricamento del modulo JS |
| "Any objects or widgets created by an extension **MUST** be destroyed in `disable()`" (idem segnali e sorgenti del main loop) | Ogni `connect`, `timeout_add`, widget creato in `enable()` va rimosso in `disable()` |
| "Extensions **MUST NOT** include binary executables or libraries" | Nessun binario nello zip; il venv non va nello zip (§7.3) |
| "Scripts **MUST** be written in GJS, unless absolutely necessary" | Il daemon Python è giustificato; niente script shell superflui |
| "Processes **MUST** be spawned carefully and exit cleanly" | Tutti i processi figli terminano quando l'estensione viene disattivata (§8.3) |
| "Extensions **MUST NOT** use any telemetry tools to track users and share the user data online" | Nessun invio automatico di dati; segnalazioni solo su azione dell'utente (§6.4) |
| "Extension **MUST NOT** print excessively to the log" | Log INFO ridotti (§6.3) |
| Rifiuto di "large amounts of unnecessary code, inconsistent code style, imaginary API usage, comments serving as LLM prompts" | Codice morto rimosso (§5), stile uniforme, niente commenti tipo "istruzioni per l'AI" |

---

## 1. Obiettivo

Rendere il progetto **stabile, testabile e pubblicabile su EGO**:
1. una suite di test che non può più bloccare la macchina, eseguita in CI su GitHub Actions (Fase 0);
2. rimozione del codice morto o duplicato già individuato (Fase 1);
3. regole uniformi per GLib, errori, log, privacy, crash e segnalazioni (Fase 2);
4. installazione pulita da zip conforme a EGO (Fase 3);
5. un daemon diviso per dominio, con i motori ML isolati in processi separati (Fase 4);
6. motori predefiniti leggeri (sherpa-onnx dove utilizzabile), dipendenze installate solo quando servono, GPU solo per l'LLM (Fase 5);
7. un gate Speaker ID che non rifiuta l'utente legittimo per lentezza della macchina (Fase 6).

## 2. Fuori perimetro (NON implementare)
- Barriera Speaker ID **per singolo tool** (resta rinviata, `speaker-identification-plan.md` §9).
- Mostrare la trascrizione **in tempo reale** nella GUI: oggi non esiste e non va introdotta.
- GPU per STT, TTS, wake word, VAD, router semantico, Speaker ID: **tutto su CPU**. GPU solo per l'LLM locale (§9.9).
- Login GitHub nell'app (OAuth, token) o un server proprio per le segnalazioni.
- Rimozione di motori o runtime (R3).
- Nuove funzionalità non descritte qui.

---

## 3. Ordine delle fasi

| Fase | Sezione | Contenuto | Può iniziare quando |
|---|---|---|---|
| **0** | §4 | Test sicuri, pytest unico, CI, pyright, ruff | subito |
| **1** | §5 | Rimozioni già decise | Fase 0 completa |
| **2** | §6 | Standard trasversali (GLib, errori, log, crash, segnalazioni, checksum, lingua) | Fase 1 completa |
| **3** | §7 | Packaging ed EGO | Fase 2 completa |
| **4** | §8 | Architettura: StateMachine unica, divisione per dominio, processi worker | Fase 2 completa (può procedere in parallelo alla 3 solo se la review lo autorizza) |
| **5** | §9 | Motori, modelli, dipendenze su richiesta, GPU per l'LLM | Fasi 3 e 4 complete |
| **6** | §10 | Latenza del gate Speaker ID | Fasi 4 e 5 complete |

**Regole d'ordine:**
- Non fare refactoring (Fase 4) su codice che la Fase 1 elimina.
- Alla fine di ogni fase: apri una richiesta di review a Claude indicando i commit e l'esito della checklist di §12 per quella fase. **Non iniziare la fase successiva prima dell'approvazione.**

### 3.1 Convenzioni usate nei passi
- I percorsi sono relativi alla radice del repository.
- "Riga ~N" indica la riga al commit `9b6757d`: può essersi spostata, cerca il testo citato.
- I blocchi di codice sono **codice di riferimento**: rispettane nomi, firme e comportamento; puoi adattare dettagli di stile.
- "Verifica" = comandi da eseguire e risultato atteso.

---

## 4. Fase 0 — Test sicuri e CI

### 4.1 Correggere il ciclo infinito di `tests/test_gui.py`

**Causa (verificata con prove):**
1. `test_toggle_mic_uses_active_context_or_creates_new` (riga ~1488) esegue `win._refresh_chats_list = MagicMock()` su una `AssistantWindow` **vera**.
2. Quella finestra, alla creazione, ha chiesto in modo asincrono un proxy D-Bus verso il daemon **vero** in esecuzione sulla macchina.
3. Il primo test che fa girare il main loop GLib è `test_tts_settings_engine_and_voice_change_in_gui` (`while GLib.MainContext.default().iteration(False):`, righe ~511 e ~520). Lì arriva la risposta del proxy: `_on_proxy_created` → `_on_daemon_ready` → `GLib.idle_add(self._refresh_chats_list)` (`src/gui/assistant_window.py:412`). Ma `_refresh_chats_list` ora è il `MagicMock`.
4. Chiamare un `MagicMock` restituisce un altro `MagicMock`, che vale "vero". Per GLib "vero" significa "ripeti": la sorgente idle si ripete per sempre e il mock salva ogni chiamata in memoria.

**Passo 4.1.a — Togliere le assegnazioni dirette di mock.**
File: `tests/test_gui.py`. Queste righe assegnano mock a oggetti veri:

| Riga ~ | Test | Assegnazione |
|---|---|---|
| 1412 | `test_chats_list_has_no_voice_commands_row` | `win.daemon_client.list_conversations_sync = MagicMock(...)` |
| 1442 | `test_transcript_received_does_not_change_current_chat` | `win.daemon_client.list_conversations_sync = MagicMock(...)` |
| 1486 | `test_toggle_mic_uses_active_context_or_creates_new` | `win.daemon_client.toggle_listening_in_context = MagicMock()` |
| 1487 | idem | `win.daemon_client.create_conversation_sync = MagicMock(...)` |
| 1488 | idem | `win._refresh_chats_list = MagicMock()` |

Sostituisci ciascuna con `patch.object` dentro un blocco `with`, così alla fine del blocco il metodo originale torna al suo posto. Esempio per il test di riga ~1478:

```python
def test_toggle_mic_uses_active_context_or_creates_new(self):
    from gui.assistant_window import AssistantWindow

    app = Adw.Application(application_id="org.local.VoiceAssistant.TestMicContext")
    win = AssistantWindow(application=app)
    self.addCleanup(win.destroy)

    with patch.object(win.daemon_client, "toggle_listening_in_context") as toggle, \
         patch.object(win.daemon_client, "create_conversation_sync", return_value="chat-new-999") as create, \
         patch.object(win, "_refresh_chats_list", return_value=False):
        # Case 1: active chat
        win._current_context_id = "chat-active-123"
        win._on_toggle_mic(win.mic_btn)
        toggle.assert_called_with("chat-active-123")
        create.assert_not_called()

        # Case 2: no current chat
        toggle.reset_mock()
        win._current_context_id = ""
        win._on_toggle_mic(win.mic_btn)
        create.assert_called_once()
        self.assertEqual(win._current_context_id, "chat-new-999")
        toggle.assert_called_with("chat-new-999")
```

Nota `return_value=False` su `_refresh_chats_list`: anche se il mock finisse in `idle_add`, restituirebbe "falso" e GLib non lo ripeterebbe.

**Passo 4.1.b — Distruggere le finestre a fine test.**
Oggi nessun test di `TestGUI` distrugge finestre o applicazioni (`grep -c "tearDown\|addCleanup\|destroy" tests/test_gui.py` → 0). In **ogni** test che crea `AssistantWindow(...)` o `_SettingsWindow()`, subito dopo la creazione aggiungi:

```python
self.addCleanup(win.destroy)
```

**Passo 4.1.c — Tetto ai cicli del main loop.**
Aggiungi in cima a `tests/test_gui.py` (dopo gli import) questa funzione e sostituisci **tutti** i `while GLib.MainContext.default().iteration(False): pass` con una sua chiamata:

```python
def drain_main_loop(max_iterations: int = 1000) -> None:
    """Process pending GLib events, failing loudly if they never settle."""
    context = GLib.MainContext.default()
    for _ in range(max_iterations):
        if not context.iteration(False):
            return
    raise AssertionError(
        f"GLib main loop still busy after {max_iterations} iterations: "
        "a source is probably rescheduling itself forever"
    )
```

**Verifica 4.1:**
```bash
grep -nE "^\s+[a-z_]+(\.[a-z_]+)+ = MagicMock\(" tests/test_gui.py   # nessun risultato
grep -n "iteration(False)" tests/test_gui.py                            # solo dentro drain_main_loop
systemd-run --user --scope -p MemoryMax=800M -p MemorySwapMax=0 \
  timeout 120 python3 -m pytest -q \
  "tests/test_gui.py::TestGUI::test_toggle_mic_uses_active_context_or_creates_new" \
  "tests/test_gui.py::TestGUI::test_tts_settings_engine_and_voice_change_in_gui"
```
Prima della correzione questa coppia di test andava in OOM a 800 MB in circa 8 secondi, anche con il daemon acceso. Dopo deve passare in pochi secondi.

### 4.2 Isolare i test dal sistema reale

**Passo 4.2.a — Fixture globale in `tests/conftest.py`.**
Il file oggi aggiunge i percorsi a `sys.path` e imposta `VOICE_ASSISTANT_CONFIG_DIR` e `GSETTINGS_BACKEND=memory`. **Mantieni** quelle righe e aggiungi, **prima di qualsiasi import del progetto**, il reindirizzamento delle cartelle utente:

```python
# Redirect every per-user directory to a throwaway location BEFORE project
# modules are imported: several modules compute paths at import time.
_test_home = tempfile.TemporaryDirectory(prefix="va_test_home_")
os.environ["HOME"] = _test_home.name
os.environ["XDG_DATA_HOME"] = os.path.join(_test_home.name, ".local", "share")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_test_home.name, ".config")
os.environ["XDG_CACHE_HOME"] = os.path.join(_test_home.name, ".cache")
for _d in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
    os.makedirs(os.environ[_d], exist_ok=True)
```

Poi rendi disponibile lo schema GSettings compilando quello del repository in una cartella temporanea (senza, `Gio.Settings.new` fa terminare Python con abort):

```python
import shutil
import subprocess

_schema_dir = tempfile.TemporaryDirectory(prefix="va_test_schemas_")
shutil.copy(
    _root / "data" / "schemas" / "org.gnome.shell.extensions.voice-assistant.gschema.xml",
    _schema_dir.name,
)
subprocess.run(["glib-compile-schemas", _schema_dir.name], check=True)
os.environ["GSETTINGS_SCHEMA_DIR"] = _schema_dir.name
```

**Passo 4.2.b — Rimuovere i percorsi scritti a mano nei test.**
`tests/test_download_progress.py:9`, `tests/test_core_pipeline.py:9`, `tests/test_services_tts.py:9`, `tests/test_services_llm.py:8`, `tests/test_providers.py:8` (5 file) cercano pacchetti nel venv dell'estensione installata dell'utente (`~/.local/share/gnome-shell/extensions/voice-assistant@scroker.github.io/daemon/venv/...`). Elimina quel blocco: i test devono usare solo l'interprete con cui vengono lanciati. Se un test richiede un pacchetto opzionale non installato, usa `pytest.importorskip("nome")`.

**Passo 4.2.c — `SmartPathController` e `VectorStore`.**
`tests/test_smart_path_controller.py` crea `SmartPathController()` 12 volte; ognuna apre `~/.local/share/voice-assistant/rag_store.db` e avvia un thread di sincronizzazione mai fermato. Con la fixture 4.2.a il database finisce in una cartella temporanea, ma aggiungi comunque dopo ogni creazione:

```python
self.addCleanup(controller.vector_store.close)
```

In `src/daemon/services/rag_store.py` correggi `close()`: oggi fa `join(timeout=5)` mentre il thread dorme 30 s (`time.sleep(self.sync_interval)`), quindi il thread sopravvive. Sostituisci lo `sleep` con un `threading.Event`:

```python
# __init__: prima di creare il thread
self._stop_event = threading.Event()

def _periodic_sync(self) -> None:
    while not self._stop_event.wait(self.sync_interval):
        try:
            self._sync_to_db()
        except Exception:
            logger.exception("[VectorStore] Periodic sync failed")

def close(self) -> None:
    self._stop_event.set()
    if self._sync_thread.is_alive():
        self._sync_thread.join(timeout=5)
    self._sync_to_db()
```
Rimuovi `self._sync_running` (non serve più).

**Passo 4.2.d — Test GUI su un bus D-Bus privato.**
I test GUI non devono mai raggiungere il daemon reale. In CI e in locale vanno lanciati così:

```bash
dbus-run-session -- python3 -m pytest -q tests/test_gui.py
```
`dbus-run-session` crea un session bus vuoto: nessun daemon risponde, e `_on_daemon_ready` non viene mai chiamato. Scrivilo anche in `docs/development.md` (§4.3).

**Passo 4.2.e — Timeout per singolo test.**
Aggiungi `pytest-timeout` alle dipendenze di sviluppo (§4.4.b) e in `pytest.ini`:

```ini
[pytest]
asyncio_mode = auto
testpaths = tests
timeout = 60
```

**Verifica 4.2:**
```bash
ls ~/.local/share/voice-assistant/rag_store.db -l   # annota data/ora
dbus-run-session -- systemd-run --user --scope -p MemoryMax=1500M -p MemorySwapMax=0 \
  timeout 180 python3 -m pytest -q tests/test_smart_path_controller.py
ls ~/.local/share/voice-assistant/rag_store.db -l   # data/ora invariata (a meno che il daemon vero l'abbia toccato)
grep -rn "gnome-shell/extensions" tests/            # nessun risultato
```

### 4.3 Pytest come unico punto di ingresso

**Passo 4.3.a** — `tests/meson.build`: elimina **tutte** le 46 voci `test(...)`. Se il file resta vuoto, eliminalo e rimuovi `subdir('tests')` dal `meson.build` principale (controlla con `grep -n "subdir" meson.build`).

**Passo 4.3.b** — `docs/development.md`: sostituisci ogni riferimento a `meson test -C build` con:
```bash
dbus-run-session -- python3 -m pytest -q
```
e aggiungi un avviso: *"Run the suite with a memory cap while developing: `systemd-run --user --scope -p MemoryMax=3G -p MemorySwapMax=0 …`"*. Aggiorna anche la tabella dei test (righe ~100–135) togliendo le righe dei file eliminati in Fase 1.

### 4.4 CI su GitHub Actions

**Passo 4.4.a — Scoprire le dipendenze dei test.** Non indovinare. In un venv pulito:
```bash
python3 -m venv --system-site-packages /tmp/va-ci-venv
/tmp/va-ci-venv/bin/pip install pytest pytest-timeout pytest-asyncio numpy
/tmp/va-ci-venv/bin/python -m pytest --collect-only -q 2>&1 | grep -E "ModuleNotFoundError|ImportError"
```
Per ogni modulo mancante: se è una dipendenza **di base** del daemon (es. `dasbus`, `notify2`), aggiungila a `requirements-dev.txt`; se è **opzionale** (vosk, sherpa_onnx, faster_whisper, llama_cpp, piper, openwakeword, resemblyzer, torch), rendi il test tollerante con `pytest.importorskip`. Ripeti finché la raccolta non dà errori.

**Passo 4.4.b — `requirements-dev.txt`** (nuovo, radice del repo): elenca pytest, pytest-timeout, pytest-asyncio, ruff, pyright e le dipendenze di base trovate al passo 4.4.a, con versione minima (`pytest>=8`).

**Passo 4.4.c — Configurazione ruff e pyright** in `pyproject.toml` (nuovo, radice):

```toml
[tool.ruff]
line-length = 120
target-version = "py311"
src = ["src/daemon", "src/gui", "src"]
extend-exclude = ["src/daemon/venv", "experiments", ".claude", "build", "builddir"]

[tool.ruff.lint]
select = ["E", "F", "W", "C901", "PLR0915", "BLE001", "S110"]

[tool.ruff.lint.mccabe]
max-complexity = 15

[tool.ruff.lint.pylint]
max-statements = 60

[tool.pyright]
typeCheckingMode = "basic"
include = ["src/daemon", "src/gui"]
exclude = ["src/daemon/venv", "**/__pycache__"]
extraPaths = ["src/daemon", "src/daemon/core", "src"]
```

**Passo 4.4.d — Workflow** `.github/workflows/ci.yml` (nuovo):

```yaml
name: CI
on:
  push:
    branches: [main]
  pull_request:

jobs:
  tests:
    # Fedora, not Ubuntu 24.04: the UI uses Adw.ButtonRow (libadwaita >= 1.6),
    # while Ubuntu 24.04 ships libadwaita 1.5.
    runs-on: ubuntu-24.04
    container:
      image: registry.fedoraproject.org/fedora:latest
      # Real memory cap (cgroup), same idea as systemd-run MemoryMax.
      # Do NOT use "ulimit -v": it caps virtual address space and breaks the
      # suite (MemoryError in pytest, fatal LLVM error in Mesa's llvmpipe).
      options: --memory 4g --memory-swap 4g
    timeout-minutes: 30
    steps:
      - name: System packages
        run: |
          # python3-dbus: notify2 imports the 'dbus' module (dbus-python)
          dnf install -y git python3 python3-pip python3-gobject python3-dbus gtk4 libadwaita portaudio \
            /usr/bin/glib-compile-schemas /usr/bin/dbus-run-session /usr/bin/xvfb-run \
            blueprint-compiler meson ninja-build gettext
      - uses: actions/checkout@v4
      - name: Python deps
        run: |
          python3 -m venv --system-site-packages .venv
          .venv/bin/pip install -r requirements-dev.txt
      - name: Build resources
        run: |
          meson setup build
          meson compile -C build
      - name: Tests
        run: |
          xvfb-run -a dbus-run-session -- .venv/bin/python -m pytest -q

  lint:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      # A venv: Ubuntu 24.04 refuses pip installs into the system Python (PEP 668).
      - run: python3 -m venv .lint-venv && .lint-venv/bin/pip install ruff pyright
      - name: Ruff (new/changed files block, whole tree reports)
        run: |
          .lint-venv/bin/ruff check --exit-zero src
          CHANGED=$(git diff --name-only --diff-filter=AM origin/main -- 'src/**/*.py' || true)
          if [ -n "$CHANGED" ]; then .lint-venv/bin/ruff check $CHANGED; fi
      - name: Pyright (report only for now)
        run: .lint-venv/bin/pyright || true
```

Note obbligatorie:
- `options: --memory 4g --memory-swap 4g` limita la memoria **reale** del container: se un test impazzisce, il job fallisce invece di bloccare il runner. **Non usare `ulimit -v`**: limita lo spazio di indirizzi virtuale e fa fallire la suite anche senza alcun problema reale (verificato: `MemoryError` in pytest dopo 29 test in locale, errore fatale di LLVM dentro Mesa in CI). Con il limite sul container la suite completa passa (499 superati, 11 saltati).
- `xvfb-run` fornisce un display: senza, i test GUI vengono saltati (`_has_display` è falso).
- Se `meson compile` fallisce in CI per dipendenze mancanti, **non togliere il passo**: aggiungi il pacchetto apt mancante.
- pyright è "solo report" (`|| true`) in questa fase; diventerà bloccante nella Fase 4 (§8.5).

**Passo 4.4.e — Test che vieta le chiamate GLib dirette.** Nuovo file `tests/test_no_raw_glib_scheduling.py`. In questa fase l'elenco delle eccezioni contiene tutti i punti attuali; la Fase 2 (§6.1) lo svuota.

```python
"""Forbid raw GLib.idle_add / GLib.timeout_add outside the scheduling helpers."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "src"
PATTERN = re.compile(r"GLib\.(idle_add|timeout_add|timeout_add_seconds)\(")
# Files allowed to call GLib directly (the helpers themselves).
ALLOWED = {"daemon/core/glib_scheduling.py"}
# Temporary list of legacy call sites, emptied in Phase 2 (§6.1).
LEGACY = set()  # fill with "relative/path.py:LINE" entries found today


def test_no_raw_glib_scheduling():
    offenders = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if "venv" in path.parts or rel in ALLOWED:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if PATTERN.search(line) and f"{rel}:{lineno}" not in LEGACY:
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, "Use schedule_idle/schedule_periodic instead:\n" + "\n".join(offenders)
```
Per riempire `LEGACY` in questa fase esegui `grep -rnE "GLib\.(idle_add|timeout_add|timeout_add_seconds)\(" src --include=*.py | grep -v venv` (al commit di partenza sono **58** righe) e copia i `percorso:riga`.

### 4.5 Criteri di accettazione della Fase 0
- [ ] `dbus-run-session -- python3 -m pytest -q` completa localmente con `MemoryMax=3G` senza essere ucciso.
- [ ] La coppia di test di §4.1 passa **con il daemon reale acceso**.
- [ ] Nessun test legge o scrive in `~/.local/share/voice-assistant` o nella cartella dell'estensione installata.
- [ ] `tests/meson.build` non contiene voci `test(...)`.
- [ ] Il workflow CI è verde su un branch di prova.

---

## 5. Fase 1 — Rimozioni già decise

Ordine obbligatorio dei passi: 5.1 → 5.2 → 5.3 → 5.4 → 5.5 → 5.6. Un commit per passo.

### 5.1 Fast-Path: il flusso da ottenere

**Oggi** `FastPathDispatcher.dispatch` (`src/daemon/core/pipeline.py`, righe ~223–346) prova, nell'ordine:
1. una regex per "imposta volume a N" (riga ~237);
2. le 19 regex di `data/nlu/fast_path_patterns.json` (riga ~256);
3. le regex `pattern` definite nelle skill (riga ~281);
4. una regex "cattura tutto" per `apri|avvia|lancia <app>` (riga ~301);
5. **solo alla fine** il router semantico, con parametri "di ripiego" scritti a mano (righe ~331–342).
Se il modello del router manca, `SemanticIntentRouter.match` usa un matcher bag-of-words (`VectorIntentMatcher`).

**Decisione dell'utente:** resta **solo il router semantico come primo e unico riconoscitore**. Le regex servono **solo a valle**, per estrarre i parametri (numero del volume, nome dell'app, destinatario della mail) **dopo** che il router ha scelto l'intent. Nessun ripiego bag-of-words.

**Flusso finale di `dispatch(text)`:**
```
text → router semantico → nessun match? → (False, None, {}, None)  [la richiesta va allo Smart-Path/LLM]
                         → match intent X con la skill S
                              → params = S.args + slot estratti con le regex di S.slots
                              → intent_handler(X, params, text, is_voice)
                                   → (False, _)  → (False, None, {}, None)  [va allo Smart-Path]
                                   → (True, "")  → (True, X, params, risposta predefinita dell'intent)
                                   → (True, "testo") → (True, X, params, "testo")
```
Regola: se il gestore dell'intent non riesce (manca un parametro, manca il tool, errore), la richiesta **non** si ferma: prosegue verso lo Smart-Path come se non ci fosse stato match.

### 5.2 Nuovo formato delle skill: chiave `slots`

**Passo 5.2.a — Estrattore dei parametri.** Nuovo file `src/daemon/skills/slot_extractor.py`:

```python
"""Downstream slot extraction for Fast-Path intents.

Regular expressions run only AFTER the semantic router has chosen an intent;
they never decide which intent matches.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger("VoiceAssistant.SlotExtractor")

NUMBER_SLOT = "@number"    # first integer in the text, digits or number words
PERCENT_SLOT = "@percent"  # like @number, clamped to 0..100


def _extract_number(text: str, number_words: Dict[str, int]) -> Optional[int]:
    digits = re.search(r"\b(\d{1,3})\b", text)
    if digits:
        return int(digits.group(1))
    # Longest words first so "ventitre" wins over "venti".
    for word, value in sorted(number_words.items(), key=lambda item: -len(item[0])):
        if re.search(rf"\b{re.escape(word)}\b", text):
            return int(value)
    return None


def extract_slots(slots: Dict[str, str], text: str, number_words: Dict[str, int]) -> Dict[str, Any]:
    """Return {slot_name: value} for every slot found in text."""
    values: Dict[str, Any] = {}
    for name, spec in (slots or {}).items():
        if spec in (NUMBER_SLOT, PERCENT_SLOT):
            number = _extract_number(text, number_words)
            if number is None:
                continue
            values[name] = max(0, min(100, number)) if spec == PERCENT_SLOT else number
            continue
        try:
            match = re.search(spec, text, re.IGNORECASE)
        except re.error:
            logger.warning("Invalid slot regex for '%s': %s", name, spec)
            continue
        if match and match.groups() and match.group(1):
            values[name] = match.group(1).strip()
    return values
```

**Passo 5.2.b — Test** `tests/test_slot_extractor.py` (nuovo). Casi minimi, tutti obbligatori:

| Input | slots | Risultato atteso |
|---|---|---|
| `"imposta il volume a 75"` | `{"volume": "@percent"}` | `{"volume": 75}` |
| `"metti il volume a centocinquanta"` con `{"centocinquanta": 150}` | `{"volume": "@percent"}` | `{"volume": 100}` |
| `"volume a ventitre"` con `{"venti": 20, "ventitre": 23}` | `{"volume": "@number"}` | `{"volume": 23}` |
| `"alza il volume"` | `{"volume": "@percent"}` | `{}` |
| `"apri il terminale"` | `{"app": "(?:apri\|avvia\|lancia)\\s+(?:il\\s+\|la\\s+\|le\\s+\|l'\|i\\s+)?(.+)$"}` | `{"app": "terminale"}` |
| qualunque testo | `{"x": "("}` (regex non valida) | `{}` senza eccezioni |

**Passo 5.2.c — Registro delle skill.** File `src/daemon/skills/skill_registry.py`, funzione `_parse_markdown_skill` (riga ~123). Nel dizionario `skill = {...}` (righe ~230–250):
- **aggiungi** `"slots": metadata.get("slots", {}) if isinstance(metadata.get("slots"), dict) else {},`
- **rimuovi** le chiavi `"pattern"`, `"param_extract"`, `"param_key"`.

Nota sul formato: il parser legge un oggetto JSON **su una sola riga** (`value.startswith("{")` → `json.loads`). Quindi nelle skill le regex vanno scritte come stringhe JSON, **con le barre rovesciate raddoppiate** (`\\s`, non `\s`).

### 5.3 Skill: file da creare, modificare, eliminare

Tutti i file stanno in `src/daemon/skills/default_skills/`. Dopo ogni modifica verifica che la skill venga letta:
```bash
cd src/daemon && python3 -c "from skills.skill_registry import SkillRegistry as R; r=R.from_default_directory(); print(sorted(s['intent'] for s in r.skills))"
```

**5.3.a — Eliminare**
- `brightness.md` (il server MCP in uso, `bilelmoussaoui/gnome-mcp-server`, non ha un tool per la luminosità).

**5.3.b — Modificare `launch_app.md`.** Sostituisci le righe `pattern:`, `param_extract:`, `param_key:` con:
```yaml
slots: {"app": "(?:apri|aprire|avvia|lancia|mostra|mostrami|aprimi)\\s+(?:il\\s+|la\\s+|le\\s+|l'|i\\s+)?(.+)$"}
```
Il nome dello slot è **`app`** (non `app_name`): la risposta predefinita in `data/locales/responses.json` è `"Apro {app}"` e usa quel nome.
e aggiungi ai `triggers` (se mancano): `"apri la calcolatrice"`, `"apri il calendario"`, `"apri le impostazioni"`, `"apri l'orologio"`, `"apri i file"`.

**5.3.c — Creare `volume_set.md`:**
```markdown
---
name: "Set Volume"
intent: set_volume
tool: set_volume
args: {}
slots: {"volume": "@percent"}
triggers:
  - "imposta il volume a cinquanta"
  - "metti il volume al 30 per cento"
  - "porta il volume a 80"
  - "regola il volume a 20"
  - "volume al 70 per cento"
---
Set the system volume to an explicit percentage.
```

**5.3.d — Creare `compose_mail.md`** (oggi esiste solo come regex in `fast_path_patterns.json`):
```markdown
---
name: "Compose Mail"
intent: compose_mail
tool: open_file
args: {}
slots: {"to": "(?:email|mail)\\s+a\\s+([^\\s]+@[^\\s]+|[a-z0-9._-]+)", "subject": "con\\s+oggetto\\s+(.+?)(?:\\s+(?:e\\s+)?(?:con\\s+testo|testo|con\\s+messaggio|messaggio|dicendo)\\s+.+)?$", "body": "(?:con\\s+testo|testo|con\\s+messaggio|messaggio|dicendo)\\s+(.+)$"}
triggers:
  - "scrivi una mail"
  - "componi una nuova email"
  - "nuova email"
  - "manda una email a mario"
  - "invia una mail a luca con oggetto riunione"
---
Open a new email draft, optionally prefilled with recipient, subject and body.
```
Nota: `tool: open_file` corrisponde a quello che `_handle_fast_path_intent` usa già per `compose_mail` (riga ~407). Il gestore legge `params["to"]`, `params["subject"]`, `params["body"]`.

**5.3.e — Creare `open_mail.md`** (oggi "apri la posta" è una regex che lancia l'app `mail`). Serve un intent **diverso** da `launch_app`: il registro scarta una seconda skill con lo stesso intent.
```markdown
---
name: "Open Mail"
intent: open_mail
tool: launch_application
args: {"app": "mail"}
triggers:
  - "apri la posta"
  - "controlla le mail"
  - "apri la posta elettronica"
  - "guarda le email"
---
Open the default mail client.
```

**5.3.f — Controllo di copertura.** Per ognuna delle 19 voci di `data/nlu/fast_path_patterns.json`, verifica che esista una skill con quell'intent e **almeno due trigger** equivalenti. Corrispondenze:

| Intent in `fast_path_patterns.json` | Skill |
|---|---|
| `set_theme_dark` / `set_theme_light` | `theme.md` / `theme_light.md` |
| `get_time` / `get_date` | `datetime.md` / `date.md` |
| `launch_app` (browser, terminale, calcolatrice, calendario, impostazioni, orologio, file) | `launch_app.md` |
| `launch_app` con `app: mail` | `open_mail.md` (nuova) |
| `media_pause` / `media_play` | `media.md` / `media_play.md` |
| `volume_up` / `volume_down` / `mute` | `volume.md` / `volume_down.md` / `mute.md` |
| `compose_mail` (2 voci) | `compose_mail.md` (nuova) |

**5.3.g — Eliminare la cartella legacy `src/daemon/default_skills/`** (contiene `system_control.md/.json` e `theme_control.md/.json`: skill "generiche" che si basano sull'inferenza per parole chiave). In `skill_registry.py`, `from_default_directory` (riga ~280): togli `base_dir / "default_skills"` da `searched_dirs`. Elimina anche la lista incorporata `DEFAULT_SKILLS` (righe ~26–120) e il ramo `if not registry.skills: registry.skills = [...]` (riga ~295): se non ci sono skill, il registro resta vuoto.

**5.3.h — Eliminare** `data/nlu/fast_path_patterns.json` e, in `pipeline.py`, la funzione `load_fast_path_patterns` (riga ~125) e l'attributo di classe `INTENT_PATTERNS` (riga ~179). **Non** eliminare `load_number_words` e `load_fast_path_responses`: servono ancora.

**5.3.i — Risposte predefinite** (`data/locales/responses.json`, sezione `fast_path`, **in entrambe** le lingue `it` ed `en`):
- rinomina la chiave `volume_set` in `set_volume` (il testo resta uguale, usa `{volume}`);
- aggiungi `"open_mail": "Apro la posta."` / `"open_mail": "Opening mail."`;
- aggiungi `"default": "Fatto."` / `"default": "Done."` (usata quando un intent non ha una risposta propria, per esempio una skill personalizzata).

Verifica: `python3 -c "import json;d=json.load(open('data/locales/responses.json'));[print(l, sorted(d[l]['fast_path'])) for l in d]"` deve mostrare in entrambe le lingue `default`, `open_mail`, `set_volume` e **non** `volume_set`.

### 5.4 Codice del Fast-Path

**Passo 5.4.a — Riscrivere `FastPathDispatcher`** (`src/daemon/core/pipeline.py`, classe alla riga ~172, fino alla fine di `dispatch`, riga ~346).

Elimina, oltre al vecchio corpo della classe: l'attributo di classe `IT_NUMBERS` (riga ~178), `INTENT_PATTERNS` (riga ~179), il metodo `_load_skill_patterns` (riga ~195), gli attributi `self._skill_patterns` e `self.intent_patterns`. Poi scrivi la classe così:

```python
class FastPathDispatcher:
    """Direct Action Engine: semantic router first, regex slot filling downstream."""

    def __init__(
        self,
        intent_handler: Optional[Callable[[str, Dict[str, Any], str, bool], Tuple[bool, str]]] = None,
        enabled: bool = True,
        router_notifier: Optional[Callable[[str], None]] = None,
    ):
        self.enabled = enabled
        self.intent_handler = intent_handler
        self._router_notifier = router_notifier
        self.semantic_router = SemanticIntentRouter(
            SkillRegistry.from_default_directory(), notifier=router_notifier
        )
        self.semantic_min_score = SemanticIntentRouter.DEFAULT_MIN_SCORE
        self.number_words = load_number_words()
        self.responses = load_fast_path_responses()

    def reload_skills(self) -> None:
        """Rebuild the router after the Skills console saves or deletes a skill."""
        self.semantic_router = SemanticIntentRouter(
            SkillRegistry.from_default_directory(), notifier=self._router_notifier
        )

    def _default_response(self, intent_name: str, params: Dict[str, Any]) -> str:
        template = self.responses.get(intent_name) or self.responses.get("default", "")
        try:
            return template.format(**params)
        except (KeyError, IndexError, ValueError):
            return template

    def dispatch(self, text: str, is_voice: bool = False) -> Tuple[bool, Optional[str], Dict[str, Any], Optional[str]]:
        no_match: Tuple[bool, Optional[str], Dict[str, Any], Optional[str]] = (False, None, {}, None)
        if not self.enabled:
            return no_match
        clean_text = text.strip().lower()
        if not clean_text:
            return no_match

        match = self.semantic_router.match(clean_text, min_score=self.semantic_min_score)
        if not match:
            return no_match

        intent_name = match["intent"]
        skill = match.get("skill") or {}
        params: Dict[str, Any] = dict(skill.get("args") or {})
        params.update(extract_slots(skill.get("slots") or {}, clean_text, self.number_words))

        if self.intent_handler is None:
            return (True, intent_name, params, self._default_response(intent_name, params))
        try:
            success, custom_response = self.intent_handler(intent_name, params, clean_text, is_voice)
        except Exception:
            logger.exception("[FastPath] Intent handler failed for %s", intent_name)
            return no_match
        if not success:
            return no_match
        return (True, intent_name, params, custom_response or self._default_response(intent_name, params))
```
Import da aggiungere in cima a `pipeline.py`: `from skills.slot_extractor import extract_slots`.

Nello **stesso file**, `PipelineController.__init__` (riga ~356):
1. aggiungi il parametro `router_notifier: Optional[Callable[[str], None]] = None` (ultimo parametro);
2. sostituisci `self.fast_path = FastPathDispatcher(enabled=fast_path_enabled)` (riga ~378) con `self.fast_path = FastPathDispatcher(enabled=fast_path_enabled, router_notifier=router_notifier)`;
3. in `process_text_input` (riga ~663) sostituisci `self.fast_path.dispatch(text)` con `self.fast_path.dispatch(text, is_voice=speak)`. `speak` è già uguale a `is_voice`: lo passa `assistant_runtime.py` alla riga ~1272 (`speak=is_voice`).

Verifica: `grep -n "IT_NUMBERS\|INTENT_PATTERNS\|_skill_patterns\|intent_patterns" src/daemon/core/pipeline.py` → nessun risultato.

**Passo 5.4.b — `_handle_fast_path_intent`** (`src/daemon/core/assistant_runtime.py`, riga ~336).
1. Firma nuova: `def _handle_fast_path_intent(self, intent_name: str, params, text: str = "", is_voice: bool = False):`. Aggiorna la firma anche in `src/daemon/core/daemon_protocol.py` (riga ~113).
2. Elimina `_load_intent_tool_map` (righe ~40–78), la variabile `_INTENT_TOOL_MAP` (riga ~79) e il file `data/mcp/intent_tools.json`. Togli `<file>mcp/intent_tools.json</file>` da `data/schemas/org.gnome.shell.extensions.voice-assistant.gresource.xml`.
3. `open_mail`: all'inizio della funzione, **prima** di `if intent_name == "get_time":`, inserisci
   ```python
   if intent_name == "open_mail":
       intent_name, params = "launch_app", {"app": (params or {}).get("app", "mail")}
   ```
4. Ramo "senza MCP" (`if not self.owner.mcp_manager:`, riga ~345): l'ultima riga del ramo `return (False, "")` diventa `return self._execute_skill(intent_name, text, params=params, is_voice=is_voice)`. Così le skill personalizzate di tipo `response`, `command` e `prompt` funzionano anche senza MCP.
5. `set_volume` (riga ~425): se lo slot manca, **non** usare 50. Sostituisci il ramo con:
   ```python
   if intent_name == "set_volume":
       if "volume" not in params:
           return (False, "")
       vol = max(0, min(100, int(params["volume"])))
       ok = is_tool_success(run_tool("set_volume", {"volume": float(vol)}))
       return (ok, "")
   ```
6. `launch_app` (riga ~429): elimina il blocco `if not app and text: m = _re.search(...)` (righe ~431–437): il nome dell'app arriva solo dallo slot `app`. Se `app` è vuoto, restituisci `(False, "")`. Se dopo questa modifica `_re` non è più usato nel file, togli anche il suo import.
7. Elimina **tutto** il ramo `if intent_name == "system_control":` (riga ~471, fino al suo `return (False, "")` compreso).
8. Ramo del tema (riga ~503): sostituisci la tupla con `("set_theme_dark", "set_theme_light")` ed elimina la riga `if dark is None and mode is None and intent_name == "theme_control": return (False, "")`.
9. Sostituisci il blocco finale `# --- Tabella statica ---` (`mapping = _INTENT_TOOL_MAP.get(...)`, riga ~515) con:
   ```python
   return self._execute_skill(intent_name, text, params=params, is_voice=is_voice)
   ```
10. `_execute_skill` (riga ~154) diventa:
   ```python
   def _execute_skill(self, intent: str, user_text: str, params=None, is_voice: bool = False):
       """Run a matched skill; returns (success, response_text)."""
       skill = self.skill_registry.find_by_intent(intent)
       if not skill:
           return (False, "")
       llm_fallback = None
       if getattr(self.owner, "llm_service", None):
           llm_fallback = lambda prompt: "".join(self.owner.llm_service.stream_tokens(prompt))
       success, response, _result = SkillExecutor(skill).execute(
           user_text,
           mcp_manager=self.owner.mcp_manager,
           llm_fallback=llm_fallback,
           is_voice=is_voice,
           params=params,
       )
       return (success, response)
   ```
11. `src/daemon/core/runtime_manager.py`, `initialize_pipeline` (riga ~1233): nella chiamata `PipelineController(...)` aggiungi `router_notifier=...` (valore in §5.5.b). La riga `self.owner.pipeline_controller.fast_path.intent_handler = self.owner._handle_fast_path_intent` resta. Controlla con `grep -n "def _handle_fast_path_intent" -A3 src/daemon/main.py`: se `VoiceAssistant` ha un metodo che delega, aggiorna anche la sua firma con `is_voice`.

**Passo 5.4.c — `SkillExecutor`** (`src/daemon/skills/skill_executor.py`): niente più deduzione dal testo.
1. Elimina: `load_tool_keywords` (riga ~21), `load_standard_responses` (riga ~51), gli attributi di classe `TOOL_KEYWORDS` e `STANDARD_RESPONSES` (righe ~69–72), `_extract_tool_keywords_from_body` (riga ~89), `_infer_action_from_text` (riga ~106, con il ramo `screen_brightness`), `_generate_response` (riga ~374), l'attributo `self.tools_allowed`. Sono usati solo in questo file (verificato con `grep -rn "load_standard_responses\|STANDARD_RESPONSES" src`).
2. Sostituisci `execute` con:
   ```python
   def execute(
       self,
       user_text: str,
       mcp_manager: Optional[Any] = None,
       llm_fallback: Optional[Any] = None,
       is_voice: bool = False,
       params: Optional[Dict[str, Any]] = None,
   ) -> Tuple[bool, str, Optional[Any]]:
       """Execute the skill. An empty response means "use the default response"."""
       if not user_text or not user_text.strip():
           return (False, "", None)

       if self.tool == "date_time" or self.intent in ("get_time", "get_date"):
           from core.locale_utils import get_current_date_str, get_current_time_str
           fmt = self.args.get("format") or ("date" if self.intent == "get_date" else "time")
           resp = get_current_date_str() if fmt == "date" else get_current_time_str()
           return (True, resp, {"action": fmt, "response": resp})

       # KEEP UNCHANGED: the three existing blocks
       #   if self.action_type == "response": ...
       #   if self.action_type == "command": ...
       #   if self.action_type == "prompt": ...

       if self.tool:
           if not mcp_manager:
               return (False, "", None)
           tool_params = dict(self.args)
           tool_params.update(params or {})
           try:
               result = mcp_manager.execute_tool(self.tool, tool_params)
               if asyncio.iscoroutine(result):
                   from core.async_bridge import run_async
                   result = run_async(result, timeout=10.0)
           except Exception:
               logger.exception("[SkillExecutor] Tool %s failed", self.tool)
               return (False, "", None)
           return (True, "", result)

       return (False, "", None)
   ```
   I tre blocchi `response`/`command`/`prompt` si copiano **identici** da quelli di oggi (righe ~247–296). Non cambiarne il comportamento.
3. `data/mcp/known_tools.json`: rimuovi solo la chiave `keywords`. **Non eliminare il file**: `services/tool_call_parser.py:39` legge `active_tools`.
4. `src/daemon/services/tool_call_parser.py` riga ~212: elimina il ramo `elif tool_name == "screen_brightness":`.
5. **Non** eliminare le sezioni per-tool di `responses.json` (`set_volume`, `quick_settings`, …): `tool_call_parser.py` usa gli stessi nomi di tool. In Fase 4 (§8.2) verrà controllato se sono ancora lette.

**Passo 5.4.d — Test da aggiornare** (uno alla volta, con il limite di memoria di R1):
- `tests/test_core_pipeline.py`: `test_fast_path_dispatcher_intents` (riga ~58), `test_fast_path_dispatcher_passes_text_to_intent_handler` (~81), il dispatcher disabilitato (~100), `test_fast_path_mail_intents` (~224). Il router vero richiede il modello: dopo aver creato il dispatcher, sostituisci `dispatcher.semantic_router` con un oggetto finto il cui `match` restituisce `{"intent": ..., "score": 0.9, "skill": {"args": {...}, "slots": {...}}}`. Test **obbligatori** da aggiungere:
  - router che restituisce `None` → `(False, None, {}, None)`;
  - gestore che restituisce `(False, "")` → `(False, None, {}, None)`;
  - gestore che restituisce `(True, "")` → risposta presa da `responses["fast_path"][intent]`, o da `"default"` se l'intent non ce l'ha;
  - `dispatch("imposta il volume a 75", is_voice=True)` con skill `{"slots": {"volume": "@percent"}}` → il gestore riceve `params == {"volume": 75}` e `is_voice is True`.
- `tests/test_e2e_pipeline_integration.py` righe ~241 e ~420: creano `FastPathDispatcher()` vero e si aspettano un match su "alza il volume". Usa lo stesso router finto.
- `tests/test_skill_executor.py`: elimina `test_executor_detects_volume_action_from_text` (~15), `test_executor_detects_theme_action_from_text` (~31), `test_executor_generates_standardized_responses` (~52), `test_executor_detects_tools_from_body_keywords` (~92), `test_executor_handles_volume_set_with_number` (~127), `test_executor_handles_app_launch` (~141). Aggiorna `test_executor_executes_skill_with_mcp_manager` (~68): skill con `tool` e `args`, chiamata con `params=`, verifica che `execute_tool` riceva `args` uniti a `params`. Aggiorna `test_executor_falls_back_to_llm_when_no_tools_match` (~106) e rinominalo `test_executor_without_tool_returns_failure`: skill di tipo `system` senza `tool` → `(False, "", None)`. Gli altri restano.
- `tests/test_skill_markdown_loader.py`: riga ~83 (`"system_control"`) → usa `volume_up` con una skill finta nel registro e verifica che venga chiamato `set_volume` con `{"direction": "up", "relative": True}`; riga ~101 (`"theme_control"`) → usa `set_theme_dark`.
- `tests/test_assistant_runtime.py`: `test_fast_path_intent_routes_to_mcp` (~59) e `test_fast_path_intent_resolves_async_mcp_tool` (~69): fornisci la skill tramite `runtime.skill_registry` (oggetto finto con `find_by_intent`).

### 5.5 Router semantico senza ripiego, con download in background

File: `src/daemon/skills/semantic_router.py`.

**Passo 5.5.a — Codice.**
1. Elimina `from .vector_intent_matcher import VectorIntentMatcher` (riga ~29), `self._fallback = ...` (riga ~75) e, in `match` (riga ~193), sostituisci `return self._fallback.match(...)` con `return None`.
2. Aggiorna la docstring del modulo (righe 1–16) togliendo ogni riferimento al ripiego.
3. Il costruttore oggi scarica il modello **in modo sincrono** (`_try_load` → `_ensure_model_files` → `hf_hub_download`), bloccando l'avvio del daemon. Cambialo così:

```python
def __init__(self, registry=None, model_dir=None, auto_download=True,
             notifier: Optional[Callable[[str], None]] = None):
    self.registry = registry or SkillRegistry.from_default_directory()
    self.model_dir = model_dir or _default_model_dir()
    self._auto_download = auto_download
    self._notifier = notifier
    self._lock = threading.Lock()
    self._session = None
    self._tokenizer = None
    self._available = False
    self._intents: List[str] = []
    self._triggers: List[str] = []
    self._prototype_vectors: Optional[np.ndarray] = None
    self._download_thread: Optional[threading.Thread] = None

    if self._model_files_present():
        self._load()
    elif self._auto_download:
        self._start_background_download()

def _notify(self, event: str) -> None:
    if self._notifier:
        try:
            self._notifier(event)
        except Exception:
            logger.exception("[SemanticRouter] Notifier failed for %s", event)

def _model_files_present(self) -> bool:
    onnx_path, tokenizer_path = self._model_paths()
    return os.path.exists(onnx_path) and os.path.exists(tokenizer_path)

def _start_background_download(self) -> None:
    if self._download_thread and self._download_thread.is_alive():
        return
    self._download_thread = threading.Thread(
        target=self._download_and_load, name="SemanticRouterDownload", daemon=True
    )
    self._download_thread.start()

def _download_and_load(self) -> None:
    self._notify("download_started")
    if not self._download_model_files():   # the old body of _ensure_model_files, without the early return
        self._notify("download_failed")
        return
    self._load()
    self._notify("download_finished" if self._available else "download_failed")
```
`_load()` è il vecchio `_try_load()` senza la chiamata a `_ensure_model_files()`. Imposta `self._available = True` solo alla fine, quando i prototipi sono pronti. Messaggi di log in inglese.

**Passo 5.5.b — Notifica desktop.** In `src/daemon/core/runtime_manager.py` aggiungi a `DaemonRuntimeManager`:

```python
_ROUTER_MESSAGES = {
    "download_started": "Downloading the command recognition model in the background…",
    "download_finished": "Command recognition model ready.",
    "download_failed": "Could not download the command recognition model. Quick commands stay off until the next start.",
}

def on_semantic_router_event(self, event: str) -> None:
    message = self._ROUTER_MESSAGES.get(event)
    if message:
        self.notify_user("Voice Assistant", message)
```
`notify_user(title, message, icon=...)` esiste già (riga ~459). Il notificatore viene chiamato da un thread in background: incapsula la chiamata con `schedule_idle` (§6.1.a). **Se la Fase 2 non è ancora iniziata, crea ora `src/daemon/core/glib_scheduling.py` esattamente come in §6.1.a** e aggiungilo agli ALLOWED del test §4.4.e. In `DaemonRuntimeManager.initialize_pipeline` (`runtime_manager.py`, riga ~1233) passa a `PipelineController(...)` il parametro `router_notifier=lambda event: schedule_idle(self.on_semantic_router_event, event)` (§5.4.a spiega come arriva al `FastPathDispatcher`). Import: `from core.glib_scheduling import schedule_idle`.

**Passo 5.5.c — Eliminare `VectorIntentMatcher`:**
- file `src/daemon/skills/vector_intent_matcher.py`;
- `src/daemon/skills/__init__.py` righe ~4 e ~6 (import ed elemento di `__all__`);
- `src/daemon/core/__init__.py` righe ~11–14 (il blocco `try/except` che lo importa) e il nome in `__all__` (riga ~16);
- `tests/test_semantic_dispatch.py`: elimina i due test che lo usano (righe ~15 e ~24) e `test_fast_path_dispatch_falls_back_to_semantic_matching` (~33); **mantieni** `test_skill_registry_loads_default_skills` (~41);
- `tests/test_semantic_router.py`: elimina `test_falls_back_to_bag_of_words_when_model_missing` (~68) e correggi il messaggio di skip (riga ~28). Aggiungi un test: con `auto_download=False` e cartella del modello vuota, `match("alza il volume")` restituisce `None`.

**Passo 5.5.d — UI "Fast-Path" come `Adw.ExpanderRow`** (decisione: la chiave `fast-path-enabled` resta, cambia solo la UI).
File `data/ui/prefs/subpage_dispatch.blp`: sostituisci **tutto** il gruppo `Adw.PreferencesGroup { title: _("Fast-Path"); ... }` con:

```blueprint
Adw.PreferencesGroup {
  title: _("Fast-Path");
  description: _("Runs frequent commands (volume, theme, media, clock, launching apps) directly, without the language model. A small on-device model recognizes the command; the rest of the sentence is parsed only to read values such as the volume level or the app name.");

  Adw.ExpanderRow dispatch_fast_path_row {
    title: _("Enable Fast-Path");
    subtitle: _("Fastest, deterministic — no language model involved");
    show-enable-switch: true;

    Adw.SpinRow dispatch_semantic_threshold_row {
      title: _("Recognition Confidence Threshold");
      subtitle: _("Minimum similarity required to run a command directly");
      digits: 2;
      adjustment: Gtk.Adjustment {
        lower: 0.50;
        upper: 0.95;
        step-increment: 0.05;
        page-increment: 0.05;
        value: 0.62;
      };
    }
  }
}
```
Nello stesso file correggi anche la descrizione del primo gruppo (riga ~20): cita ancora il "Medium-Path", che non esiste più. Testo nuovo: `_("A recognized command goes through two stages, stopping at the first one that resolves it: Fast-Path, then Smart-Path. Disabling Fast-Path sends every command to Smart-Path.")`.

File `src/gui/components/settings/dispatch.py`, metodo `_setup`: la chiave va collegata a `enable-expansion`, non più ad `active`, e la riga `fast_row.bind_property("active", thresh_row, "sensitive", ...)` va eliminata (l'expander disattiva da solo le righe interne):
```python
_SWITCH_BINDINGS = {
    "dispatch_fast_path_row": "fast-path-enabled",
}
...
for widget_id, key in _SWITCH_BINDINGS.items():
    bind_setting(self.settings, key, self.builder, widget_id, "enable-expansion")
```

Rigenera l'interfaccia (il file compilato `data/ui/prefs.ui` è versionato e i test lo leggono):
```bash
python3 scripts/assemble_blueprints.py
blueprint-compiler compile --output data/ui/prefs.ui data/ui/prefs.blp
```

Aggiorna `tests/test_gui.py`, `test_dispatch_settings_rows_bind_to_gsettings` (riga ~445): sostituisci `fast_row.set_active(True/False)` con `fast_row.set_enable_expansion(True/False)`.

**Verifica 5.1–5.5:**
```bash
grep -rnE "VectorIntentMatcher|vector_intent_matcher|intent_tools\.json|_INTENT_TOOL_MAP|fast_path_patterns|load_fast_path_patterns|INTENT_PATTERNS|_infer_action_from_text|_extract_tool_keywords_from_body|load_tool_keywords|screen_brightness|brightness_up|system_control|theme_control|param_extract|param_key|IT_NUMBERS|_skill_patterns|tools_allowed|STANDARD_RESPONSES|volume_set" src tests data --include=*.py --include=*.json --include=*.md --include=*.blp
# Atteso: nessun risultato (tranne eventuali file temporanei creati dentro i test)
ls src/daemon/default_skills 2>&1        # "No such file or directory"
```
e i test di `test_slot_extractor.py`, `test_core_pipeline.py`, `test_e2e_pipeline_integration.py`, `test_skill_executor.py`, `test_skill_markdown_loader.py`, `test_semantic_dispatch.py`, `test_semantic_router.py`, `test_assistant_runtime.py`, uno alla volta con il limite di memoria (R1).

### 5.6 Rimuovere la pipeline di streaming mai collegata

Decisione dell'utente: `streaming_pipeline.py` e `pipeline_integration.py` non sono importati dal daemon (verificato: nessun import in `main.py` o `core/runtime_manager.py`), quindi si eliminano.

**Passo 5.6.a — File da eliminare** (`git rm`):
- `src/daemon/core/streaming_pipeline.py`
- `src/daemon/core/pipeline_integration.py`
- `tests/test_streaming_pipeline.py`
- `tests/test_pipeline_integration_adapter.py`
- `docs/streaming-pipeline-guide.md`

**Attenzione:** `tests/test_e2e_pipeline_integration.py` ha un nome simile ma prova `PipelineController`: **non** va eliminato.

**Passo 5.6.b — Documentazione da correggere:**
| File | Riga ~ | Cosa fare |
|---|---|---|
| `docs/pipeline.md` | 12 | Elimina la frase "— a differenza di `core/streaming_pipeline.py`/`core/pipeline_integration.py`, che pur documentati in [...] non sono collegati al daemon in esecuzione". Il resto del paragrafo resta. |
| `docs/architecture.md` | 70 | Elimina la riga della tabella che descrive `core/streaming_pipeline.py + core/pipeline_integration.py`. |
| `docs/development.md` | 104, 129, 133 | Elimina le righe della tabella per `test_pipeline_integration_adapter.py` e `test_streaming_pipeline.py`; nella nota di riga 133 togli `test_streaming_pipeline.py` dall'elenco. |
| `docs/future-roadmap.md` | 370, 399, 711 | Elimina i punti che parlano di `StreamingPipelineEngine`/`StreamingPipelineController` e la riga "Streaming Adapter" della tabella. |

**Verifica 5.6:**
```bash
grep -rnE "streaming_pipeline|pipeline_integration|StreamingPipeline|streaming-pipeline-guide" \
  --exclude-dir=venv --exclude-dir=.git --exclude-dir=build --exclude-dir=builddir --exclude-dir=.claude . \
  | grep -v "docs/stability-review-plan.md"
# Atteso: nessun risultato
```

### 5.7 Rimuovere il reporter Bugzilla

Decisione dell'utente: niente Bugzilla. Le segnalazioni passano da GitHub con un'azione dell'utente (§6.4, Fase 2). In questa fase **si elimina soltanto**; il nuovo flusso arriva in Fase 2.

**Passo 5.7.a — Daemon:**
1. Elimina `src/daemon/core/bug_reporter.py`.
2. `src/daemon/main.py`:
   - in `_report_error` (riga ~693) elimina `from core.bug_reporter import BugReporter` e la chiamata `BugReporter.submit_async(...)`; resta solo `ErrorCollector.record_error(...)`. Docstring nuova: `"""Record a critical exception raised by the audio thread."""`;
   - nel blocco `if __name__ == '__main__':` (riga ~798) elimina le righe `from core.logger import set_error_submitted_callback`, `from core.bug_reporter import BugReporter`, `set_error_submitted_callback(BugReporter.submit_async)`.
3. `src/daemon/core/runtime_manager.py`, `fallback_to_vosk_wakeword` (riga ~505): elimina il blocco `try: from core.bug_reporter import BugReporter ... except Exception: pass`; il commento `# 1. Registra su ErrorCollector e BugReporter` diventa `# 1. Record the error`.
4. `src/daemon/core/logger.py`: **non** eliminare `set_error_submitted_callback` né `_error_submitted_callback` (servono in §6.4). Togli solo dalla docstring (riga ~503) la frase su BugReporter/Bugzilla.

**Passo 5.7.b — GUI:**
1. Elimina `src/gui/components/settings/bugreport.py`.
2. `src/gui/components/settings/__init__.py`: elimina `from .bugreport import BugReportSettings` (riga ~17) e `"BugReportSettings"` da `__all__` (riga ~37).
3. `src/gui/components/__init__.py`: elimina `BugReportSettings,` dall'import (riga ~16) e da `__all__` (riga ~33).
4. `src/gui/settings_window.py`: elimina `BugReportSettings,` dai due import (righe ~38 e ~58) e la riga `self.bugreport_settings = BugReportSettings(...)` (riga ~319).
5. `src/gui/components/settings/general.py`: elimina il blocco `bugreport_subpage_row = ...` fino a `bugreport_subpage_row.connect("activated", _open_bugreport)` (righe ~217–224).

**Passo 5.7.c — Interfaccia:**
1. Elimina `data/ui/prefs/subpage_bugreport.blp`.
2. `data/ui/prefs/page_general.blp`: elimina l'intero blocco `Adw.ActionRow bugreport_subpage_row { ... }` (riga ~80).
3. `scripts/assemble_blueprints.py`: togli `"subpage_bugreport.blp",` dall'elenco (riga ~29).
4. Rigenera:
   ```bash
   python3 scripts/assemble_blueprints.py
   blueprint-compiler compile --output data/ui/prefs.ui data/ui/prefs.blp
   grep -c bugreport data/ui/prefs.blp data/ui/prefs.ui   # atteso: 0 e 0
   ```

**Passo 5.7.d — Schema GSettings** (`data/schemas/org.gnome.shell.extensions.voice-assistant.gschema.xml`, righe ~235–259): elimina le 5 chiavi `bugreport-enabled`, `bugreport-endpoint`, `bugreport-api-key`, `bugreport-product`, `bugreport-component`. Verifica: `glib-compile-schemas --strict --dry-run data/schemas` senza errori. Se `data/schemas/gschemas.compiled` è versionato (`git ls-files data/schemas`), rigeneralo con `glib-compile-schemas data/schemas`.

**Passo 5.7.e — Test** (`tests/test_gui.py`):
- elimina `self.assertIsNotNone(win.bugreport_settings)` (riga ~262) e l'intero `test_bugreport_button_row` (riga ~266);
- riga ~1174: togli `"bugreport_apikey_row"` dalla tupla;
- righe ~1203–1224: elimina le righe che cercano `bugreport_subpage_row`/`bugreport_subpage` e la simulazione della loro apertura; il resto del test resta.

**Passo 5.7.f — Documentazione:** `docs/gsettings.md` righe ~212–216 (le 5 chiavi); `docs/architecture.md` riga ~73 (riga di `core/bug_reporter.py`), riga ~216 (togli "BugReport" dall'elenco), riga ~338 (voce "Bug Reporting"); `docs/development.md` riga ~200 (`bugreport.py`) e riga ~255 (`subpage_bugreport.blp`).

**Verifica 5.7:**
```bash
grep -rniE "bugzilla|bug_reporter|BugReporter|bugreport" \
  --exclude-dir=venv --exclude-dir=.git --exclude-dir=build --exclude-dir=builddir --exclude-dir=.claude . \
  | grep -v "docs/stability-review-plan.md"
# Atteso: nessun risultato
```

### 5.8 Criteri di accettazione della Fase 1
- [ ] Tutte le verifiche di §5.1–§5.7 danno il risultato atteso.
- [ ] `dbus-run-session -- python3 -m pytest -q` (con `MemoryMax=3G`) è verde.
- [ ] Prova manuale con il daemon installato e `fast-path-enabled` attivo: "alza il volume", "imposta il volume a 30", "apri il terminale", "apri la posta", "che ore sono" vengono eseguiti **senza** LLM; "raccontami una barzelletta" va allo Smart-Path.
- [ ] Prova manuale con la cartella del modello del router cancellata (`~/.local/share/voice-assistant/models/...`, percorso in `_default_model_dir()` di `semantic_router.py`): il daemon parte subito, compare la notifica di download, a download finito il Fast-Path funziona senza riavvio.
- [ ] Il Fast-Path nelle impostazioni è una riga espandibile con l'interruttore integrato.

---

## 6. Fase 2 — Standard trasversali

Ordine obbligatorio: 6.1 → 6.2 → 6.3 → 6.4 → 6.5 → 6.6 → 6.7. Un commit per passo (o più commit piccoli).

### 6.1 GLib: nessuna callback può ripetersi per sbaglio

**Problema.** In GLib una callback di `idle_add`/`timeout_add` che restituisce un valore "vero" viene **richiamata di nuovo, per sempre** (è la causa dell'OOM di §4.1). Oggi ci sono 58 chiamate dirette e solo 5 sono protette da `glib_safe` (`core/logger.py:577`). Inoltre il watchdog di `main.py:_start_speaking_watchdog` (riga ~773) crea un nuovo timer a ogni passaggio in `speaking` senza fermare il precedente.

**Passo 6.1.a — Nuovo file `src/daemon/core/glib_scheduling.py`** (nessun import del progetto, solo GLib e logging, così lo può usare anche la GUI):

```python
"""Main-loop scheduling helpers.

Every GLib callback in the project is scheduled through these helpers, so a
callback can never repeat by accident: GLib re-runs an idle/timeout source
whenever the callback returns a truthy value.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from gi.repository import GLib

logger = logging.getLogger("VoiceAssistant.GLib")


def _run_once(fn: Callable[..., Any], args: tuple) -> bool:
    try:
        fn(*args)
    except Exception:
        logger.exception("Unhandled exception in main-loop callback %s", getattr(fn, "__qualname__", fn))
    return GLib.SOURCE_REMOVE


def schedule_idle(fn: Callable[..., Any], *args: Any) -> int:
    """Run fn(*args) once on the main loop. The return value of fn is ignored."""
    return GLib.idle_add(_run_once, fn, args)


def schedule_once(delay_ms: int, fn: Callable[..., Any], *args: Any) -> int:
    """Run fn(*args) once after delay_ms milliseconds. Returns the source id."""
    return GLib.timeout_add(delay_ms, _run_once, fn, args)


def cancel(source_id: Optional[int]) -> None:
    """Remove a pending source; safe if it already ran or was removed."""
    if source_id and GLib.MainContext.default().find_source_by_id(source_id) is not None:
        GLib.source_remove(source_id)


class PeriodicTimer:
    """Call fn every interval_ms until fn returns something other than True, or stop() is called."""

    def __init__(self, interval_ms: int, fn: Callable[[], Any]):
        self._interval_ms = interval_ms
        self._fn = fn
        self._source_id = 0

    @property
    def running(self) -> bool:
        return self._source_id != 0

    def start(self) -> None:
        self.stop()
        self._source_id = GLib.timeout_add(self._interval_ms, self._tick)

    def stop(self) -> None:
        cancel(self._source_id)
        self._source_id = 0

    def _tick(self) -> bool:
        try:
            keep_going = self._fn() is True
        except Exception:
            logger.exception("Unhandled exception in periodic callback %s", getattr(self._fn, "__qualname__", self._fn))
            keep_going = False
        if not keep_going:
            self._source_id = 0
        return GLib.SOURCE_CONTINUE if keep_going else GLib.SOURCE_REMOVE
```
Le API `GLib.MainContext.find_source_by_id`, `GLib.SOURCE_REMOVE` (= `False`) e `GLib.SOURCE_CONTINUE` (= `True`) sono state verificate sul PyGObject installato.

**Passo 6.1.b — Test** `tests/test_glib_scheduling.py` (nuovo), tutti obbligatori. Per far girare il main loop usa `drain_main_loop` (copiala da §4.1.c in un modulo `tests/glib_helpers.py` e importala da lì in entrambi i file):
- `schedule_idle` con una callback che restituisce `MagicMock()` (valore "vero") → chiamata **una sola volta** dopo `drain_main_loop()`;
- una callback che solleva un'eccezione → nessuna eccezione esce, il log contiene "Unhandled exception";
- `schedule_once(10, fn)` seguito da `cancel(id)` → `fn` non viene mai chiamata; `cancel` chiamato due volte non produce warning GLib;
- `PeriodicTimer` con una callback che restituisce `True` due volte e poi `None` → chiamata 3 volte, poi `running is False`;
- `PeriodicTimer.start()` chiamato due volte di seguito → un solo timer attivo (conta le chiamate).

**Passo 6.1.c — GUI: rendere importabile il modulo.** In `src/gui/main.py`, subito dopo il blocco che aggiunge `_GUI_DIR` a `sys.path` (riga ~11), aggiungi anche `src/daemon/core`:
```python
_DAEMON_CORE_DIR = os.path.join(os.path.dirname(_GUI_DIR), "daemon", "core")
if _DAEMON_CORE_DIR not in sys.path:
    sys.path.insert(0, _DAEMON_CORE_DIR)
```
Nella GUI l'import è `from glib_scheduling import schedule_idle` (come già fa `assistant_window.py` con `from logger import glib_safe`). Nel daemon l'import è `from core.glib_scheduling import schedule_idle`.

**Passo 6.1.d — Sostituire tutte le chiamate dirette.** Elenco completo al commit di partenza (dopo la Fase 1 alcune righe non esistono più: `bugreport.py` è stato eliminato):

| File | Riga ~ | Oggi | Diventa |
|---|---|---|---|
| `daemon/main.py` | 769 | `GLib.idle_add(self.set_state, "idle")` | `schedule_idle(self.set_state, "idle")` |
| `daemon/main.py` | 784 | watchdog con `GLib.timeout_add(1000, _check)` | vedi 6.1.e |
| `daemon/core/lifecycle.py` | 29, 94 | `GLib.idle_add(_emit)`, `GLib.idle_add(_do_set_state)` | `schedule_idle(_emit)`, `schedule_idle(_do_set_state)`; togli i `return False` interni (non servono più) |
| `daemon/core/runtime_manager.py` | 453, 455, 457 | `GLib.idle_add(launcher, ...)`, `GLib.idle_add(self.owner.ShowWindow)` | `schedule_idle(...)` con gli stessi argomenti |
| `daemon/core/runtime_manager.py` | 1272 | `self.owner._model_idle_watch_id = GLib.timeout_add_seconds(30, self._check_model_idle)` | `self._model_idle_timer = PeriodicTimer(30_000, self._check_model_idle); self._model_idle_timer.start()`; `_check_model_idle` deve terminare con `return True` esplicito; togli `_model_idle_watch_id` anche da `daemon_protocol.py` (riga ~99) |
| `daemon/core/assistant_runtime.py` | 560, 593, 595, 1318 | `GLib.idle_add(self.owner.set_state, ...)` | `schedule_idle(self.owner.set_state, ...)` |
| `daemon/core/provider_manager.py` | 93, 163, 181, 202, 222, 226, 491, 530, 626, 643, 793 | `GLib.idle_add(...)` | `schedule_idle(...)` con gli stessi argomenti |
| `daemon/core/logger.py` | 583 | esempio nella docstring di `glib_safe` | `glib_safe` si elimina (6.1.f) |
| `gui/settings_window.py` | 123 | `GLib.idle_add(lambda: app.quit() if ... else None)` | `schedule_idle(lambda: app.quit() if len(app.get_windows()) <= 1 else None)` |
| `gui/assistant_window.py` | 649 | idem | idem |
| `gui/dependency_installer.py` | 296, 299, 304, 307, 312, 315, 319, 325 | `GLib.idle_add(...)` | `schedule_idle(...)` |
| `gui/assistant_window.py` | 412, 413, 481 | `GLib.idle_add(self._refresh_chats_list)` ecc. | `schedule_idle(...)` |
| `gui/assistant_window.py` | 449, 461, 469, 477, 513 | `GLib.idle_add(_glib_safe(fn, "nome"), args)` | `schedule_idle(fn, args)` |
| `gui/assistant_window.py` | 486 | `self._dep_debounce_id = GLib.timeout_add(500, self._trigger_poll_from_signal)` | `cancel(self._dep_debounce_id); self._dep_debounce_id = schedule_once(500, self._trigger_poll_from_signal)` |
| `gui/components/daemon_client.py` | 111, 140, 144 | `GLib.idle_add(handler, *unpacked)` ecc. | `schedule_idle(...)` |
| `gui/components/chat/chat_view.py` | 40 | `GLib.idle_add(_scroll)` | `schedule_idle(_scroll)` |
| `gui/components/settings/skills.py` | 222, 555, 587 | `GLib.idle_add(...)` | `schedule_idle(...)` |
| `gui/components/settings/speaker_id.py` | 371, 388 | `GLib.idle_add(self._update_status_ui)` | `schedule_idle(self._update_status_ui)` |
| `gui/components/settings/model_selector.py` | 132, 854, 940 | `GLib.idle_add(...)` | `schedule_idle(...)` |

Se una callback che diventa periodica deve davvero ripetersi, **non** usare `schedule_idle`: usa `PeriodicTimer` e fai restituire `True` esplicito.

**Passo 6.1.e — Watchdog del parlato** (`main.py`, `_start_speaking_watchdog`). Sostituisci con:
```python
def _start_speaking_watchdog(self):
    """Return to idle if the state says 'speaking' but no audio is playing."""
    ticks = 0

    def _check():
        nonlocal ticks
        ticks += 1
        if self._state != "speaking":
            return False
        if ticks > 3 and not getattr(self.audio_player, "is_playing", False):
            logger.info("[Watchdog] 'speaking' state without playback: back to idle")
            self.set_state("idle")
            return False
        return True

    if getattr(self, "_speaking_watchdog", None) is None:
        self._speaking_watchdog = PeriodicTimer(1000, _check)
    else:
        self._speaking_watchdog._fn = _check
    self._speaking_watchdog.start()   # start() stops the previous timer first
```
In `__init__` di `VoiceAssistant` aggiungi `self._speaking_watchdog = None`. Test: chiamando due volte `_start_speaking_watchdog()` resta **un solo** timer attivo.

**Passo 6.1.f — Eliminare `glib_safe`:** `core/logger.py` righe ~577–610 e il fallback in `gui/assistant_window.py` righe ~37–40.

**Passo 6.1.g — Svuotare `LEGACY`** nel test di §4.4.e: alla fine di questo passo `LEGACY = set()` e il test passa.

**Verifica 6.1:**
```bash
grep -rnE "GLib\.(idle_add|timeout_add|timeout_add_seconds)\(" src --include=*.py | grep -v venv
# Atteso: solo righe dentro src/daemon/core/glib_scheduling.py
grep -rn "glib_safe" src --include=*.py | grep -v venv   # nessun risultato
```

### 6.2 Eccezioni: niente `except Exception` silenziosi

**Situazione:** 553 `except Exception` e 3 `except:` nudi; 256 non registrano nulla (`pass`, `return`, `continue`), 252 registrano senza traceback.

**Regole (valgono da ora per ogni riga nuova o modificata):**
1. Cattura l'eccezione **specifica** che ti aspetti (`OSError`, `json.JSONDecodeError`, `KeyError`, `GLib.Error`, `subprocess.SubprocessError`…).
2. `except Exception` è ammesso **solo ai confini**: ciclo principale di un thread, metodo esposto su D-Bus, callback GLib (già coperte da 6.1), callback di librerie esterne. Lì va sempre `logger.exception(...)` (che include il traceback).
3. `pass` silenzioso è ammesso **solo** nel codice di chiusura (`close`, `stop`, `__del__`, pulizia di file temporanei), con un commento che spiega perché, per esempio `# Best effort: the temp file may already be gone.`
4. Mai `except:` nudo.

**Passo 6.2.a — Correggere subito i casi pericolosi** (questi nascondono guasti reali):
```bash
ruff check --select BLE001,S110,E722 --output-format concise src | grep -v venv > /tmp/va_except.txt
wc -l /tmp/va_except.txt
```
Da quell'elenco correggi in questa fase **tutti** i casi che rientrano in queste categorie (le altre si sistemano file per file in Fase 4, quando ogni modulo viene spostato):
- avvio di processi (`subprocess`, `Gio.Subprocess`);
- emissione di segnali D-Bus (per esempio `main.py:_on_llm_token`, righe ~713–720: oggi `except Exception: pass`) → `except Exception: logger.exception("Failed to emit %s", "ResponseTokenStreamed")`;
- lettura e scrittura di file (profili, conversazioni, configurazione, report);
- caricamento e scaricamento di modelli;
- i 3 `except:` nudi.

**Passo 6.2.b — Rendere la regola bloccante per il codice nuovo.** Il workflow di §4.4.d controlla già `BLE001` e `S110` sui file modificati. Aggiungi `E722` a `select` in `pyproject.toml`.

**Verifica 6.2:** `ruff check --select BLE001,S110,E722 src | grep -c ""` deve essere **diminuito** rispetto a `/tmp/va_except.txt`; riporta i due numeri nella richiesta di review.

### 6.3 Log: mai il testo dell'utente, niente log eccessivi

**Regola (decisione dell'utente):** nessun log, a nessun livello, contiene quello che l'utente ha detto o scritto, né il testo prodotto da LLM o TTS. Si registrano solo metadati (lunghezza, intent, contesto, durata). Il testo si può vedere **solo** attivando esplicitamente una modalità di debug temporanea.

**Passo 6.3.a — Helper in `src/daemon/core/logger.py`:**
```python
_LOG_USER_TEXT = os.environ.get("VOICE_ASSISTANT_LOG_USER_TEXT") == "1"


def redact(text: Optional[str]) -> str:
    """Describe user/LLM text for logs without revealing it (unless debug mode is on)."""
    if _LOG_USER_TEXT:
        return repr(text)
    return f"<{len(text or '')} chars>"
```
In `setup_logger`, alla fine: `if _LOG_USER_TEXT: logger.warning("VOICE_ASSISTANT_LOG_USER_TEXT=1: user text is written to the logs. Disable it after debugging.")`.

**Passo 6.3.b — Righe da correggere** (elenco completo al commit di partenza, esclusi i file eliminati in Fase 1). Il messaggio va anche tradotto in inglese:

| File | Riga ~ | Nuovo messaggio |
|---|---|---|
| `daemon/main.py` | 491 | `logger.info("[D-Bus] Text input from GUI: %s", redact(text))` |
| `daemon/main.py` | 506 | `logger.info("[D-Bus] Text input from GUI for context %s: %s", context_id, redact(text))` |
| `daemon/providers/openai_cloud_provider.py` | 119 | `logger.info("[CloudSTT] Transcript received: %s", redact(transcript))` |
| `daemon/services/rag_store.py` | 259 | `logger.info("[VectorStore] Added document %s (%d chars)", doc.doc_id, len(content))` |
| `daemon/services/memory_manager.py` | 74 | `logger.debug("[Memory] Added %s message: %s", role, redact(content))` |
| `daemon/services/tts_service.py` | 636 | `logger.info("[TTS] Playing %d bytes for %s", len(audio_bytes), redact(text))` |
| `daemon/core/pipeline.py` | 616 | `logger.info("[Pipeline] First deep-dive sentence sent to TTS: %s", redact(clean_sentence))` |
| `daemon/core/pipeline.py` | 626 | `logger.debug("[Pipeline] Sentence ready for TTS: %s", redact(clean_sentence))` |
| `daemon/core/pipeline.py` | 631 | `logger.exception("[Pipeline] TTS synthesis failed for sentence %s", redact(clean_sentence))` |
| `daemon/core/pipeline.py` | 667 | `logger.info("[Pipeline] Fast-Path match: %s (speak=%s, %s)", intent, speak, redact(response_text))` |
| `daemon/core/pipeline.py` | 693 | `logger.info("[Pipeline] No Fast-Path match, trying Smart-Path (speak=%s, %s)", speak, redact(text))` |
| `daemon/core/pipeline.py` | 709 | `logger.info("[Pipeline] Smart-Path success (speak=%s, %s)", speak, redact(smart_response))` |
| `daemon/core/pipeline.py` | 727 | `logger.info("[Pipeline] Sending to streaming LLM (speak=%s, %s)", speak, redact(text))` |
| `daemon/core/assistant_runtime.py` | 124 | `logger.info("[Queue] Request queued (%s, voice=%s, ctx=%s, prio=%s, interrupting=%s)", redact(text), is_voice, context_id, priority, was_interrupting)` |
| `daemon/core/assistant_runtime.py` | 1077 | `logger.info("Transcript contains only the wake word (%s): back to idle", redact(text))` |
| `daemon/core/assistant_runtime.py` | 1177 | `logger.info("[Recognized] %s (voice=%s, ctx=%s, interrupting=%s)", redact(text), is_voice, context_id, was_interrupting)` |
| `daemon/core/assistant_runtime.py` | 1225 | nel `logger.warning` del rifiuto Speaker ID togli `rejection_msg` (è un testo fisso, non serve nel log) |
| `daemon/core/assistant_runtime.py` | 1304 | `logger.info("[TTS Fallback] Synthesizing %s", redact(text))` |

Nota: `pipeline.py:626` passa da INFO a DEBUG perché viene scritta **una volta per frase** (EGO: "MUST NOT print excessively to the log").

**Passo 6.3.c — Test che impedisce di reintrodurle** `tests/test_no_user_text_in_logs.py`:
```python
"""Forbid logging user or model text directly (use core.logger.redact)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "src"
TEXT_NAMES = r"(text|user_text|clean_text|clean_sentence|sentence|response|response_text|smart_response|transcript|prompt|query|content|reply|answer|rejection_msg)"
# f-string interpolation, or %-style argument, of a text variable inside a logger call
F_STRING = re.compile(r"logger\.\w+\(\s*f[\"'].*\{" + TEXT_NAMES + r"(\[[^\]]*\])?(![rs])?\}")
PERCENT_ARG = re.compile(r"logger\.\w+\(.*,\s*" + TEXT_NAMES + r"\s*[,)]")


def test_no_user_text_in_logs():
    offenders = []
    for path in ROOT.rglob("*.py"):
        if "venv" in path.parts:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if F_STRING.search(line) or PERCENT_ARG.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}: {line.strip()}")
    assert not offenders, "Wrap user/LLM text with redact():\n" + "\n".join(offenders)
```
Se il test segnala righe che non contengono testo dell'utente (per esempio una variabile chiamata `response` che contiene un codice HTTP), **rinomina la variabile** invece di indebolire il test.

**Verifica 6.3:** il test passa; avvia il daemon, fai una richiesta vocale e una scritta, poi `grep -i "<la frase che hai detto>" ~/.local/share/voice-assistant/logs/voice-assistant.log` → nessun risultato; `journalctl --user -u voice-assistant -n 200 | grep -i "<la frase>"` → nessun risultato.

### 6.4 Report di errore e segnalazioni su GitHub

**Decisioni dell'utente:** issue tracker = **GitHub Issues di `Scroker/voice-assistant`**. Livello 1: l'utente apre la segnalazione **dal browser** con un link già compilato; nessun login nell'app, nessun token. Ricerca anonima di issue simili all'apertura della finestra: sì. Nessun invio automatico (EGO: niente telemetria).

**Flusso da ottenere:**
```
errore → ErrorCollector calcola la firma → firma nuova? ─no→ incrementa il contatore nel report esistente (nessuna notifica)
                                                        └sì→ salva il report → UNA notifica desktop "Voice Assistant hit an error" con azione "Report…"
azione "Report…" → GUI --report-problem <firma> → finestra con anteprima modificabile
   → all'apertura: ricerca anonima di issue con quella firma su GitHub
   → "Open on GitHub": apre nel browser issues/new già compilata (l'utente invia)
   → "Show diagnostic files": crea il bundle e apre la cartella nel file manager (l'utente lo trascina nella issue)
```

**Passo 6.4.a — Firma e deduplicazione** in `src/daemon/core/logger.py`:
```python
MAX_ERROR_REPORTS = 50


def error_signature(exc_type, exc_traceback, component: str) -> str:
    """Stable id for 'the same bug': component, exception type and the last 3 frames (no line numbers)."""
    frames = traceback.extract_tb(exc_traceback)[-3:] if exc_traceback else []
    parts = [component, exc_type.__name__ if exc_type else "UnknownError"]
    parts += [f"{os.path.basename(f.filename)}:{f.name}" for f in frames]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]
```
Modifica `ErrorCollector.record_error` (riga ~93):
1. calcola `signature = error_signature(exc_type, exc_traceback, component)`;
2. il file diventa `report_<signature>.json` (non più uno per timestamp);
3. se il file **esiste**: leggilo, fai `count += 1`, `last_seen = now`, riscrivilo (in modo atomico: scrivi `*.tmp` e poi `os.replace`), **non** chiamare la callback;
4. se **non esiste**: scrivi il report con i campi di oggi più `"signature"`, `"count": 1`, `"first_seen"`, `"last_seen"`; poi applica la **retention** (se i file `report_*.json` sono più di `MAX_ERROR_REPORTS`, elimina i più vecchi per `last_seen`); poi chiama `_error_submitted_callback(signature, component, error_type_name)` se impostata;
5. `message` passa da `_sanitize_text`, come già `traceback`;
6. il log diventa `logger.error("Error report %s saved (%s)", signature, filepath)` — **sempre con traceback** nel report, mai nel testo della notifica.

Rinomina `set_error_submitted_callback` in `set_new_error_callback` (firma della callback: `(signature: str, component: str, error_type: str) -> None`) e aggiorna `install_global_exception_hooks` (righe ~524 e ~541): gli hook chiamano solo `ErrorCollector.record_error(...)`, che a sua volta chiama la callback.

**Test** `tests/test_error_collector.py` (con `ERROR_REPORTS_DIR` puntato a una cartella temporanea tramite `monkeypatch`):
- stessa eccezione sollevata 3 volte dalla stessa funzione → 1 file, `count == 3`, callback chiamata **1** volta;
- due eccezioni diverse → 2 file, callback chiamata 2 volte;
- 55 firme diverse → restano 50 file;
- una riga diversa nella stessa funzione (stesso frame, riga diversa) → **stessa** firma.

**Passo 6.4.b — Notifica con azione** (daemon). In `src/daemon/main.py`, blocco `__main__`, registra la callback **dopo** aver creato `assistant`:
```python
set_new_error_callback(lambda sig, comp, etype: schedule_idle(assistant.runtime_manager.notify_new_error, sig, comp, etype))
```
In `DaemonRuntimeManager` aggiungi `notify_new_error(signature, component, error_type)`. Riusa il meccanismo già esistente delle notifiche con azioni via GDBus (`_on_gdbus_action_invoked`, riga ~427, e `_handle_deps_notification_action`, riga ~449): la notifica ha titolo `_("Voice Assistant hit an error")`, corpo `_("You can report it to the developers. Nothing is sent without your confirmation.")`, azione con chiave `report:<signature>` ed etichetta `_("Report…")`. Quando l'azione viene invocata: `schedule_idle(self.owner._launch_gui, "--report-problem", signature)`. Adatta `_on_gdbus_action_invoked` perché gestisca sia le azioni delle dipendenze sia quelle `report:`.

**Passo 6.4.c — Finestra di segnalazione** (GUI), nuovo file `src/gui/components/report_dialog.py`, classe `ReportProblemDialog(Adw.Dialog)`:
- **Argomenti:** `parent`, `daemon_client`, `signature: str | None` (`None` = segnalazione manuale senza errore).
- **Contenuto iniziale** (tutto in inglese, è il testo della issue):
  - titolo: se c'è la firma, `"[<component>] <error_type>"`, altrimenti vuoto;
  - corpo: sezioni `### What happened`, `### Steps to reproduce` (vuote, da compilare), `### Error signature` (`<signature>` o `none`), `### Environment` (OS, versione GNOME, versione estensione, motori selezionati: prese da `GetErrorReports` e dalle impostazioni; **nessun** percorso della home, già sanificato), `### Traceback` (dal report, in un blocco di codice).
  - Titolo e corpo sono in un `Adw.EntryRow` e in una `Gtk.TextView` **modificabili**: l'utente vede esattamente cosa verrà pubblicato.
- **Ricerca di duplicati** (solo se c'è la firma), in un thread, all'apertura della finestra:
  `GET https://api.github.com/search/issues?q=repo:Scroker/voice-assistant+is:issue+<signature>` con header `Accept: application/vnd.github+json` e `User-Agent: voice-assistant`, timeout 5 s. Mostra fino a 5 risultati come righe cliccabili (`title`, `state`, apertura di `html_url` con `Gtk.UriLauncher`). Se la richiesta fallisce (rete assente, limite di 10 richieste al minuto superato) mostra `_("Could not search for similar reports.")` e **non** bloccare nulla. Aggiorna la GUI solo con `schedule_idle`.
- **Pulsante "Open on GitHub":** costruisce
  `https://github.com/Scroker/voice-assistant/issues/new?template=bug_report.yml&title=<titolo>&description=<corpo>`
  con `urllib.parse.urlencode`. Se l'URL supera **8000 caratteri**, accorcia il blocco del traceback tenendo le **ultime** righe e aggiungendo `(truncated — see diagnostic files)`. Apri con `Gtk.UriLauncher.new(url).launch(parent, None, None, None)`.
- **Pulsante "Show diagnostic files":** chiama il metodo D-Bus esistente `GenerateDiagnosticBundle`, poi apre la **cartella** che contiene il file con `Gtk.FileLauncher.new(Gio.File.new_for_path(path)).open_containing_folder(parent, None, None, None)`. Testo di aiuto sotto il pulsante: `_("Drag the file into the GitHub page. Check its contents first.")`.
- Verifica le API GTK sulla versione installata (R6): `python3 -c "from gi.repository import Gtk; print(hasattr(Gtk,'UriLauncher'), hasattr(Gtk.FileLauncher,'open_containing_folder'))"`.

**Passo 6.4.d — Avvio della finestra.** `src/gui/main.py`, gestione della riga di comando: `--report-problem <firma>` apre `ReportProblemDialog` con quella firma; `--report-problem` senza firma la apre in modalità manuale. Nelle impostazioni (`data/ui/prefs/page_general.blp`, dove in Fase 1 c'era la riga Bugzilla) aggiungi `Adw.ActionRow report_problem_row { title: _("Report a Problem"); subtitle: _("Open a prefilled report on GitHub"); activatable: true; }` e in `general.py` collegala all'apertura della finestra in modalità manuale. Rigenera `prefs.blp`/`prefs.ui` come in §5.5.d.

**Passo 6.4.e — Modulo issue di GitHub.** Elimina `.github/ISSUE_TEMPLATE/bug_report.md`. Crea `.github/ISSUE_TEMPLATE/bug_report.yml`:
```yaml
name: Bug report
description: Something does not work as expected
labels: ["bug"]
body:
  - type: textarea
    id: description
    attributes:
      label: Description
      description: What happened, what you expected, and the steps to reproduce it.
    validations:
      required: true
  - type: input
    id: signature
    attributes:
      label: Error signature
      description: Shown in the app's report window. Leave empty if there is none.
  - type: textarea
    id: environment
    attributes:
      label: Environment
      description: Distribution, GNOME version, extension version, selected engines.
  - type: textarea
    id: logs
    attributes:
      label: Diagnostic files
      description: Drag here the file opened by "Show diagnostic files". Check its contents first.
```
Il parametro `description=` dell'URL di 6.4.c compila il campo con `id: description` (vedi V6 in §11: va verificato a mano una volta).

**Test:** `tests/test_report_dialog.py` con la rete sostituita da un finto (`unittest.mock.patch("urllib.request.urlopen")`): (1) URL costruito con `template=bug_report.yml` e titolo codificato; (2) corpo più lungo di 8000 caratteri → URL ≤ 8000 e contiene `(truncated`; (3) ricerca che solleva `URLError` → nessuna eccezione, messaggio mostrato. Nessuna richiesta di rete reale (R5).

### 6.5 Crash del thread audio: riavvii con attesa crescente

**Oggi** (`assistant_runtime.py`, righe ~1164–1171): a ogni eccezione del thread audio salva un report e dopo 2 s riavvia il thread, **per sempre**. Un errore permanente (microfono assente) produce un report ogni 2 s.

**Passo 6.5.a — Nuovo file `src/daemon/core/restart_policy.py`:**
```python
"""Exponential backoff with a maximum number of attempts."""
from __future__ import annotations

import time
from typing import Callable, Optional, Sequence


class RestartPolicy:
    def __init__(
        self,
        delays_s: Sequence[float] = (1.0, 2.0, 4.0, 8.0, 16.0),
        stable_after_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._delays = tuple(delays_s)
        self._stable_after_s = stable_after_s
        self._clock = clock
        self._attempt = 0
        self._started_at: Optional[float] = None

    def record_start(self) -> None:
        """Call every time the supervised component (re)starts."""
        self._started_at = self._clock()

    def next_delay(self) -> Optional[float]:
        """Delay before the next restart, or None when attempts are exhausted."""
        if self._started_at is not None and self._clock() - self._started_at >= self._stable_after_s:
            self._attempt = 0   # it ran long enough: this is a new, unrelated failure
        if self._attempt >= len(self._delays):
            return None
        delay = self._delays[self._attempt]
        self._attempt += 1
        return delay

    def reset(self) -> None:
        self._attempt = 0
```
Test `tests/test_restart_policy.py` con un orologio finto: 5 ritardi `1,2,4,8,16` poi `None`; dopo `record_start()` e 60 s simulati si riparte da 1.

**Passo 6.5.b — Nuovo stato `error`.** In `core/state.py` aggiungi `ERROR = "error"` ad `AssistantState`. In `src/extension.js`, `updateUiState` (riga ~499), aggiungi il caso:
```js
case 'error':
    this.checked = false;
    this.subtitle = _('Error — open settings');
    break;
```
Aggiungi la stringa a `po/` (§6.7). In `data/dbus/org.local.VoiceAssistant.xml` e in `docs/dbus.md` documenta il nuovo valore di `StateChanged`/`GetState`.

**Passo 6.5.c — Uso nel thread audio.** Il codice usa `_()` e `schedule_idle`: se `src/daemon/core/i18n.py` (§6.7.a) non esiste ancora, crealo **ora** esattamente come in §6.7.a e importa `from core.i18n import _`. In `AssistantRuntimeController.__init__` crea `self._audio_restart = RestartPolicy()`. All'inizio di `_audio_loop` chiama `self._audio_restart.record_start()`. Sostituisci il blocco `except` finale (righe ~1164–1171) con:
```python
except Exception as e:
    logger.critical("Audio thread crashed", exc_info=True)
    self.owner._report_error(e)
    if self.owner._state == "disabled":
        return
    delay = self._audio_restart.next_delay()
    if delay is None:
        logger.error("Audio thread failed repeatedly: giving up until the user re-enables the assistant")
        schedule_idle(self.owner.set_state, "error")
        schedule_idle(
            self.owner.runtime_manager.notify_user,
            _("Voice Assistant stopped listening"),
            _("The microphone keeps failing. Check the audio device, then turn the assistant off and on again."),
        )
        return
    logger.info("Restarting audio thread in %.0f s", delay)
    time.sleep(delay)
    self.owner._audio_thread = threading.Thread(target=self.owner._audio_loop, daemon=True, name="AudioLoop")
    self.owner._audio_thread.start()
```
Quando l'utente riattiva l'assistente (chiave `enabled` da `false` a `true`, gestita in `assistant_runtime.py` righe ~590–596) chiama `self._audio_restart.reset()` e, se lo stato era `error`, riavvia il thread audio.

**Passo 6.5.d — `faulthandler`.** In `src/daemon/main.py` e in `src/gui/main.py`, prima di tutto il resto:
```python
import faulthandler
_crash_log = open(os.path.join(LOG_DIR, "crash.log"), "a", encoding="utf-8")  # kept open for the process lifetime
faulthandler.enable(file=_crash_log)
```
(`LOG_DIR` da `core.logger`.) `crash.log` va incluso nel bundle diagnostico (`DiagnosticBundler.generate`, dopo il punto 2), passando da `_sanitize_text`.

**Verifica 6.5:** test verdi; prova manuale: con `pactl suspend-source @DEFAULT_SOURCE@ 1` o scollegando il microfono USB il daemon fa al massimo 5 tentativi, poi lo stato diventa `error`, compare **una** notifica e in `error_reports/` c'è **un** file con `count` ≥ 5.

### 6.6 Integrità dei modelli scaricati (checksum)

**Perché:** un file corrotto oggi viene caricato senza controlli (esempio reale: la voce `it_IT-riccardo-x_low.onnx` sulla macchina di sviluppo dà `InvalidProtobuf`).

**Passo 6.6.a — Nuovo file `src/daemon/core/integrity.py`:**
```python
"""Integrity checks for downloaded model files."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Dict, Optional

MANIFEST_NAME = ".va-manifest.json"


def sha256_file(path: os.PathLike, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def matches_sha256(path: os.PathLike, expected: str) -> bool:
    """expected may be 'sha256:<hex>' or '<hex>'."""
    return sha256_file(path) == expected.lower().removeprefix("sha256:")


def parse_checksum_txt(text: str) -> Dict[str, str]:
    """Parse sherpa-onnx release checksum.txt lines: '<asset name>\\t<sha256>'."""
    result: Dict[str, str] = {}
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) == 2:
            result[parts[0]] = parts[1].lower()
    return result


def write_manifest(model_dir: Path) -> None:
    """Record size and sha256 of every file after a verified download."""
    entries = {}
    for path in sorted(p for p in model_dir.rglob("*") if p.is_file() and p.name != MANIFEST_NAME):
        entries[path.relative_to(model_dir).as_posix()] = {"size": path.stat().st_size, "sha256": sha256_file(path)}
    tmp = model_dir / (MANIFEST_NAME + ".tmp")
    tmp.write_text(json.dumps(entries, indent=1), encoding="utf-8")
    os.replace(tmp, model_dir / MANIFEST_NAME)


def verify_manifest(model_dir: Path, full: bool = False) -> Optional[bool]:
    """None = no manifest (downloaded before this feature); False = damaged; True = intact.

    full=False compares sizes only (cheap, run on every load);
    full=True also compares hashes (run after a load failure).
    """
    manifest_path = model_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        return None
    entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    for rel, info in entries.items():
        path = model_dir / rel
        if not path.is_file() or path.stat().st_size != info["size"]:
            return False
        if full and sha256_file(path) != info["sha256"]:
            return False
    return True
```
Formato di `checksum.txt` verificato sulle release `asr-models`, `tts-models`, `kws-models` e `speaker-recongition-models` di sherpa-onnx: una riga per file, `nome<TAB>sha256`.

**Passo 6.6.b — Da dove arriva l'hash atteso:**
| Sorgente | Hash atteso |
|---|---|
| Release GitHub di sherpa-onnx (`kws-models`, e in Fase 5 `asr-models`, `tts-models`, `speaker-recongition-models`) | `https://github.com/k2-fsa/sherpa-onnx/releases/download/<tag>/checksum.txt`, riga dell'archivio scaricato |
| Hugging Face (voci Piper, GGUF, router semantico, modelli Whisper) | hash LFS del file: vedi V7 in §11 per l'API esatta. Finché V7 non è verificato: nessun hash atteso, solo manifest |
| Vosk (zip da alphacephei.com) | nessun hash pubblicato: `zipfile.ZipFile(path).testzip() is None` prima di estrarre, poi manifest |
| Ollama | nessun controllo (Ollama verifica da solo) |

**Passo 6.6.c — Regole comuni per ogni download:**
1. scarica in `<destinazione>.part`, **mai** direttamente nel file finale;
2. se c'è un hash atteso e non corrisponde: elimina il `.part`, registra `logger.error("Checksum mismatch for %s", name)`, notifica `_("The download of %s is damaged. Please try again.")`, restituisci errore;
3. estrai o rinomina (`os.replace`) solo dopo il controllo;
4. alla fine chiama `write_manifest(cartella_del_modello)`;
5. **al caricamento** del modello: `verify_manifest(dir)`; se `False` → non caricarlo, notifica `_("The model %s is damaged. Download it again from Settings → Models.")`; se il caricamento fallisce con un'eccezione e il manifest esiste → `verify_manifest(dir, full=True)` e, se `False`, stessa notifica.

**Passo 6.6.d — Punti di download da modificare** (al commit di partenza):
| File | Riga ~ | Cosa scarica |
|---|---|---|
| `daemon/core/provider_manager.py` | 725 (`download_sherpa_model`) | modelli KWS sherpa (tar.bz2) — hash da `checksum.txt` |
| `daemon/core/runtime_manager.py` | 987 (`_download_sherpa_kws_model`) | stesso download duplicato: fallo chiamare `provider_manager.download_sherpa_model` ed elimina il duplicato |
| `daemon/providers/vosk_provider.py` | 114 | zip Vosk — `testzip` |
| `daemon/providers/whisper_provider.py` | 118 | `silero_vad.onnx` con `urlretrieve` — hash da `asr-models/checksum.txt` (voce `silero_vad.onnx`, verificata), scarica da `https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx` |
| `daemon/services/tts_service.py` | 171–180 e 240–243 | voci Piper (HTTP diretto e `hf_hub_download`) — manifest |
| `daemon/services/llm_service.py` | 210 (`download_llm_model`), 319 | GGUF — manifest |
| `daemon/skills/semantic_router.py` | 98–103 | router (`hf_hub_download`) — manifest |
| modelli di faster-whisper | scaricati internamente dalla libreria | manifest dopo il primo caricamento riuscito |

**Test** `tests/test_integrity.py`: file noto → hash noto; `parse_checksum_txt` con due righe separate da TAB; `write_manifest` + `verify_manifest` → `True`; file troncato → `False` (anche con `full=False`); un byte cambiato a parità di dimensione → `True` con `full=False` e `False` con `full=True`; cartella senza manifest → `None`.

### 6.7 Lingua: inglese nel codice, italiano solo tramite gettext

**Passo 6.7.a — Python.** Nuovo file `src/daemon/core/i18n.py`:
```python
"""gettext setup shared by daemon and GUI (domain 'voice-assistant', same as metadata.json)."""
from __future__ import annotations

import gettext
from pathlib import Path

DOMAIN = "voice-assistant"
_translation = gettext.NullTranslations()


def setup_translations(extension_dir: Path) -> None:
    global _translation
    _translation = gettext.translation(DOMAIN, localedir=str(extension_dir / "locale"), fallback=True)


def _(message: str) -> str:
    return _translation.gettext(message)


def ngettext(singular: str, plural: str, n: int) -> str:
    return _translation.ngettext(singular, plural, n)
```
Chiama `setup_translations(...)` all'avvio di `daemon/main.py` e di `gui/main.py` con la cartella dell'estensione (in Fase 3 arriva `extension_dir()`, §7.1; fino ad allora usa `Path(__file__).resolve().parents[1]` da `main.py`). In `gui/assistant_window.py` sostituisci `import gettext` / `_ = gettext.gettext` (righe 1–2) con `from i18n import _`.

**Passo 6.7.b — Cosa tradurre e come:**
| Tipo di testo | Regola |
|---|---|
| Log, eccezioni interne, docstring, commenti | inglese, **senza** `_()` |
| Notifiche, dialoghi, messaggi restituiti alla GUI via D-Bus, testi dell'estensione | inglese dentro `_()`, traduzione italiana in `po/it.po` |
| Frasi **pronunciate** dall'assistente (`data/locales/responses.json`, `speaker_enrollment.json`) | restano nei JSON per lingua: dipendono dalla lingua parlata, non dalla lingua del desktop |
| Descrizioni dello schema GSettings (`<summary>`, `<description>`) | inglese |
| Stringhe nei `.blp` oggi in italiano (es. `subpage_*`) | inglese dentro `_()` |

In `src/extension.js` le stringhe oggi italiane (`'In ascolto...'`, `'Elaborazione...'`, `'Riproduzione...'`, `'Download...'`, `'Disabilitato'`, `'Non disponibile'`, `'In attesa'`) diventano inglesi (`'Listening…'`, `'Processing…'`, `'Speaking…'`, `'Downloading…'`, `'Disabled'`, `'Unavailable'`, `'Idle'`). La riga ~794 ``_(`Download ${pName} (${mName}): ${percent}%`)`` non è traducibile: sostituiscila con ``this._quickIndicator.quickToggle.subtitle = `${_('Downloading')} ${pName} (${mName}): ${percent}%`;`` (solo la parola fissa passa da gettext).

**Passo 6.7.c — Test che blocca l'italiano nuovo nei log**, `tests/test_log_language.py`, stesso schema di §4.4.e (elenco `LEGACY` riempito oggi e svuotato file per file):
```python
ITALIAN = re.compile(r"logger\.\w+\(.*[\"'].*\b(errore|impossibile|caricamento|avvio|richiesta|ricevuto|modello|scaricamento|completat[oa]|fallit[oa]|non riuscito|in corso)\b", re.IGNORECASE)
```
Converti in inglese **in questa fase** i log dei file già toccati in §6.1–§6.6; gli altri file si convertono quando vengono spostati in Fase 4 (§8.2), e a quel punto si tolgono da `LEGACY`.

**Passo 6.7.d — Catalogo delle traduzioni.**
```bash
grep -rlE "\b_\(\s*[\"']" src --include=*.py --include=*.js | grep -v venv | sort
```
Aggiungi a `po/POTFILES.in` tutti i file trovati (oggi contiene solo `data/ui/prefs.blp`, `src/extension.js`, `src/prefs.js`), poi:
```bash
meson compile -C build voice-assistant-pot
meson compile -C build voice-assistant-update-po
```
e completa `po/it.po` (nessuna voce `msgstr ""` vuota: `msgfmt --check --statistics po/it.po`).

### 6.8 Criteri di accettazione della Fase 2
- [ ] Verifiche 6.1, 6.2, 6.3, 6.5 con i risultati attesi.
- [ ] Nuovi test verdi: `test_glib_scheduling`, `test_no_user_text_in_logs`, `test_error_collector`, `test_report_dialog`, `test_restart_policy`, `test_integrity`, `test_log_language`.
- [ ] Prova manuale 6.4: sollevare un'eccezione di prova (per esempio un metodo D-Bus di debug **temporaneo**, da togliere prima del commit) due volte → una sola notifica; "Report…" apre la finestra; "Open on GitHub" apre il browser con titolo e descrizione compilati; **nessuna** issue viene creata senza clic dell'utente sul sito.
- [ ] `msgfmt --check po/it.po` senza errori; con il desktop in italiano la GUI e il menu dell'estensione sono in italiano.

---

## 7. Fase 3 — Packaging ed EGO

**Obiettivo:** l'estensione si installa **solo** dallo zip (`gnome-extensions install`), come su extensions.gnome.org, e funziona su un account utente pulito. Ordine obbligatorio: 7.1 → 7.2 → 7.3 → 7.4 → 7.5 → 7.6.

**Problemi verificati oggi:**
1. Il daemon termina con abort su un'installazione da zip: `Gio.Settings.new` cerca lo schema nei percorsi di sistema, ma lo schema sta solo in `<estensione>/schemas`.
2. Il venv sta dentro la cartella dell'estensione (`daemon/venv`): ogni aggiornamento dell'estensione lo cancella.
3. Percorsi con l'UUID scritti a mano, con **due** UUID diversi (`voice-assistant@scroker.github.io` e `voice-assistant@mkswap.github.io`) in 11 punti.
4. File dati letti al momento dell'import, prima che la GResource sia registrata.
5. `extension.js` scrive servizi systemd e D-Bus, file `.desktop` e icone, lancia `systemctl --user enable --now` (365 righe nel processo di GNOME Shell) e usa `Gdk`/`Gtk` in `enable()` (righe ~628–642) **senza nemmeno importarli**: oggi quel blocco solleva sempre `ReferenceError`, catturato dal `try`.

### 7.1 Una sola installazione: lo zip. Nessun UUID scritto a mano

**Passo 7.1.a — Cartella dell'estensione calcolata dal file.** In `src/daemon/core/path_utils.py` aggiungi:
```python
def extension_dir() -> Path:
    """Root of the installed extension (or src/ in a development checkout).

    This file lives at <root>/daemon/core/path_utils.py.
    """
    return Path(__file__).resolve().parents[2]
```
Test `tests/test_path_utils.py`: `extension_dir() / "daemon" / "core" / "path_utils.py"` esiste.

**Passo 7.1.b — Sostituire tutti i percorsi con l'UUID** (elenco completo; verifica finale col `grep` sotto):
| File | Riga ~ | Sostituzione |
|---|---|---|
| `daemon/main.py` | 456–457, 478–479 | tieni solo i candidati relativi a `ext_dir` (righe ~455 e ~477); elimina quelli con `~/.local/share/gnome-shell/extensions/...` |
| `daemon/core/data_loader.py` | 21–28 | `_CANDIDATE_SEARCH_PATHS = [_DATA_DIR, extension_dir() / "data", extension_dir()]` (togli anche `/usr/share/voice-assistant`: l'estensione non si installa lì) |
| `daemon/core/runtime_manager.py` | 255 | `res_file = str(extension_dir() / "org.gnome.shell.extensions.voice-assistant.gresource")`; se non esiste, prova `extension_dir().parent / "build" / "data" / <stesso nome>` (sviluppo) |
| `gui/components/resources.py` | 31–40, 82–83 | tieni solo i candidati basati su `_EXT_ROOT` (già presenti alle righe ~20–29 e ~81) |
| `gui/settings_window.py` | 202 | cartella degli schemi: vedi §7.2 (la funzione comune sostituisce tutto il blocco) |
| `gui/assistant_window.py` | 113 | idem §7.2 |
| `gui/components/settings/speaker_id.py` | 60 | leggi `locales/speaker_enrollment.json` con `load_json_data` di `core/data_loader.py` |
| `tests/*.py` | — | già fatto in §4.2.b |

**Verifica 7.1.b:**
```bash
grep -rn "scroker.github.io\|mkswap.github.io\|gnome-shell/extensions/" src --include=*.py --include=*.js --include=*.sh | grep -v venv
# Atteso: nessun risultato
```
L'UUID resta **solo** in `meson.build` (riga 6) e `data/metadata.json.in` (riga 2), e deve essere lo stesso in entrambi (`voice-assistant@scroker.github.io`).

**Passo 7.1.c — Meson solo per compilare e creare lo zip.**
1. `src/meson.build`: elimina le righe `install_subdir(...)` (righe ~7–8) e ogni altra regola `install_*`. Fai lo stesso in `data/meson.build` e `po/meson.build` (togli `install_dir`/`install: true`; la compilazione dei `.mo` la fa `gnome-extensions pack`, passo 3).
2. `meson.build`: elimina `gnome.post_install(...)` (righe ~19–23) e `meson.add_install_script(...)` (riga ~25). `subdir('tests')` è già stato tolto in §4.3.a.
3. Target `zip` (righe ~27–60): nel comando `gnome-extensions pack`
   - togli `--extra-source=services`, `--extra-source=dbus`, `--extra-source=mcp`, `--extra-source=dependencies` e le rispettive righe `cp -r` (quei file sono già dentro la GResource: controlla in `data/schemas/org.gnome.shell.extensions.voice-assistant.gresource.xml`);
   - tieni `daemon`, `gui`, `icons`, la `.gresource`, `schemas/`, `stylesheet.css`, `metadata.json`;
   - aggiungi `--podir=<src_root>/po` (compila le traduzioni in `locale/`);
   - dopo `cp -r src/*` elimina anche le cartelle `tests` e `experiments` se presenti, e i file `*.pyc`.
4. Nuovo script `scripts/install-dev.sh`:
   ```bash
   #!/bin/sh
   # Build the extension zip and install it exactly like extensions.gnome.org does.
   set -eu
   ROOT="$(cd "$(dirname "$0")/.." && pwd)"
   [ -d "$ROOT/build" ] || meson setup "$ROOT/build" "$ROOT"
   meson compile -C "$ROOT/build" zip
   gnome-extensions install --force "$ROOT/build/voice-assistant@scroker.github.io.shell-extension.zip"
   echo "Installed. Log out and back in (Wayland) to reload the extension."
   ```
5. `docs/development.md`: sostituisci le istruzioni `meson install` (righe ~38–54, ~300–310) con `scripts/install-dev.sh`; togli `--prefix=$HOME/.local`.

**Verifica 7.1.c:**
```bash
scripts/install-dev.sh
unzip -l build/voice-assistant@scroker.github.io.shell-extension.zip | grep -E "venv|__pycache__|\.pyc$|\.so|tests/|experiments/"
# Atteso: nessun risultato
unzip -l build/voice-assistant@scroker.github.io.shell-extension.zip | grep -E "locale/it/LC_MESSAGES/voice-assistant.mo|schemas/gschemas.compiled|metadata.json"
# Atteso: 3 righe
```

### 7.2 Schema GSettings trovato anche da zip

**Passo 7.2.a — Nuovo file `src/daemon/core/settings_source.py`** (usato da daemon e GUI; importa solo `gi` e `path_utils`):
```python
"""Open the extension's GSettings schema, whether installed from zip or run from a checkout."""
from __future__ import annotations

from gi.repository import Gio

SCHEMA_ID = "org.gnome.shell.extensions.voice-assistant"


class SchemaNotFoundError(RuntimeError):
    pass


def open_settings() -> Gio.Settings:
    """Never calls Gio.Settings.new(): that aborts the process when the schema is missing."""
    from path_utils import extension_dir  # 'core' directory is on sys.path in daemon and GUI

    default_source = Gio.SettingsSchemaSource.get_default()
    schema = None
    schemas_dir = extension_dir() / "schemas"
    if (schemas_dir / "gschemas.compiled").is_file():
        source = Gio.SettingsSchemaSource.new_from_directory(str(schemas_dir), default_source, False)
        schema = source.lookup(SCHEMA_ID, False)
    if schema is None and default_source is not None:
        schema = default_source.lookup(SCHEMA_ID, True)   # development and tests (GSETTINGS_SCHEMA_DIR)
    if schema is None:
        raise SchemaNotFoundError(f"GSettings schema {SCHEMA_ID} not found (looked in {schemas_dir})")
    return Gio.Settings.new_full(schema, None, None)
```
**Attenzione all'import:** il daemon importa i moduli come `core.xxx`, la GUI come `xxx`. Prima di scrivere, verifica come `path_utils` è importabile in entrambi (`grep -rn "import path_utils\|from core.path_utils\|from path_utils" src`). Se nel daemon `path_utils` non è importabile senza prefisso, usa `from core.path_utils import extension_dir` con un `try/except ImportError` che ripiega su `from path_utils import extension_dir`. `Gio.SettingsSchemaSource.new_from_directory` esiste sul PyGObject installato (verificato).

**Passo 7.2.b — Sostituire tutte le aperture dello schema:**
| File | Riga ~ | Oggi | Diventa |
|---|---|---|---|
| `daemon/core/settings.py` | 88 | `Gio.Settings.new(self.SCHEMA_ID)` | `open_settings()` |
| `daemon/core/cloud_config.py` | 209 | `Gio.Settings.new("org.gnome...")` | `open_settings()` |
| `daemon/core/runtime_manager.py` | 265 | `Gio.Settings.new("org.gnome...")` | `open_settings()` |
| `gui/assistant_window.py` | 105–122 | tentativi con `Gio.Settings.new` e `new_full` | `self._settings = open_settings()` dentro `try/except SchemaNotFoundError` (in caso di errore: `logger.error` e finestra senza impostazioni, come oggi) |
| `gui/settings_window.py` | 195–212 | idem | idem |

Nel daemon, se `open_settings()` solleva `SchemaNotFoundError` in `load_settings`, registra `logger.critical(...)` ed esci con `sys.exit(78)` (EX_CONFIG): mai abort.

**Test** `tests/test_settings_source.py`: in un **sottoprocesso** (così un eventuale abort non uccide pytest) crea una cartella finta `<tmp>/daemon/core/`, copia `path_utils.py` e `settings_source.py`, compila lo schema in `<tmp>/schemas`, togli `GSETTINGS_SCHEMA_DIR` dall'ambiente e chiama `open_settings().get_boolean("enabled")` → exit code 0. Secondo caso: cartella senza schema → exit code 0 e `SchemaNotFoundError` stampata.

**Verifica 7.2:** `grep -rn "Gio.Settings.new\b\|Gio.Settings.new(" src --include=*.py | grep -v venv` → nessun risultato.

### 7.3 Il venv fuori dalla cartella dell'estensione

**Decisione:** venv in `${XDG_DATA_HOME:-~/.local/share}/voice-assistant/venv`, creato dalla GUI al primo avvio con il consenso dell'utente (§7.5). Il daemon **non** installa più nulla da solo.

**Passo 7.3.a — Funzione unica per il percorso.** In `path_utils.py`:
```python
def venv_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return Path(base) / "voice-assistant" / "venv"


def venv_python() -> Path:
    return venv_dir() / "bin" / "python3"
```

**Passo 7.3.b — `src/daemon/start.sh`** riscritto (in inglese):
```bash
#!/bin/sh
# Start the Voice Assistant daemon. The virtualenv is created by the GUI first-run setup.
set -eu
DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="${XDG_DATA_HOME:-$HOME/.local/share}/voice-assistant/venv"

if [ ! -x "$VENV/bin/python3" ]; then
    echo "Voice Assistant is not set up yet: open the extension menu and choose 'Set Up Voice Assistant'." >&2
    exit 78
fi

# The process must be called 'VoiceAssistant' to be listed correctly by PipeWire.
REAL_PYTHON="$(readlink -f "$VENV/bin/python3")"
if ! cmp -s "$REAL_PYTHON" "$VENV/bin/VoiceAssistant"; then
    cp "$REAL_PYTHON" "$VENV/bin/VoiceAssistant"
fi

cd "$DIR"
exec "$VENV/bin/VoiceAssistant" main.py
```
Il blocco `NEEDS_INSTALL` / `pip install` sparisce: le dipendenze di base diventano voci di `data/dependencies/python_deps.json` con `"is_critical": true` — `sounddevice`, `dasbus`, `notify2`, `huggingface-hub` (con `import_name` rispettivamente `sounddevice`, `dasbus`, `notify2`, `huggingface_hub`). **Attenzione:** `notify2` importa il modulo `dbus` (dbus-python), che su Fedora arriva dal pacchetto di sistema `python3-dbus` (verificato con la CI). Aggiungi a `data/dependencies/system_deps.json` una voce per dbus-python (Fedora `python3-dbus`, Debian/Ubuntu `python3-dbus`, Arch `python-dbus`; verifica i nomi per ogni gestore già elencato in `package_managers.json`), e il primo avvio (§7.5) deve controllarla prima di creare il venv.

**Passo 7.3.c — Altri riferimenti al vecchio venv:**
- `src/gui/start.sh` riga ~7: `DAEMON_VENV="${XDG_DATA_HOME:-$HOME/.local/share}/voice-assistant/venv"` (il resto resta: se il venv non c'è, la GUI parte col Python di sistema, che basta per il primo avvio).
- `src/gui/dependency_installer.py` riga ~33: `_VENV_PIP` diventa `str(venv_dir() / "bin" / "pip")`; se il venv non esiste, **non** ripiegare su `pip3` di sistema (riga ~199): restituisci `(False, "virtualenv missing")`.
- `meson.build` riga ~38: `rm -rf "$TMPDIR/daemon/venv"` può restare (innocuo).
- `src/daemon/core/logger.py` riga ~189: `venv_packages` continua a funzionare perché usa l'interprete corrente.

### 7.4 `extension.js` minimo

**Decisioni:** l'estensione fa **solo** indicatore, collegamento D-Bus e avvio della GUI su azione dell'utente. Il daemon vive insieme all'estensione: nessun `systemctl enable`, e `disable()` ferma il daemon (quindi con lo schermo bloccato l'assistente non ascolta: accettato dall'utente).

**Passo 7.4.a — Eliminare:**
1. la funzione `setupDaemonServices` (righe ~32–167) e la sua chiamata in `enable()` (riga ~668);
2. il fallback XML D-Bus scritto nel codice (righe ~170–203): l'interfaccia si legge **solo** dalla GResource; se la lettura fallisce, `console.error(...)`, nessun proxy e stato `unavailable`;
3. il blocco `Gdk.Display` / `Gtk.IconTheme` in `enable()` (righe ~627–642): `getIcon()` (riga ~207) usa già i file dell'estensione per percorso assoluto.

**Passo 7.4.b — Nuovo metodo D-Bus `Quit`.**
- `data/dbus/org.local.VoiceAssistant.xml`: aggiungi `<method name="Quit"/>`.
- `src/daemon/main.py`, classe `VoiceAssistant`:
  ```python
  def Quit(self) -> None:
      """Stop audio, engines and child processes, then leave the main loop."""
      logger.info("[D-Bus] Quit requested")
      schedule_idle(self._shutdown)
  ```
  `_shutdown` chiude lo stream audio (`self._close_stream()`), ferma i server MCP (`mcp_manager`, metodo di arresto già esistente: cercalo con `grep -n "async def stop\|def stop" src/daemon/mcp/manager.py`), chiama `speaker_id_controller.stop()` se presente, `vector_store.close()` dove esiste, e infine esce dal main loop (cerca come `run_event_loop` crea il loop: `grep -n "def run_event_loop" -A15 src/daemon/*.py src/daemon/core/*.py`). Da Fase 4 chiamerà anche `WorkerSupervisor.stop_all()` (§8.3).
- `docs/dbus.md`: documenta `Quit`.

**Passo 7.4.c — `disable()`:** prima di azzerare `this._dbusProxy`, chiedi l'uscita del daemon senza aspettare:
```js
if (this._dbusProxy) {
    try {
        this._dbusProxy.QuitRemote(() => {});
    } catch (e) {
        console.warn(`[VoiceAssistant] Quit request failed: ${e.message}`);
    }
}
```
`QuitRemote` è generato da `Gio.DBusProxy.makeProxyWrapper` perché il metodo è nell'XML (7.4.b).

**Passo 7.4.d — Voce di menu "Set Up Voice Assistant".** Nel `QuickMenuToggle` (classe alla riga ~425) aggiungi una voce `PopupMenu.PopupMenuItem(_('Set Up Voice Assistant'))` visibile **solo** quando lo stato è `unavailable`; al clic chiama `this._extension._launchGuiDirect(['--first-run'])`. Il segnale `activate` va scollegato in `destroy()`.

**Passo 7.4.e — Controllo `enable()`/`disable()`:** ogni `connect`, `bus_watch_name`, `addKeybinding`, indicatore e `resources_register` di `enable()` deve avere la sua rimozione in `disable()`. Scrivi la tabella nella richiesta di review (colonna "creato in", colonna "rimosso in").

**Verifica 7.4:**
```bash
grep -nE "Gtk|Gdk|Adw|systemctl|setupDaemonServices|replace_contents|make_directory_with_parents" src/extension.js
# Atteso: nessun risultato
wc -l src/extension.js   # annota il numero nella review (oggi 869)
```

### 7.5 Primo avvio dalla GUI (installazione con consenso)

**Passo 7.5.a — Nuovo file `src/gui/first_run.py`**, classe `FirstRunDialog(Adw.Dialog)`, aperto da `gui/main.py` con `--first-run` (e automaticamente all'avvio della GUI se `is_setup_complete()` è falso). Funziona col **Python di sistema** (solo PyGObject), perché il venv ancora non c'è.

Passi mostrati all'utente, in quest'ordine, con un pulsante "Continue" e la possibilità di annullare:
1. **Consenso.** Testo: cosa verrà installato (elenco dei pacchetti di base da `python_deps.json` con `is_critical: true`, più i pacchetti dei motori predefiniti per la lingua del desktop — da Fase 5, §9.8.d), dove (`venv_dir()`), spazio stimato. Pulsanti "Install" / "Cancel".
2. **Creazione venv:** `python3 -m venv --system-site-packages <venv_dir()>` (in un thread, con `subprocess.run(..., check=False, capture_output=True)`; errore → messaggio con le ultime righe di stderr).
3. **Installazione pacchetti:** riusa `_install_pip_packages` di `dependency_installer.py` (§7.3.c).
4. **File di servizio** (scritti dalla GUI, **non** dall'estensione), usando i modelli nella GResource `services/*.in` letti con `Gio.resources_lookup_data`:
   - `~/.local/share/dbus-1/services/org.local.VoiceAssistant.service` con `@startScript@` = `extension_dir()/daemon/start.sh`;
   - `~/.config/systemd/user/voice-assistant.service` con lo stesso script. **Nessuna** sezione `[Install]`, **nessun** `systemctl enable`. Aggiungi nella sezione `[Unit]` del modello `data/services/voice-assistant.service.in`: `StartLimitIntervalSec=60` e `StartLimitBurst=3`;
   - poi `systemctl --user daemon-reload` (una volta).
5. **Voce nel menu applicazioni:** `~/.local/share/applications/org.local.VoiceAssistant.GUI.desktop` dal modello `org.local.VoiceAssistant.desktop.in`, dove la riga `Icon=vocal-assistant-icon` diventa `Icon=@iconPath@` e la GUI sostituisce `@iconPath@` con il percorso assoluto `extension_dir()/icons/hicolor/scalable/apps/vocal-assistant-icon.svg`. **Niente** file in `~/.local/share/icons` e niente `gtk-update-icon-cache`.
6. **Avvio del daemon:** chiama il metodo D-Bus `GetState` (l'attivazione D-Bus avvia il daemon); attendi al massimo 20 s; mostra "Ready" oppure l'errore.

Scrivi i file **in modo idempotente**: il dialogo si può ripetere senza danni.

**Passo 7.5.b — `is_setup_complete()`** in `first_run.py`: vero se (1) `venv_python()` esiste, (2) `subprocess.run([venv_python(), "-c", "import dasbus, notify2, sounddevice"])` restituisce 0, (3) i due file di servizio esistono e contengono il percorso attuale di `start.sh`.

**Passo 7.5.c — Togliere dal daemon** `DaemonRuntimeManager.ensure_desktop_file` (`runtime_manager.py`, righe ~177–252) e la sua chiamata (`grep -rn "ensure_desktop_file" src`): ora lo fa la GUI al passo 5. Nelle notifiche l'hint `desktop-entry` resta `org.local.VoiceAssistant.GUI`.

**Test** `tests/test_first_run.py` (senza rete, senza pip reale: `subprocess.run` sostituito da un finto; `HOME`/`XDG_*` già temporanei da §4.2.a): (1) i file di servizio vengono scritti con il percorso di `start.sh`; (2) una seconda esecuzione produce file identici; (3) il `.desktop` contiene `Icon=/` (percorso assoluto); (4) `is_setup_complete()` falso senza venv.

### 7.6 Niente letture di file al momento dell'import

Letture da spostare dentro funzioni con cache (`functools.lru_cache(maxsize=None)`), chiamate al primo uso:
| File | Riga ~ | Oggi | Diventa |
|---|---|---|---|
| `daemon/core/runtime_manager.py` | 78 | `_OPTIONAL_PYTHON_DEPS = _load_optional_python_deps()` | `@lru_cache` su `_load_optional_python_deps`; ogni uso di `_OPTIONAL_PYTHON_DEPS` diventa `_load_optional_python_deps()` |
| `daemon/core/runtime_manager.py` | 124 | `_SYSTEM_DEPS = _load_system_deps()` | idem con `_load_system_deps()` |
| `gui/components/settings/general.py` | 47 | `SUPPORTED_LANGUAGES = _load_supported_languages()` | idem |

(Quelle di `pipeline.py`, `assistant_runtime.py` e `skill_executor.py` sono già state eliminate in Fase 1.)

**Verifica 7.6:**
```bash
grep -rnE "^[A-Za-z_]+ *= *[A-Za-z_\.]*(load|_load)[A-Za-z_]*\(" src --include=*.py | grep -v venv
grep -rnE "^    [A-Z_]+ *= *[A-Za-z_\.]*load[A-Za-z_]*\(" src --include=*.py | grep -v venv
# Atteso: nessun risultato in entrambi
```
e, in `src/daemon/main.py`, la registrazione della GResource avviene **prima** di qualsiasi lettura di dati (spostala all'inizio del blocco `__main__`, subito dopo `setup_logger()`).

### 7.7 Criteri di accettazione della Fase 3
- [ ] Verifiche 7.1.b, 7.1.c, 7.2, 7.4, 7.6 con i risultati attesi; nuovi test verdi.
- [ ] **Prova su account pulito** (crea un utente di prova: `sudo useradd -m va-test`, accedi con quell'utente): installa lo zip con `gnome-extensions install`, abilita l'estensione, lo stato è `unavailable`, dal menu "Set Up Voice Assistant" completa il primo avvio, il daemon parte e l'assistente risponde alla wake word.
- [ ] `gnome-extensions disable voice-assistant@scroker.github.io` → entro 5 s `busctl --user status org.local.VoiceAssistant` fallisce e nessun processo `VoiceAssistant` resta attivo (`pgrep -a VoiceAssistant` vuoto). `enable` → il daemon riparte.
- [ ] Aggiornamento dell'estensione (reinstallazione dello zip) → il venv resta e il daemon riparte senza reinstallare pacchetti.
- [ ] Nessun file in `~/.local/share/icons` creato dall'estensione.

---

## 8. Fase 4 — Architettura

Ordine obbligatorio: 8.1 → 8.2 → 8.3 → 8.4 → 8.5. Ogni sotto-passo numerato = almeno un commit. **Nessun cambiamento di comportamento visibile all'utente** in questa fase (tranne i crash isolati di §8.3).

**Situazione verificata:**
- Due "stati" separati: `StateMachine` in `core/state.py` (usata solo da `PipelineController`) e `owner._state` scritto da `core/lifecycle.py:45`. La prima inoltra alla seconda con una callback (`runtime_manager.py:1232`). `assistant_runtime.py` legge `owner._state` in 11 punti confrontandolo anche con stringhe come `"AssistantState.SPEAKING"`.
- I "controller" non sono componenti veri: `assistant_runtime.py` legge `self.owner.*` 195 volte e ne scrive 80; `runtime_manager.py` 245 e 67; `provider_manager.py` 69 e 10. Stesso attributo scritto da file diversi (per esempio `sherpa_spotter` da 3 file).
- `VoiceAssistant` (`main.py`) ha 96 metodi; `DaemonOwner` (`core/daemon_protocol.py`) non elenca 14 membri usati davvero.
- 36 funzioni oltre 100 righe; `_audio_loop` ne ha 252.

### 8.1 Una sola StateMachine con transizioni esplicite

**Passo 8.1.a — Censire le transizioni reali.** Prima di scrivere codice:
```bash
grep -rnE "set_state\(|_state = |state_machine\.set_state\(" src/daemon --include=*.py | grep -v venv > /tmp/va_transitions.txt
```
Per ogni riga annota "da quale stato può arrivare → a quale stato". Confronta con la tabella di 8.1.b. **Se trovi una transizione reale che la tabella non ammette, fermati e segnalala nella review**: non aggiungerla di tua iniziativa.

**Passo 8.1.b — `src/daemon/core/state.py` riscritto:**
```python
"""Single source of truth for the assistant state."""
from __future__ import annotations

import logging
import threading
from enum import Enum
from typing import Callable, Dict, FrozenSet, List

logger = logging.getLogger("VoiceAssistant.State")


class AssistantState(str, Enum):
    DISABLED = "disabled"
    LOADING = "loading"
    DOWNLOADING = "downloading"
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    ERROR = "error"


S = AssistantState
ALLOWED_TRANSITIONS: Dict[AssistantState, FrozenSet[AssistantState]] = {
    S.DISABLED:    frozenset({S.IDLE, S.LOADING, S.ERROR}),
    S.LOADING:     frozenset({S.IDLE, S.LISTENING, S.DOWNLOADING, S.DISABLED, S.ERROR}),
    S.DOWNLOADING: frozenset({S.LOADING, S.IDLE, S.DISABLED, S.ERROR}),
    S.IDLE:        frozenset({S.LISTENING, S.PROCESSING, S.LOADING, S.DOWNLOADING, S.DISABLED, S.ERROR}),
    S.LISTENING:   frozenset({S.PROCESSING, S.IDLE, S.LOADING, S.DISABLED, S.ERROR}),
    S.PROCESSING:  frozenset({S.SPEAKING, S.IDLE, S.LISTENING, S.DISABLED, S.ERROR}),
    S.SPEAKING:    frozenset({S.IDLE, S.LISTENING, S.PROCESSING, S.DISABLED, S.ERROR}),
    S.ERROR:       frozenset({S.IDLE, S.LOADING, S.DISABLED}),
}

Listener = Callable[[AssistantState, AssistantState], None]


class StateMachine:
    """Thread-safe. Listeners run on the calling thread, outside the lock."""

    def __init__(self, initial: AssistantState = S.DISABLED):
        self._lock = threading.Lock()
        self._state = initial
        self._listeners: List[Listener] = []

    @property
    def state(self) -> AssistantState:
        with self._lock:
            return self._state

    def add_listener(self, listener: Listener) -> None:
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def transition(self, new_state: AssistantState) -> bool:
        """Apply the transition if allowed. Same state = no-op (returns False, no listeners)."""
        new_state = AssistantState(new_state)
        with self._lock:
            old_state = self._state
            if new_state == old_state:
                return False
            if new_state not in ALLOWED_TRANSITIONS[old_state]:
                logger.warning("Rejected state transition %s -> %s", old_state.value, new_state.value)
                return False
            self._state = new_state
            listeners = list(self._listeners)
        logger.info("State %s -> %s", old_state.value, new_state.value)
        for listener in listeners:
            try:
                listener(old_state, new_state)
            except Exception:
                logger.exception("State listener failed")
        return True
```
Nota: **non** esiste un metodo `set_state` che restituisce un booleano; nessun codice passa `transition` a `schedule_idle` sperando nel valore di ritorno. `UNAVAILABLE` sparisce dal daemon: è solo uno stato dell'estensione quando il daemon non è sul bus. `ErrorCollector.update_context("state", ...)` va fatto in un listener, non dentro la classe.

**Passo 8.1.c — Effetti del cambio di stato in un listener.** Il corpo di `DaemonLifecycle.set_state` (`core/lifecycle.py`, righe ~34–94: segnale D-Bus `StateChanged`, inibizione della sospensione, apertura/chiusura del microfono, reset del riconoscitore, watchdog del parlato) diventa il listener `DaemonLifecycle.on_state_changed(old, new)`, che **per intero** esegue il lavoro sul main loop con `schedule_idle`. Il caso speciale di oggi `self.owner._state = "loading"` (riga ~67) diventa `state_machine.transition(S.LOADING)`.

**Passo 8.1.d — Un solo punto di scrittura.**
1. `VoiceAssistant.set_state(state)` diventa `self.state_machine.transition(AssistantState(state))`; la `StateMachine` si crea in `VoiceAssistant.__init__` con stato iniziale `DISABLED` (non più in `initialize_pipeline`, `runtime_manager.py:1231`); `PipelineController` riceve **la stessa** istanza.
2. Elimina `self._state` da `main.py` (riga ~74), `lifecycle.py` e `daemon_protocol.py`. `GetState` restituisce `self.state_machine.state.value`.
3. Sostituisci ogni lettura di `owner._state` (elenco: `grep -rn "owner\._state\|self\._state" src/daemon --include=*.py`) con confronti sull'enum, per esempio `self.owner.state_machine.state in (S.SPEAKING, S.PROCESSING)`. Elimina **tutte** le stringhe `"AssistantState.XXX"`.

**Test** `tests/test_core_state.py` (riscrivi il file):
- ogni coppia `(da, a)` ammessa → `transition` restituisce `True` e il listener riceve `(da, a)`;
- una coppia non ammessa (es. `DISABLED → SPEAKING`) → `False`, stato invariato, listener **non** chiamato, warning nel log;
- stesso stato → `False`, listener non chiamato;
- 8 thread che fanno 1000 transizioni `IDLE↔LISTENING` → nessuna eccezione e lo stato finale è uno dei due;
- un listener che solleva un'eccezione non impedisce agli altri di essere chiamati.

**Verifica 8.1:**
```bash
grep -rn "AssistantState\.[A-Z]*\"" src/daemon --include=*.py | grep -v venv   # nessun risultato (stringhe "AssistantState.X")
grep -rn "\._state\b" src/daemon --include=*.py | grep -v "venv\|core/state.py"  # nessun risultato
```

### 8.2 Divisione per dominio: ogni componente possiede il suo stato

**Regole:**
1. Ogni componente è una classe con **dipendenze esplicite nel costruttore**. Vietato ricevere `owner` e leggere o scrivere suoi attributi.
2. Uno stato ha **un solo proprietario**: solo quel componente lo scrive; gli altri usano metodi o proprietà in sola lettura.
3. `VoiceAssistant` resta **solo** lo strato D-Bus: ogni metodo D-Bus è una riga o poche righe che delegano.
4. Ogni nuovo file rispetta i limiti ruff (C901 ≤ 15, PLR0915 ≤ 60 istruzioni) e le regole di §6.2, §6.3, §6.7 (log in inglese). Quando sposti codice, correggilo lì: `except` silenziosi, log italiani, log con testo dell'utente.
5. Una componente per commit; dopo ogni commit i test sono verdi e il daemon funziona (prova manuale breve: wake word → domanda → risposta).

**Componenti di destinazione e attributi che possiedono** (ricavati dall'elenco degli attributi scritti oggi su `owner`):
| # | Componente (nuovo file) | Possiede | Oggi scritto in |
|---|---|---|---|
| 1 | `core/notifications.py` → `Notifier` | `notify_user`, `_active_notifs`, `_deps_notif`, `_last_deps_notif_id`, `_missing_deps`, azioni GDBus (`_on_gdbus_action_invoked`) | `runtime_manager.py`, `provider_manager.py` |
| 2 | `core/model_downloads.py` → `ModelDownloads` | `_downloading_models`, `_cancel_requests`, `download_model`, `cancel_download`, `delete_model`, `download_sherpa_model` | `provider_manager.py` |
| 3 | `core/settings.py` → `AssistantSettings` (estende `SettingsObserver`) | `settings`, `language`, `_reload_timer`, lettura di tutte le chiavi, notifica dei cambi per chiave | `runtime_manager.py`, `assistant_runtime.py` |
| 4 | `audio/input.py` → `AudioInput` (da `core/audio_runtime.py`) | `_stream`, `q`, `audio_filter`, `_ignore_audio_until`, `_audio_thread`, `RestartPolicy` di §6.5 | `audio_runtime.py`, `assistant_runtime.py`, `lifecycle.py` |
| 5 | `core/wakeword.py` → `WakeWordDetector` | `wakeword`, `wakeword_engine`, `oww_model_name`, `oww_model_instance`, `_oww_buffer`, `sherpa_ww_model_dir`, `sherpa_model`, `sherpa_spotter`, `sherpa_stream`, `ww_recognizer`, `ww_model`, `ww_provider`, `vosk_ww_model`, `fallback_to_vosk_wakeword` | `runtime_manager.py`, `assistant_runtime.py`, `provider_manager.py` |
| 6 | `core/speech_recognition.py` → `SpeechRecognizer` | `provider`, `stt_provider`, `provider_name`, `model_name`, `hardware`, `extra_config`, `models_dir`, `_stt_load_pending`, `_pending_state_after_provider_load`, `_load_id`, `load_provider`, `ensure_stt_provider` | `provider_manager.py`, `runtime_manager.py`, `assistant_runtime.py` |
| 7 | `core/listening_session.py` → `ListeningSession` (fine frase, sola wake word, timeout 2,5 s / 6 s, contesto) | `_listening_start_time`, `_last_speech_time`, `_last_partial_text`, `_last_partial_change_time`, `_active_listen_context_id`, `_was_interrupting`, `_speaker_session` | `assistant_runtime.py`, `lifecycle.py` |
| 8 | `core/request_queue.py` → `RequestQueue` (`enqueue_request`, `_process_text`) | coda delle richieste e suo thread | `assistant_runtime.py` |
| 9 | `core/daemon_app.py` → `DaemonApp` (radice di composizione) | crea **tutti** i componenti nell'ordine giusto e li collega; possiede `llm_service`, `tts_manager`, `mcp_manager`, `context_manager`, `pipeline_controller`, `speaker_id_controller`, `model_manager`, `state_machine` | `runtime_manager.py` |

`_audio_loop` (252 righe) si divide così: il ciclo resta in `AudioInput` e, per ogni blocco audio, chiama un metodo per stato (`WakeWordDetector.feed(chunk)` negli stati `idle`/`speaking`/`processing`, `ListeningSession.feed(chunk)` in `listening`). Nessun metodo supera i limiti ruff.

**Procedura per ogni componente (ripetila 9 volte):**
1. Crea la classe con costruttore esplicito, per esempio `WakeWordDetector(settings: AssistantSettings, notifier: Notifier, downloads: ModelDownloads, on_detected: Callable[[str], None])`.
2. Sposta attributi e metodi della tabella; nel vecchio file lascia **temporaneamente** una delega di una riga solo se serve a non rompere il commit.
3. Togli gli attributi spostati da `DaemonOwner` (`core/daemon_protocol.py`).
4. Scrivi i test del componente con dipendenze finte (nessun `owner` finto).
5. Verifica che il numero di accessi a `owner` sia sceso: `grep -c "self\.owner\." src/daemon/core/*.py` e annota il numero nel messaggio di commit.

**Fine di 8.2:**
- `core/daemon_protocol.py`, `core/assistant_runtime.py`, `core/runtime_manager.py`, `core/provider_manager.py`, `core/lifecycle.py` sono **eliminati** (il loro contenuto è nei componenti);
- `grep -rn "self\.owner" src/daemon --include=*.py | grep -v venv` → nessun risultato;
- `VoiceAssistant` contiene solo metodi e segnali D-Bus (conta i metodi: `grep -c "    def " src/daemon/main.py`, annotalo nella review);
- controlla ora le sezioni per-tool di `responses.json` lasciate in §5.4.c: se nessun file le legge più (`grep -rn "\"quick_settings\"" src --include=*.py` ecc.), eliminale.

### 8.3 Motori ML in processi separati (worker)

**Decisioni dell'utente:** core leggero in puro Python (D-Bus, stato, impostazioni, logica della pipeline) + un processo worker per ruolo, avviato quando serve e sorvegliato con attese crescenti e numero massimo di tentativi (poi stato `error`); comunicazione JSON-RPC su stdin/stdout più frame audio binari; **prima** si misura la latenza con un prototipo; cattura audio, wake word e STT nello **stesso** worker; i worker terminano quando stdin si chiude; il core li termina all'arresto (EGO); `faulthandler` in ogni processo.

**Passo 8.3.a — Prototipo di latenza (V3, bloccante).** Cartella `experiments/ipc_latency/` (non va nello zip, §7.1.c):
- `worker.py`: legge frame dal protocollo di 8.3.b; per ogni frame audio risponde con un messaggio JSON `{"jsonrpc":"2.0","method":"audio.ack","params":{"seq":N}}`; risponde a `ping` con `pong`.
- `bench.py`: avvia il worker con `subprocess.Popen`, invia 3000 frame audio da 20 ms (640 byte PCM int16 16 kHz) al ritmo reale e 1000 `ping`; misura il tempo tra invio e risposta.
- Criteri: **p95 < 5 ms** per i frame audio, **p95 < 20 ms** per `ping`. Riporta p50/p95/p99 nella review. **Se non sono rispettati, fermati**: si decide con l'utente prima di proseguire.

**Passo 8.3.b — Protocollo** `src/daemon/workers/protocol.py`:
```
frame   := type (1 byte) | length (4 byte, big-endian, uint32) | payload (length byte)
type 0x01 = JSON-RPC 2.0 message, UTF-8 (request, response or notification)
type 0x02 = audio chunk: PCM int16 little-endian, mono, 16 kHz
max length = 16 MiB; a larger length is a protocol error: close the connection
```
Funzioni: `write_json(stream, obj)`, `write_audio(stream, pcm: bytes)`, `read_frame(stream) -> tuple[int, bytes] | None` (`None` = EOF). Uso di `stream.write` + `flush` su file binari (`sys.stdin.buffer` / `sys.stdout.buffer`). **stdout è riservato ai frame**: nei worker i log vanno su stderr (`logging.basicConfig(stream=sys.stderr)`) e il core li inoltra al proprio logger riga per riga, con prefisso `[worker:<nome>]`.
Test `tests/test_worker_protocol.py`: andata e ritorno di JSON e audio su `io.BytesIO`; frame troncato → `None`; lunghezza > 16 MiB → eccezione `ProtocolError`.

**Passo 8.3.c — Supervisore** `src/daemon/workers/supervisor.py`, classe `WorkerSupervisor`:
- `start(name)`: `subprocess.Popen([str(venv_python()), "-m", f"workers.{name}_worker"], cwd=extension_dir()/"daemon", stdin=PIPE, stdout=PIPE, stderr=PIPE)`; un thread legge stdout (frame) e uno stderr (log);
- `request(name, method, params, timeout_s) -> result` (JSON-RPC con `id` crescente; eccezione `WorkerError` su errore o timeout), `notify(name, method, params)`, `send_audio(name, pcm)`;
- uscita inattesa del processo → `RestartPolicy` di §6.5 (una per worker); a tentativi esauriti → `state_machine.transition(S.ERROR)` e notifica `_("The %s engine keeps crashing and has been stopped.")`;
- `stop(name)` e `stop_all()`: chiudi stdin → aspetta 3 s → `terminate()` → aspetta 2 s → `kill()`. `stop_all()` si chiama da `Quit` (§7.4.b) e alla chiusura del main loop;
- avvio **su richiesta** (al primo uso del motore) e arresto quando `model_manager` scarica il modello per inattività.

**Passo 8.3.d — Scheletro comune dei worker** `src/daemon/workers/base_worker.py`: `faulthandler.enable()` su stderr; ciclo `read_frame(sys.stdin.buffer)`; su EOF esce con codice 0; smista le richieste JSON-RPC ai metodi `rpc_<nome>`; un'eccezione in un metodo diventa una risposta di errore JSON-RPC (codice `-32000`, messaggio `str(exc)`), **non** un'uscita del processo.

**Passo 8.3.e — Worker da creare, in quest'ordine** (un commit ciascuno; il vecchio codice nel core si elimina nello stesso commit):
| Ordine | Worker | Contenuto | Messaggi principali |
|---|---|---|---|
| 1 | `llm_worker` | solo LLM **locale** llama.cpp (`services/llm_service.py`, parte `LocalGGUF`). Ollama e cloud restano nel core (sono solo HTTP) | `load(model_path, n_ctx, gpu_layers)`, `generate(prompt, history)` → notifiche `llm.token {text}` e `llm.done` |
| 2 | `tts_worker` | Piper, eSpeak, (Fase 5) sherpa TTS | `synthesize(text, voice, speed)` → risposta con i byte WAV in un frame audio |
| 3 | `speaker_worker` | backend Speaker ID (resemblyzer; in Fase 5 sherpa) | `load()`, `embed()` con audio in frame 0x02 → `embedding [..]` |
| 4 | `nlu_worker` | router semantico ONNX (`skills/semantic_router.py`) | `match(text, min_score)` |
| 5 | `audio_worker` | cattura audio + filtro + wake word + VAD + STT (`AudioInput`, `WakeWordDetector`, `SpeechRecognizer`, `ListeningSession` di §8.2) | notifiche `wakeword.detected`, `speech.final {text}`, `audio.level`; frame 0x02 dal worker al core **solo** durante l'ascolto (servono allo Speaker ID) |

Il core inoltra i frame audio dell'ascolto al `speaker_worker`.

**Verifica 8.3:**
- test del protocollo e del supervisore (con un worker finto scritto nel test, che risponde a `ping` e può essere fatto terminare con `os._exit(1)`): riavvio con attese crescenti, stato `error` a tentativi esauriti, `stop_all()` lascia zero processi figli;
- prova manuale: `pkill -f tts_worker` durante una risposta → la risposta successiva funziona (il worker è ripartito), il daemon non si è fermato;
- `ps -o rss,cmd --ppid $(pgrep -f "VoiceAssistant main.py")`: annota RSS del core e dei worker nella review (confronto con 1,34 GB del processo unico di oggi).

### 8.4 Modello di concorrenza documentato

Nuovo `docs/concurrency.md` (in inglese) con: elenco dei thread del core e dei worker e cosa fa ciascuno; la regola "oggetti GLib e segnali D-Bus solo sul main loop, da altri thread solo con `schedule_idle`"; chi possiede quale stato (tabella di §8.2); ciclo di vita dei worker (§8.3.c). Collega il file da `docs/architecture.md`.

### 8.5 pyright e ruff diventano bloccanti

`.github/workflows/ci.yml`:
- `pyright || true` → `pyright` (bloccante). Correggi gli errori che emergono, **senza** `# type: ignore` generici: se serve un ignore, deve essere `# pyright: ignore[<regola>]` con commento.
- Ruff: la parte "whole tree" diventa bloccante per `E`, `F`, `W`, `BLE001`, `S110`, `E722`; `C901` e `PLR0915` restano bloccanti sui file nuovi/modificati.
- `tests/test_log_language.py`: `LEGACY` vuoto.

### 8.6 Criteri di accettazione della Fase 4
- [ ] Verifiche 8.1, 8.2 (fine), 8.3 con i risultati attesi; p95 del prototipo riportati.
- [ ] CI verde con pyright e ruff bloccanti.
- [ ] Nessun cambiamento funzionale: wake word, domanda vocale, domanda scritta nella GUI, Fast-Path, interruzione con "stop", Speaker ID in modalità `informative` e `gate` funzionano come prima della fase.
- [ ] `gnome-extensions disable` → nessun processo del core o dei worker resta attivo.

---

## 9. Fase 5 — Motori, modelli, dipendenze su richiesta, GPU per l'LLM

**Decisioni dell'utente (non cambiarle):**
1. **Regola generale:** sherpa-onnx è il motore predefinito ovunque sia utilizzabile. Tutti gli altri motori restano (R3) ma sono **opzionali**, e la UI dice che richiedono l'installazione di dipendenze.
2. **Tabella dei predefiniti per lingua.** Se sherpa-onnx (anche con modelli multilingua) non supporta la lingua per un ruolo, si usa un motore opzionale **e** nella UI sherpa non si può scegliere per quella lingua.
3. **Italiano:** wake word = **Vosk** (sherpa KWS non selezionabile: esistono solo modelli inglese/cinese, e la prova con voci italiane ha dato 1 riconoscimento su 8); STT = **sherpa-onnx FastConformer multilingua int8**, fine frase decisa dal **VAD Silero**; Parakeet TDT 0.6b v3 come opzione più grande; Vosk e Whisper opzionali.
4. **TTS:** predefinito = voci Piper in formato sherpa-onnx (VITS); `piper-tts` opzionale (le sue voci si scaricano a parte).
5. **Speaker ID:** predefinito = sherpa-onnx CAM++ (modello esatto dopo V1); resemblyzer opzionale, con torch **solo CPU**.
6. **Dipendenze e modelli si scaricano solo quando il motore serve.** Se l'utente sceglie un motore il cui pacchetto manca, la GUI chiede subito il consenso e applica la scelta **solo dopo** l'installazione riuscita. Nessuna disinstallazione automatica.
7. **Tutto su CPU tranne l'LLM locale**, che deve poter usare GPU Intel, AMD e NVIDIA quando disponibili.
8. Whisper medium/large restano selezionabili ma con l'etichetta "slow on CPU".

Ordine obbligatorio: 9.1 → 9.10.

### 9.1 Tabella dei predefiniti per lingua

**Passo 9.1.a — Nuovo file `data/catalog/engine_defaults.json`** (aggiungilo anche alla GResource: `data/schemas/org.gnome.shell.extensions.voice-assistant.gresource.xml`). Voci per `it` ed `en` già decise:
```json
{
  "it": {
    "wakeword": {"default": {"engine": "vosk", "model": "vosk-model-small-it-0.22"},
                 "available": ["vosk", "openwakeword"]},
    "stt":      {"default": {"engine": "sherpa-onnx", "model": "sherpa-onnx-nemo-fast-conformer-transducer-be-de-en-es-fr-hr-it-pl-ru-uk-20k-int8"},
                 "available": ["sherpa-onnx", "vosk", "whisper", "openai_cloud", "groq_cloud"]},
    "tts":      {"default": {"engine": "sherpa-onnx", "voice": "vits-piper-it_IT-paola-medium-int8"},
                 "available": ["sherpa-onnx", "piper", "espeak", "openai", "system"]},
    "speaker":  {"default": {"engine": "sherpa-onnx", "model": "<V1>"},
                 "available": ["sherpa-onnx", "resemblyzer"]}
  },
  "en": {
    "wakeword": {"default": {"engine": "sherpa-onnx", "model": "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01"},
                 "available": ["sherpa-onnx", "vosk", "openwakeword"]},
    "stt":      {"default": {"engine": "sherpa-onnx", "model": "sherpa-onnx-nemo-fast-conformer-transducer-be-de-en-es-fr-hr-it-pl-ru-uk-20k-int8"},
                 "available": ["sherpa-onnx", "vosk", "whisper", "openai_cloud", "groq_cloud"]},
    "tts":      {"default": {"engine": "sherpa-onnx", "voice": "<9.1.b>"},
                 "available": ["sherpa-onnx", "piper", "espeak", "openai", "system"]},
    "speaker":  {"default": {"engine": "sherpa-onnx", "model": "<V1>"},
                 "available": ["sherpa-onnx", "resemblyzer"]}
  }
}
```
I nomi dei modelli sono verificati sulle release di sherpa-onnx (archivi `.tar.bz2` in `asr-models`, `kws-models`, `tts-models`). Per lo STT si usa la variante **transducer** (vedi V2 in §11 prima di chiudere la fase).

**Passo 9.1.b — Le altre lingue** di `data/locales/supported_languages.json`. Compila **una voce per ogni lingua** applicando queste regole, senza inventare nomi:
- **wake word:** `sherpa-onnx` è disponibile **solo** per `en` (gigaspeech) e `zh` (wenetspeech). Per le altre lingue: default `vosk` con il modello di `data/catalog/stt_models.json` → `defaults.vosk.<lingua>` se esiste; se non esiste, default `openwakeword` (modello `alexa`, come il valore di `oww-model` nello schema) e `vosk` fuori da `available`.
- **STT:** `sherpa-onnx` con il modello FastConformer sopra **solo** se la lingua è tra `be de en es fr hr it pl ru uk`. Altrimenti default `whisper` modello `small`, e `sherpa-onnx` fuori da `available`.
- **TTS:** `sherpa-onnx` solo se esiste una voce Piper per quella lingua nella release `tts-models`. Elenca i nomi con:
  ```bash
  curl -sL https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/checksum.txt | cut -f1 | grep "^vits-piper-<ll>_" | grep -- "-medium-int8.tar.bz2"
  ```
  Scegli la **prima** voce `-medium-int8` in ordine alfabetico (togli `.tar.bz2` dal nome). Nessuna voce → default `piper`, `sherpa-onnx` fuori da `available`.
- **speaker:** uguale per tutte le lingue.

**Passo 9.1.c — Lettura** in un modulo nuovo `src/daemon/core/engine_defaults.py`:
```python
def engine_defaults(language: str) -> dict:
    """Return the entry for the language, falling back to 'en' for unknown languages."""

def default_for(role: str, language: str) -> dict:          # role: wakeword | stt | tts | speaker

def is_available(role: str, engine: str, language: str) -> bool:
```
Test `tests/test_engine_defaults.py`: `is_available("wakeword", "sherpa-onnx", "it") is False`; `default_for("stt", "it")["engine"] == "sherpa-onnx"`; lingua sconosciuta `"xx"` → valori di `en`; **ogni** lingua di `supported_languages.json` ha tutte e 4 le chiavi; ogni `default.engine` è contenuto nel suo `available`.

### 9.2 Impostazioni: vuoto = automatico

**Passo 9.2.a — Schema** (`data/schemas/...gschema.xml`):
- il default diventa `''` (= "usa il predefinito della lingua") per: `wakeword-engine`, `vosk-ww-model`, `sherpa-model`, `stt-provider`, `stt-model`, `tts-engine`, `tts-provider`, `tts-voice`;
- aggiungi `speaker-id-backend` (tipo `s`, default `''`) e `speaker-id-model` (tipo `s`, default `''`);
- elimina `stt-hardware` (Whisper gira solo su CPU, decisione 7): togli la chiave, il gruppo `hw_grp` in `gui/components/settings/stt.py` (riga ~60) e la relativa parte di blueprint, e in `providers/whisper_provider.py` forza `device="cpu"`, `compute_type="int8"`;
- summary e description in inglese (§6.7).

**Passo 9.2.b — Risoluzione** in `AssistantSettings` (§8.2, componente 3): metodo `effective_engine(role) -> dict` che restituisce il valore della chiave se non è vuoto **e** `is_available(role, engine, language)`; altrimenti `default_for(role, language)`. Ogni componente legge **solo** `effective_engine`, mai direttamente le chiavi.

**Passo 9.2.c — Cambio di lingua.** Quando la chiave `language` cambia e il motore scelto a mano per un ruolo non è disponibile nella nuova lingua: reimposta la chiave a `''` e notifica `_("%s is not available for %s: switched to the default engine.")`.

### 9.3 UI: sherpa non selezionabile dove non funziona

1. `gui/components/settings/wakeword.py`: dopo `bind_radio_group` (riga ~23) e a ogni cambio di `language`, per ogni radio (`ww_engine_vosk_radio`, `ww_engine_oww_radio`, `ww_engine_sherpa_radio`) chiama `set_sensitive(is_available("wakeword", engine, lang))`; il radio non disponibile mostra nel sottotitolo `_("Not available for this language")`.
2. Stessa cosa in `stt.py` (radio `stt_engine_*`, righe ~86–90) e `tts.py`, dove aggiungi il radio `sherpa-onnx` (titolo `_("Built-in (sherpa-onnx)")`, sottotitolo `_("Recommended — no extra installation")`). Nei motori opzionali il sottotitolo termina con `_("Requires installing extra components")`.
3. `gui/components/settings/model_selector.py`, `_model_matches_language` (riga ~250): elimina il ramo che per `m_lang == "en"` restituisce `True` anche per `it`, `de`, `fr`, … (righe ~267–269, commento "Gigaspeech supporta keyword fonetiche…"): un modello KWS inglese vale solo per `en`.
4. Etichette dei modelli Whisper: nella costruzione dell'elenco (`services/catalog_manager.py`, `get_whisper_models`, riga ~321) per `medium`, `large-v2`, `large-v3` (e varianti che iniziano così) aggiungi al sottotitolo ` • ` + `_("slow on CPU")`.
5. Test GUI in `tests/test_gui.py`: con `language = "it"` il radio sherpa della wake word è **non sensibile**; con `en` è sensibile.

### 9.4 Download dei modelli sherpa-onnx (con checksum)

Generalizza `download_sherpa_model` (dopo la Fase 4 sta in `core/model_downloads.py`):
```python
SHERPA_RELEASES = {
    "wakeword": "kws-models",
    "stt": "asr-models",
    "vad": "asr-models",
    "tts": "tts-models",
    "speaker": "speaker-recongition-models",   # sic: the release tag is misspelled upstream
}

def download_sherpa_asset(self, role: str, asset_name: str, progress_cb=None) -> Path:
    """asset_name is the exact file name in the release, e.g. 'silero_vad.onnx' or '<name>.tar.bz2'."""
```
Passi obbligatori: (1) scarica `https://github.com/k2-fsa/sherpa-onnx/releases/download/<tag>/checksum.txt` e cerca `asset_name` con `parse_checksum_txt` (§6.6.a); **se manca, rifiuta il download** con errore; (2) scarica l'asset in `.part`; (3) `matches_sha256`; (4) se è `.tar.bz2` estrai con `tar.extractall(path, filter="data")` (Python ≥ 3.12; con Python più vecchio rifiuta i membri con percorso assoluto o `..`), altrimenti `os.replace`; (5) `write_manifest`. Cartella: `get_models_dir()/sherpa-onnx/<role>/<nome senza .tar.bz2>`.

Il VAD `silero_vad.onnx` (usato da STT sherpa e da Whisper) si scarica con `role="vad"` una sola volta e si condivide.

### 9.5 STT sherpa-onnx con fine frase dal VAD

**Passo 9.5.a — Verifica API** (R6), prima di scrivere:
```bash
python3 -c "import sherpa_onnx as s; print(s.__version__); help(s.OfflineRecognizer.from_transducer)" | head -40
python3 -c "import sherpa_onnx as s; help(s.VadModelConfig); help(s.VoiceActivityDetector)" | head -80
```
Usa **solo** i parametri che compaiono nell'output. Guarda i file dentro l'archivio estratto (`ls`) per i nomi di `encoder`, `decoder`, `joiner`, `tokens`.

**Passo 9.5.b — `src/daemon/providers/sherpa_stt_provider.py`**, classe `SherpaSTTProvider(STTProvider)` (interfaccia in `providers/base.py`: `process_chunk(data) -> (text, partial_text)`, `flush_and_transcribe()`, `reset()`, `get_available_models()`, `get_default_model(lang)`):
- al caricamento: `OfflineRecognizer.from_transducer(...)` con `num_threads = max(1, os.cpu_count() // 2)`, `provider="cpu"`; `VoiceActivityDetector` con Silero (`silero_vad.onnx`), `sample_rate=16000`;
- `process_chunk(data)`: converti int16 → float32 in [-1, 1]; passa al VAD; **finché** c'è parlato restituisci `("", "…")` come parziale fittizio (lo stesso trucco di `WhisperProvider`, `whisper_provider.py` righe ~200–260: il ciclo di fine frase in `ListeningSession` considera "finita" una frase quando il parziale non cambia per 1 s, quindi il parziale **deve restare identico** durante il silenzio e cambiare durante il parlato — usa un contatore di segmenti di parlato: `"." * n_segmenti`);
- quando il VAD chiude un segmento di parlato: accoda i campioni del segmento;
- `flush_and_transcribe()` e fine frase: crea uno stream `recognizer.create_stream()`, `accept_waveform(16000, campioni)`, `recognizer.decode_stream(stream)`, restituisci `stream.result.text.strip()`; poi azzera i buffer;
- `reset()`: azzera VAD e buffer;
- registralo in `providers/__init__.py`, `get_provider`: `elif p == "sherpa-onnx": return SherpaSTTProvider(...)`; in `get_default_model` usa `engine_defaults`.
- **Il testo parziale non viene mai mostrato** (§2): non introdurre la trascrizione in tempo reale.

**Passo 9.5.c — Catalogo** (`services/catalog_manager.py`): nuovo `get_sherpa_stt_models()` con due voci fisse: il FastConformer transducer int8 (`size_text` "≈107 MB", sottotitolo `_("Recommended • 10 languages")`) e `sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8` (`size_text` "≈487 MB", sottotitolo `_("More accurate • 25 European languages • larger download")`). Parakeet si carica con `OfflineRecognizer.from_transducer` come l'altro: verifica i nomi dei file nell'archivio.

**Test** (senza modelli, R5): `SherpaSTTProvider` con `OfflineRecognizer` e `VoiceActivityDetector` sostituiti da finti: (1) durante il parlato il parziale cambia a ogni nuovo segmento e resta uguale nel silenzio; (2) `flush_and_transcribe` restituisce il testo del finto e azzera il buffer; (3) `reset` svuota tutto.

### 9.6 TTS sherpa-onnx (voci Piper VITS)

1. Verifica API: `python3 -c "import sherpa_onnx as s; help(s.OfflineTtsVitsModelConfig); help(s.OfflineTts.generate)" | head -60`.
2. Nuova classe `SherpaTTSProvider(BaseTTSProvider)` in `services/tts_service.py` (dopo la Fase 4: nel `tts_worker`): modello `<voce>/<file>.onnx`, `tokens.txt`, `data_dir=<voce>/espeak-ng-data` (controlla i nomi reali con `ls` dopo l'estrazione); `generate(text, sid=0, speed=speed)` → converti `samples` float32 in WAV int16 con la `sample_rate` restituita.
3. Registra `"sherpa-onnx": SherpaTTSProvider(...)` nel dizionario `self.providers` di `TTSServiceManager` (riga ~543).
4. Catalogo voci: `get_sherpa_voices(lang)` legge i nomi da `checksum.txt` di `tts-models` (filtro `vits-piper-<ll>_`), mostrando per ogni voce solo la variante `-int8`.
5. Test con `OfflineTts` finto: la WAV prodotta ha intestazione RIFF e la sample rate del finto.

### 9.7 Speaker ID sherpa-onnx

1. Verifica API: `python3 -c "import sherpa_onnx as s; help(s.SpeakerEmbeddingExtractorConfig); help(s.SpeakerEmbeddingExtractor)" | head -80`.
2. Nuovo `src/daemon/services/speaker_id/sherpa_backend.py`, classe `SherpaSpeakerBackend` che implementa **tutto** il protocollo `SpeakerEmbeddingBackend` (`services/speaker_id/backend.py`: `name`, `sample_rate`, `min_samples`, `is_available`, `load`, `unload`, `preprocess`, `embed`):
   - `name = f"sherpa-onnx:{model_file}"` (così i profili registrati con un altro modello vengono segnalati da registrare di nuovo: `profiles.py` riga ~125 confronta già `meta["backend"]`);
   - `embed(pcm)`: `stream = extractor.create_stream()`, `stream.accept_waveform(16000, pcm)`, `stream.input_finished()`, `extractor.is_ready(stream)` falso → `None`; altrimenti `np.array(extractor.compute(stream))` normalizzato L2;
   - `min_samples`: 1,0 s (16000); `preprocess`: restituisce il PCM invariato.
3. `create_backend` (`backend.py`): `if name.startswith("sherpa-onnx"): return SherpaSpeakerBackend(model_file=...)`. Il modello arriva da `effective_engine("speaker")`.
4. La soglia predefinita (0,75) è stata calibrata su resemblyzer: con sherpa **non** è valida. Aggiungi la chiave `speaker-id-threshold-sherpa` (tipo `d`, default `0.5` provvisorio) e fai calibrare la soglia all'utente in §10 (V8 in §11).
5. Test con `SpeakerEmbeddingExtractor` finto: `embed` restituisce un vettore di norma 1; audio più corto di `min_samples` → `None`; `name` contiene il nome del file del modello.

### 9.8 Dipendenze installate solo quando servono

**Passo 9.8.a — `data/dependencies/python_deps.json`**: ogni voce riceve due campi nuovi:
- `"engines": ["<motore>", ...]` — i motori che richiedono il pacchetto (es. `vosk` → `["vosk"]`; `sherpa-onnx` → `["sherpa-onnx"]`; `onnxruntime` → `["semantic-router"]`; `faster-whisper` → `["whisper"]`; `llama-cpp-python` → `["llama-cpp"]`; `piper-tts` → `["piper"]`; `openwakeword` → `["openwakeword"]`; `resemblyzer` → `["resemblyzer"]`);
- `"pip_args": [...]` — argomenti extra (vuoto se non servono).

Aggiungi la voce `torch` (`import_name` `torch`, `package_name` `torch`, `engines` `["resemblyzer"]`, `pip_args` `["--index-url", "https://download.pytorch.org/whl/cpu"]`) **prima** di `resemblyzer`: così pip installa torch solo CPU e non scarica le librerie CUDA (oggi ≈4,4 GB). Vedi V5 in §11. Aggiungi anche `tokenizers` con `engines` `["semantic-router"]` se `semantic_router.py` lo importa (`grep -n "import" src/daemon/skills/semantic_router.py`).

**Passo 9.8.b — `dependency_installer.py`:**
- `_install_pip_packages` installa **un pacchetto alla volta**, nell'ordine del file, con `[pip, "install", "--prefer-binary", *pip_args, package_name]`;
- nuova funzione `ensure_engine_installed(parent, engine: str, on_ready: Callable[[], None], on_cancel: Callable[[], None])`: calcola i pacchetti mancanti per quel motore (import di prova col Python del venv in un sottoprocesso); se non manca nulla chiama subito `on_ready`; altrimenti mostra il dialogo di consenso **con l'elenco dei pacchetti e la dimensione indicativa**, installa, e chiama `on_ready` solo se tutto è riuscito, altrimenti `on_cancel`.

**Passo 9.8.c — Uso nella GUI.** Nei radio dei motori (`wakeword.py`, `stt.py`, `tts.py`, `speaker_id.py`) e nel selettore dei modelli: al clic su un motore **non** scrivere subito la chiave GSettings. Chiama `ensure_engine_installed(...)`: `on_ready` scrive la chiave; `on_cancel` riporta il radio al valore precedente (senza scrivere la chiave). Rimuovi il `bind_radio_group` diretto dove impedisce questo comportamento.

**Passo 9.8.d — Primo avvio (§7.5, passo 1):** i pacchetti proposti sono quelli con `is_critical: true` **più** quelli dei motori `default` della lingua del desktop (`engine_defaults.json`) **più** `semantic-router`. Per l'italiano quindi anche `vosk` e `sherpa-onnx`.

**Test:** con `subprocess.run` finto: (1) il motore già installato chiama `on_ready` senza dialogo; (2) l'annullamento chiama `on_cancel` e non scrive GSettings; (3) `torch` è installato con `--index-url https://download.pytorch.org/whl/cpu` e prima di `resemblyzer`.

### 9.9 GPU per l'LLM locale (Intel, AMD, NVIDIA)

**Fatti verificati (README ufficiale di llama-cpp-python):**
| Variante | Indice delle wheel precompilate |
|---|---|
| CPU | `https://abetlen.github.io/llama-cpp-python/whl/cpu` |
| Vulkan (Intel, AMD, NVIDIA) | `https://abetlen.github.io/llama-cpp-python/whl/vulkan` |
| CUDA (NVIDIA) | `https://abetlen.github.io/llama-cpp-python/whl/<cu118\|cu121\|cu122\|cu123\|cu124\|cu125\|cu130\|cu132>` |
| ROCm (AMD) | `https://abetlen.github.io/llama-cpp-python/whl/rocm72` |

Il README dice che le wheel CUDA esistono solo per **Python 3.10–3.12**; la macchina di sviluppo ha Python 3.14. Per le altre varianti la compatibilità va controllata (V4).

**Passo 9.9.a — Rilevamento** `src/daemon/core/gpu_detect.py`, funzione `detect_gpus() -> list[dict]`, **solo lettura di file, nessun processo esterno obbligatorio**:
- per ogni `/sys/class/drm/card[0-9]*/device/vendor`: `0x10de` → `nvidia`, `0x1002` → `amd`, `0x8086` → `intel`;
- `vulkan: bool` = `ctypes.util.find_library("vulkan") is not None`;
- per NVIDIA: `driver` = prima riga di `/proc/driver/nvidia/version` se esiste;
- nessuna GPU trovata → lista vuota.
Test con una finta cartella `/sys` (passa la radice come parametro, default `/`).

**Passo 9.9.b — Scelta della variante** (funzione pura `choose_llama_variant(gpus, python_version) -> str`, testata):
1. nessuna GPU o nessun loader Vulkan e nessuna NVIDIA → `cpu`;
2. c'è una GPU e il loader Vulkan → `vulkan` (funziona con tutti e tre i produttori);
3. solo NVIDIA senza Vulkan → `cu124`;
4. il risultato deve comparire nell'indice per la versione di Python (V4); se non c'è una wheel compatibile → `cpu` e la GUI lo spiega.

**Passo 9.9.c — Impostazioni:** nuove chiavi `llm-gpu-backend` (tipo `s`, valori `auto` | `cpu` | `vulkan` | `cuda` | `rocm`, default `auto`) e `llm-gpu-layers` (tipo `i`, default `-1` = tutti i layer quando la GPU è attiva). In `gui/components/settings/llm.py`, nel gruppo del modello locale: `Adw.ComboRow` "Acceleration" con le voci `Automatic`, `CPU only`, `Vulkan (Intel, AMD, NVIDIA)`, `CUDA (NVIDIA)`, `ROCm (AMD)`; le voci non compatibili con `detect_gpus()` non sono sensibili.

**Passo 9.9.d — Installazione:** nel dialogo di consenso per `llama-cpp` (9.8.b) usa `pip_args = ["--extra-index-url", "<indice della variante>"]`. Salva la variante installata in `${XDG_DATA_HOME}/voice-assistant/llama_backend.json` (`{"variant": "vulkan", "version": "<llama_cpp.__version__>"}`). Cambiare "Acceleration" a una variante diversa da quella installata → nuovo consenso e reinstallazione con `--force-reinstall --no-deps`.

**Passo 9.9.e — Caricamento** (`services/llm_service.py`, `Llama(...)` alla riga ~394; dopo la Fase 4 nel `llm_worker`):
```python
n_gpu_layers = 0 if backend == "cpu" else settings_gpu_layers
llm = Llama(model_path=model_path, n_ctx=2048, n_threads=max(1, (os.cpu_count() or 4) - 1),
            n_gpu_layers=n_gpu_layers, verbose=False)
```
Se il caricamento con GPU solleva un'eccezione **o il worker termina in modo anomalo** durante il caricamento: riprova **una volta** con `n_gpu_layers=0`, notifica `_("GPU acceleration failed: the local model is running on the CPU.")` e registra l'errore (§6.4). Verifica il nome del parametro `n_gpu_layers` sulla versione installata (R6).

### 9.10 Criteri di accettazione della Fase 5
- [ ] Nuovi test verdi (9.1.c, 9.3, 9.5, 9.6, 9.7, 9.8, 9.9.a–b).
- [ ] Account pulito con desktop in italiano (prova di §7.7): il primo avvio installa `vosk` e `sherpa-onnx`, **non** installa `torch`, `faster-whisper`, `llama-cpp-python`, `piper-tts`; wake word "assistente" con Vosk, trascrizione con FastConformer, risposta vocale con la voce sherpa `paola`.
- [ ] Con lingua italiana il radio sherpa della wake word non è selezionabile.
- [ ] Scegliere Whisper nella GUI senza `faster-whisper` → dialogo di consenso; "Annulla" → resta il motore precedente; "Installa" → Whisper attivo solo a installazione finita.
- [ ] Attivare resemblyzer → nel venv `pip list | grep -i nvidia` è vuoto.
- [ ] Macchina con GPU: "Acceleration: Automatic" usa la GPU (log del `llm_worker` con la variante); forzando un errore (es. `llm-gpu-layers` enorme con un modello più grande della VRAM) il modello riparte su CPU con una notifica.

---

## 10. Fase 6 — Latenza del gate Speaker ID

**Decisioni dell'utente (non cambiarle):**
1. verifica del parlante **in streaming**, mentre l'utente parla (con una validazione dell'accuratezza);
2. in modalità `gate`, se la voce non viene riconosciuta (**anche per timeout**) l'assistente risponde semplicemente che non può eseguire l'azione perché non ha riconosciuto l'utente;
3. timeout **calibrato** sulla velocità della macchina, non fisso;
4. verifica **in parallelo** alla trascrizione, senza bloccare le richieste scritte.

### 10.1 Com'è oggi (verificato)
- `_process_text` (`assistant_runtime.py`, riga ~1204; dopo la Fase 4 in `core/request_queue.py`) gira sull'**unico** thread della coda delle richieste e chiama `speaker_session.finalize(timeout_s=1.5)` **dopo** la trascrizione.
- `finalize` (`services/speaker_id/session.py`, riga ~312) calcola l'embedding dell'**intera** frase in quel momento: più la frase è lunga, più tempo serve.
- Il lavoro finale finisce in coda **dietro** alle finestre di sovrapposizione ancora in attesa (una sola coda FIFO in `SpeakerIdentifier`, `identifier.py` riga ~128).
- Timeout fisso di 1,5 s: in `gate` timeout = rifiuto, quindi su una CPU lenta l'utente legittimo viene rifiutato.
- `finalize` aspetta anche fino a **8 s** il warm-up (riga ~323).
- `update_history_if_eligible` → `add_history` → `_save_atomic` scrive su disco **prima** di restituire il verdetto.
- Nel frattempo la coda è ferma: anche una richiesta scritta dalla GUI aspetta.

### 10.2 Embedding in streaming

**Passo 10.2.a — Interfaccia facoltativa** in `services/speaker_id/backend.py`:
```python
@runtime_checkable
class StreamingEmbedder(Protocol):
    def start(self) -> None: ...
    def feed(self, pcm: np.ndarray) -> None:
        """16 kHz float32 audio, any chunk size. Must be cheap: heavy work goes to a background thread."""
    def finish(self) -> Optional[np.ndarray]:
        """L2-normalized embedding of everything fed since start(), or None if too little speech."""


class SpeakerEmbeddingBackend(Protocol):
    ...
    def streaming_embedder(self) -> StreamingEmbedder: ...
```
- **sherpa-onnx** (`SherpaSpeakerBackend`, §9.7): `start` crea lo stream, `feed` fa `stream.accept_waveform(16000, pcm)`, `finish` fa `input_finished()` e `compute(stream)` (il calcolo avviene sui dati accumulati: il costo va misurato, V8).
- **resemblyzer**: `VoiceEncoder.embed_utterance` è la media normalizzata degli embedding di finestre da 1,6 s. `feed` accumula l'audio; ogni volta che c'è una finestra completa di 1,6 s (passo 0,8 s) la calcola in un thread proprio e aggiunge il vettore a una lista; `finish` calcola l'ultima finestra parziale se ≥ 0,8 s, poi restituisce la media normalizzata L2. Prima di scrivere, leggi `embed_utterance` nel pacchetto installato (`python3 -c "import resemblyzer, inspect; print(inspect.getsource(resemblyzer.VoiceEncoder.embed_utterance))"`) e **usa gli stessi parametri** (`rate`, `min_coverage`) che trovi lì.

**Passo 10.2.b — `VoiceSpeakerSession`:** in `start()` crea `self._embedder = identifier.backend.streaming_embedder()` e chiama `start()`; in `feed()` passa ogni blocco (pre-roll compreso) a `self._embedder.feed(...)`. Il controllo delle sovrapposizioni (finestre, `_check_overlap_incremental`) **resta com'è**.

**Passo 10.2.c — Validazione dell'accuratezza** (script manuale, non un test: servono la voce reale e il modello). `experiments/speaker_streaming_validation.py`:
- registra 40 frasi dell'utente registrato (da 1 a 6 s) e 20 frasi di un'altra persona, salvandole in `experiments/speaker_streaming_validation/data/` (cartella **ignorata da git**: aggiungila a `.gitignore`);
- per ogni frase calcola l'embedding "intero" (`backend.embed`) e quello "streaming" (blocchi da 20 ms in `feed`, poi `finish`);
- stampa per ogni backend: coseno medio e minimo tra i due embedding; percentuale di frasi con lo **stesso verdetto** (riconosciuto / non riconosciuto) contro il profilo; tempo di `finish`.
- Criteri proposti (§11.2): coseno minimo ≥ 0,97 e stesso verdetto su ≥ 95 % delle frasi. **Se non sono rispettati, lo streaming non si attiva per quel backend** (resta il calcolo intero) e lo segnali nella review.

### 10.3 Verifica in parallelo, coda mai bloccata

**Passo 10.3.a — Il verdetto parte a fine parlato.** Nel punto in cui `ListeningSession` stabilisce che la frase è finita e chiama `enqueue_request(text, is_voice=True, ..., speaker_session=...)` (oggi `assistant_runtime.py` righe ~1080–1150): **prima** di trascrivere, chiama `verdict_future = speaker_session.request_verdict()`. Nuovo metodo di `VoiceSpeakerSession`:
```python
def request_verdict(self) -> "concurrent.futures.Future[SpeakerVerdict]":
    """Start computing the final verdict now; the future completes on the speaker worker thread."""
```
Usa `concurrent.futures.Future` e completala nel callback del worker (lo stesso meccanismo di `_on_final` di oggi). Il verdetto finale usa `self._embedder.finish()` al posto di un nuovo `embed` sull'audio intero.

**Passo 10.3.b — Il lavoro finale passa davanti alle finestre.** In `SpeakerIdentifier.submit` (`identifier.py`, riga ~128) sostituisci `queue.Queue` con `queue.PriorityQueue`: `put((0 if is_final else 1, seq, item))` con `seq = next(self._seq)` (`itertools.count()`), così il lavoro finale non aspetta le finestre di sovrapposizione accodate. Adatta `_worker_loop` e `stop()` (l'elemento di stop diventa `(-1, next(self._seq), None)`).

**Passo 10.3.c — La coda riceve solo richieste pronte.** Una richiesta vocale con `verdict_future` **non** entra subito nella coda: un thread di attesa (`threading.Thread(name="SpeakerVerdictWait", daemon=True)`) aspetta `verdict_future.result(timeout=budget)` (§10.4), poi accoda la richiesta con il verdetto (o con `SpeakerVerdict(status="unavailable", timed_out=True)` se scade). `_process_text` **non chiama più** `finalize` né aspetta nulla: legge `request.verdict`. Le richieste scritte vanno in coda subito e non aspettano mai.

Conseguenza accettata: una richiesta scritta inviata mentre si aspetta un verdetto vocale può essere eseguita prima di quella vocale.

**Test** `tests/test_speaker_parallel.py` (backend finto con `embed` che dorme 0,5 s):
- durante l'attesa del verdetto, una richiesta scritta accodata viene elaborata **prima** che il verdetto sia pronto;
- con 3 finestre di sovrapposizione in coda, il lavoro finale viene eseguito per primo;
- verdetto in ritardo oltre il budget → la richiesta arriva a `_process_text` con `timed_out=True`.

### 10.4 Timeout calibrato

**Passo 10.4.a — Misura al warm-up.** In `SpeakerIdController.warm_up` (`core/speaker_runtime.py`, riga ~83), dopo il caricamento: calcola 3 volte l'embedding di 3 s di rumore leggero (`np.random.randn(48000) * 0.001`), prendi la **mediana** del tempo e salva `self.seconds_per_audio_second = mediana / 3.0`.

**Passo 10.4.b — Budget per ogni frase:**
```python
def verdict_budget_s(self, utterance_seconds: float) -> float:
    """Time allowed for the final verdict, scaled to this machine's measured speed."""
    per_second = self.seconds_per_audio_second or 0.25   # before warm-up: conservative guess
    estimate = per_second * utterance_seconds
    return min(6.0, max(1.0, 2.0 * estimate + 0.3))
```
Con lo streaming di §10.2 attivo, `utterance_seconds` è la durata dell'**ultima finestra** ancora da calcolare (al massimo 1,6 s), non dell'intera frase.

**Passo 10.4.c — Niente più attesa separata di 8 s.** Elimina l'attesa di `ready_event` con timeout 8,0 in `finalize` (riga ~323). Se il modello non è pronto quando arriva il momento del verdetto, il verdetto è `unavailable` (e in `gate` si applica §10.5). Il warm-up parte già all'avvio del daemon quando la modalità non è `disabled` (oggi in `SpeakerIdController.__init__`, riga ~76) e il modello **non** viene scaricato dalla memoria in modalità `gate` (decisione già presente in `fix-plan-multichat-speaker-id.md` B2: resta).

**Test:** `verdict_budget_s` con `seconds_per_audio_second` 0,05 / 0,5 / 5 e frasi da 1 e 10 s → valori attesi calcolati a mano con la formula, sempre tra 1,0 e 6,0.

### 10.5 Messaggio unico di rifiuto

In `gate`, **ogni** rifiuto di una richiesta vocale (`unknown`, `overlap`, `insufficient_audio`, `timeout`, `unavailable`, `no_profiles`) usa lo **stesso** messaggio pronunciato. In `data/locales/responses.json`, sezione `speaker_id`:
- aggiungi `"not_recognized": "Non posso eseguire questa azione perché non ho riconosciuto la tua voce."` in `it`;
- aggiungi `"not_recognized": "I can't do that because I didn't recognize your voice."` in `en`;
- nel codice del rifiuto (`assistant_runtime.py` righe ~1216–1222; dopo la Fase 4 in `request_queue.py`) usa **sempre** la chiave `not_recognized`; le altre chiavi di `speaker_id` che non servono più si eliminano (tieni `context_template`);
- il motivo tecnico resta nel log (solo il codice del motivo, §6.3) e nel segnale D-Bus `SpeakerRejected(reason, message)`, così la GUI può spiegare cosa è successo;
- le parole di stop durante il parlato restano ammesse anche senza riconoscimento (decisione in `fix-plan-multichat-speaker-id.md` A2: resta).

### 10.6 Aggiornamento del profilo fuori dal percorso del verdetto

`update_history_if_eligible` (chiamato in `finalize`, riga ~372) non deve ritardare la risposta: il verdetto si restituisce subito e l'aggiornamento della history (con la scrittura su disco `_save_atomic`) va sottomesso al worker con **priorità bassa** (`2` nella `PriorityQueue` di §10.3.b), come un lavoro senza callback.

Test: con `store.add_history` finto che dorme 1 s, la future del verdetto è completata **prima** che `add_history` termini.

### 10.7 Differenze con i piani precedenti di Gemini

Questo documento prevale (§0.1). Differenze esplicite:
| Argomento | `speaker-identification-plan.md` / `fix-plan-multichat-speaker-id.md` | Ora |
|---|---|---|
| Motore predefinito | D1: resemblyzer | sherpa-onnx CAM++ (§9.7); resemblyzer opzionale, torch solo CPU (§9.8.a) |
| Timeout del verdetto | `finalize(timeout_s=1.5)` fisso (§4.4) | budget calibrato (§10.4) |
| Attesa del warm-up | fino a 8 s (fix-plan B2, punto 3) | nessuna attesa separata (§10.4.c); il resto di B2 (warm-up all'avvio, niente scarico in `gate`) resta |
| Calcolo dell'embedding finale | frase intera a fine trascrizione | in streaming durante il parlato (§10.2), avviato a fine parlato (§10.3.a) |
| Messaggi di rifiuto | uno per motivo | uno solo, `not_recognized` (§10.5) |
| Aggiornamento della history | dentro `finalize`, prima del verdetto | in background dopo il verdetto (§10.6) |
| Soglia | 0,75 | 0,75 per resemblyzer; soglia separata per sherpa (§9.7 punto 4) |
| Richieste scritte durante la verifica | aspettano nella coda | non aspettano (§10.3.c) |

Tutto il resto dei due piani (modalità `disabled`/`informative`/`gate`, fail-closed, stop ammessi, controllo delle sovrapposizioni, registrazione della voce) **resta valido**.

### 10.8 Criteri di accettazione della Fase 6
- [ ] Test di §10.3, §10.4, §10.6 verdi.
- [ ] Script di §10.2.c eseguito **dall'utente** con la sua voce; risultati riportati nella review; streaming attivo solo per i backend che rispettano i criteri.
- [ ] Prova manuale in `gate`: frase lunga (≈6 s) dell'utente registrato → eseguita; la stessa frase detta da un'altra persona → rifiutata con il messaggio `not_recognized`; con la CPU occupata (`stress-ng --cpu $(nproc) --timeout 60s` in un altro terminale) l'utente registrato **non** viene rifiutato per timeout.
- [ ] Durante la verifica di una frase vocale, un messaggio scritto nella GUI riceve risposta senza aspettare.

---

## 11. Punti aperti

### 11.1 Verifiche da fare prima di chiudere la fase indicata

Se una verifica dà un esito diverso da quello atteso, **fermati e segnalalo nella review**: non scegliere un'alternativa da solo.

| # | Fase | Cosa verificare | Come | Se l'esito è negativo |
|---|---|---|---|---|
| V1 | 5 | Licenza del modello CAM++ da usare per lo Speaker ID: `3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx` (3D-Speaker) oppure `wespeaker_en_voxceleb_CAM++.onnx` (WeSpeaker, modelli VoxCeleb sotto CC BY 4.0) | pagina del modello su ModelScope/3D-Speaker e README di WeSpeaker; riporta il testo della licenza | se nessuno dei due permette la redistribuzione del download all'utente, si decide con l'utente |
| V2 | 5 | Per il FastConformer multilingua, quale decoder (transducer o CTC) corrisponde al WER italiano pubblicato (MCV12 5,60 %) | model card NVIDIA `stt_multilingual_fastconformer_hybrid_large_pc` (o nome equivalente) | se il valore è del CTC, cambia il nome in `engine_defaults.json` con la variante `ctc-...-int8` (esiste nella release) |
| V3 | 4 | Latenza della comunicazione tra processi | prototipo di §8.3.a | fermarsi (§8.3.a) |
| V4 | 5 | Esistono wheel precompilate di llama-cpp-python (cpu, vulkan, cuda, rocm) per la versione di Python del venv? | apri `https://abetlen.github.io/llama-cpp-python/whl/<variante>/llama-cpp-python/` e cerca `cp3XX` con la versione di `python3 --version` | offrire solo le varianti disponibili; se nessuna, la GUI propone la compilazione con `CMAKE_ARGS` e i pacchetti di sistema necessari, previo consenso |
| V5 | 5 | Esistono wheel CPU di torch per la versione di Python del venv? | `pip download torch --index-url https://download.pytorch.org/whl/cpu --no-deps -d /tmp/x` con il Python del venv | resemblyzer resta installabile solo con torch standard: segnalalo nella review |
| V6 | 2 | Il link `issues/new?template=bug_report.yml&title=...&description=...` precompila titolo e campo "Description" | apri a mano l'URL generato dal test di §6.4 in un browser con login GitHub | usare `body=` e un template Markdown invece del modulo YAML |
| V7 | 2 | API di `huggingface_hub` per leggere l'hash sha256 LFS di un file | `python3 -c "import huggingface_hub as h; help(h.HfApi.get_paths_info)"` sulla versione installata (cerca `expand` e `lfs`) | niente hash atteso per i file Hugging Face: solo manifest (§6.6.b) |
| V8 | 5–6 | Soglia e tempi dello Speaker ID con sherpa-onnx | script di §10.2.c esteso: distribuzione dei punteggi utente/altre persone; soglia = punto che separa meglio le due distribuzioni | resta la soglia provvisoria 0,5 e lo si segnala |

### 11.2 Scelte tecniche proposte da Claude, non discusse con l'utente

Implementale **come scritte**. Sono elencate perché l'utente possa cambiarle in review; non sono decisioni sue.
- Firma degli errori = componente + tipo di eccezione + ultimi 3 frame senza numeri di riga; massimo 50 report (§6.4.a).
- Riavvii: attese 1, 2, 4, 8, 16 s; azzeramento dopo 60 s di funzionamento; nuovo stato `error` (§6.5).
- Variabile d'ambiente `VOICE_ASSISTANT_LOG_USER_TEXT=1` per la modalità di debug dei log (§6.3.a).
- Controllo dei modelli: dimensioni a ogni caricamento, hash completo solo dopo un errore (§6.6).
- Tabella delle transizioni di stato (§8.1.b).
- Formato dei frame tra processi (§8.3.b) e limite di 16 MiB; soglie del prototipo (5 ms / 20 ms).
- Router semantico in un worker separato (`nlu_worker`, §8.3.e).
- Una richiesta Fast-Path il cui gestore fallisce prosegue verso lo Smart-Path (§5.1); le skill senza `tool` né tipo di azione non vengono più passate all'LLM dentro il Fast-Path ma allo Smart-Path (§5.4.c).
- Impostazioni vuote = predefinito della lingua (§9.2).
- Scelta automatica della variante GPU: Vulkan per primo (§9.9.b).
- Criteri di validazione dello streaming (coseno ≥ 0,97, verdetto uguale ≥ 95 %) e formula del budget (§10.2.c, §10.4.b).
- Coda con priorità per il lavoro dello Speaker ID (§10.3.b).

---

## 12. Checklist per la review (cosa controllerà Claude)

**Ogni fase:**
- [ ] I commit sono piccoli, in inglese, formato `tipo(ambito): descrizione`, e non mescolano fasi (R2).
- [ ] Nella richiesta di review ci sono: elenco dei commit, output dei comandi "Verifica", esito dei criteri di accettazione, numeri richiesti (conteggi, p95, RSS).
- [ ] Nessun test usa rete, modelli reali o `torch` (R5); i test girano con `dbus-run-session` e `MemoryMax`.
- [ ] Nessuna API inventata: per ogni libreria esterna nuova c'è l'output del comando di verifica (R6).
- [ ] Codice, commenti e log in inglese; testi per l'utente dentro `_()` (R4).
- [ ] Nessun `except Exception` silenzioso nuovo; nessun log con testo dell'utente; nessuna chiamata GLib diretta.
- [ ] Nessun motore rimosso (R3).

**Fase 0:** ciclo di `test_gui` corretto con `patch.object`; finestre distrutte; `drain_main_loop` ovunque; `conftest.py` isola `HOME`/`XDG_*` e schema; `tests/meson.build` senza test; CI verde.

**Fase 1:** nessun riconoscimento tramite regex prima del router; slot solo a valle; `set_volume` senza numero non usa 50; nessuna traccia di `VectorIntentMatcher`, `_INTENT_TOOL_MAP`, `system_control`, `brightness`, streaming pipeline, Bugzilla; UI Fast-Path con `Adw.ExpanderRow`; download del router in background con notifica.

**Fase 2:** `LEGACY` del test GLib vuoto; watchdog con un solo timer; report deduplicati e una notifica per firma; nessun dato inviato senza clic; riavvii limitati; checksum su ogni download elencato; `.po` completo.

**Fase 3:** zip senza venv/binari/test; nessun UUID nel codice; daemon avviabile da zip su account pulito; `extension.js` senza Gtk/Gdk/systemctl/scrittura di file; `disable()` ferma il daemon; tabella `enable`/`disable` completa; primo avvio con consenso.

**Fase 4:** una sola `StateMachine`, transizioni non ammesse rifiutate; nessun `self.owner`; `VoiceAssistant` solo D-Bus; prototipo IPC entro le soglie; worker che si riavviano e terminano con l'estensione; pyright e ruff bloccanti.

**Fase 5:** `engine_defaults.json` completo e coerente; sherpa non selezionabile dove non supportato; consenso prima di applicare un motore; torch solo CPU; niente GPU fuori dall'LLM; ripiego su CPU funzionante; V1, V2, V4, V5 risolti.

**Fase 6:** verdetto avviato a fine parlato; coda mai bloccata; budget calibrato; messaggio unico; history in background; risultati della validazione con la voce dell'utente.
