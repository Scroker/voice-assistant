# Guida ai Provider STT

> Come funzionano, come aggiungerne di nuovi, e le differenze operative tra Vosk e Whisper.

---

## Interfaccia Astratta (`providers/base.py`)

Ogni provider deve estendere `STTProvider`:

```python
import abc

class STTProvider(abc.ABC):
    @abc.abstractmethod
    def __init__(self, model: str, hardware: str, extra: dict):
        """Inizializza il provider, scaricando il modello se necessario."""
        pass

    @abc.abstractmethod
    def process_chunk(self, data: bytes) -> tuple[str, str]:
        """
        Processa un chunk di audio PCM int16 mono a 16 kHz.
        
        Returns:
            tuple: (text, partial_text)
            - text: stringa non vuota se una frase è stata completata
            - partial_text: testo parziale mentre l'utente sta parlando
        """
        pass

    @abc.abstractmethod
    def flush_and_transcribe(self) -> str:
        """Forza la trascrizione dell'audio accumulato (per provider batch)."""
        return ""

    @abc.abstractmethod
    def reset(self):
        """Resetta lo stato interno del riconoscitore."""
        pass
```

### Factory (`providers/__init__.py`)

```python
def get_provider(provider_name, model, hardware, extra, progress_callback=None) -> STTProvider
```

Il parametro `progress_callback` è una funzione `(percent: int) -> None` che il provider chiama durante il download del modello.

---

## Provider: Vosk

| Proprietà | Valore |
|---|---|
| **File** | `providers/vosk_provider.py` |
| **Dipendenza** | `vosk` (pip) |
| **Modalità** | Streaming reale |
| **Download** | Automatico da `alphacephei.com/vosk/models/` |
| **Resume** | Sì (HTTP Range headers, fino a 10 retry) |
| **Hardware** | Solo CPU |

### Funzionamento

Vosk processa ogni chunk audio con `KaldiRecognizer.AcceptWaveform()`:
- Se ritorna `True`: è disponibile un risultato finale (`Result()`)
- Se ritorna `False`: è disponibile un risultato parziale (`PartialResult()`)

Il riconoscimento è **in tempo reale**: il testo appare progressivamente mentre l'utente parla.

### Alias dei Modelli

`VoskProvider.MODEL_MAPPINGS` traduce alias brevi nei nomi ufficiali:

```python
{
    "it": "vosk-model-small-it-0.22",
    "en": "vosk-model-small-en-us-0.15",
    "small-it": "vosk-model-small-it-0.22",
    "large-it": "vosk-model-it-0.22",
    "small-en": "vosk-model-small-en-us-0.15",
    "large-en": "vosk-model-en-us-0.22",
}
```

### Recovery da Corruzione

Se un modello esiste ma non è valido (`Model()` lancia un'eccezione), la cartella viene rimossa automaticamente e il download viene rieseguito.

---

## Provider: Whisper

| Proprietà | Valore |
|---|---|
| **File** | `providers/whisper_provider.py` |
| **Dipendenza** | `faster-whisper` (pip) + opzionalmente PyTorch/CUDA |
| **Modalità** | Batch (accumula poi trascrive) |
| **Download** | Automatico via HuggingFace Hub (Systran/faster-whisper-*) |
| **Hardware** | CPU (`int8`) o CUDA (`float16`) |

### Funzionamento

A differenza di Vosk, Whisper **non supporta streaming**. I chunk audio vengono accumulati in un `bytearray`. La trascrizione avviene solo quando `flush_and_transcribe()` viene invocato, tipicamente dopo 2 secondi di silenzio (rilevato nel `_audio_loop()` di `main.py` con soglia RMS > 500).

```
Audio chunks → bytearray → flush_and_transcribe() → float32 normalizzato → model.transcribe()
```

### Taglie dei Modelli

| Taglia | Dimensione approssimativa | VRAM (CUDA) |
|---|---|---|
| `tiny` | ~75 MB | ~1 GB |
| `base` | ~140 MB | ~1 GB |
| `small` | ~466 MB | ~2 GB |
| `medium` | ~1.5 GB | ~5 GB |
| `large-v3` | ~3.1 GB | ~10 GB |

### Tracking del Progresso di Download

Whisper usa `tqdm` per le progress bar. Il provider intercetta queste barre tramite:

1. **`GlobalTqdmRedirector`**: un wrapper su `sys.stderr` che cattura l'output di tqdm e ne estrae la dimensione scaricata tramite regex
2. **Monkey-patch di `tqdm`**: forza l'uso del redirector globale e disabilita il disable automatico
3. **Variabili globali su `sys`**: `sys._va_progress_cb`, `sys._va_model_size`, `sys._va_dl_state` — necessarie perché i thread worker di HuggingFace non ereditano `contextvars`

### Migrazione Vecchi Modelli

All'avvio, il provider cerca automaticamente vecchie cartelle HuggingFace (formato `models--Systran--faster-whisper-<size>/snapshots/<hash>/`) e le migra nella struttura pulita `whisper-<size>/`.

---

## Aggiungere un Nuovo Provider

1. **Creare il file** `providers/nuovo_provider.py`:

```python
from .base import STTProvider

class NuovoProvider(STTProvider):
    def __init__(self, model: str, hardware: str, extra: dict, progress_callback=None):
        # Scaricare/caricare il modello
        pass

    def process_chunk(self, data: bytes) -> tuple[str, str]:
        # Processare il chunk audio
        return "", ""

    def flush_and_transcribe(self) -> str:
        return ""

    def reset(self):
        pass
```

2. **Registrare nella factory** (`providers/__init__.py`):

```python
from .nuovo_provider import NuovoProvider

def get_provider(provider_name, model, hardware, extra, progress_callback=None):
    ...
    elif provider_name == "nuovo":
        return NuovoProvider(model, hardware, extra, progress_callback)
```

3. **Aggiungere la UI** in `prefs.js`:
   - Aggiungere l'opzione nella `providerList` / `providerIds`
   - Creare un nuovo `Adw.PreferencesGroup` con le opzioni specifiche
   - Gestire la visibilità nella funzione `onProviderChanged()`

4. **Aggiornare il GSchema**: se servono nuove chiavi, aggiungerle a `org.gnome.shell.extensions.voice-assistant.gschema.xml` e aggiungere un handler in `main.py:on_settings_changed()`.

---

## Formato Audio

Tutti i provider ricevono audio nel formato:

| Proprietà | Valore |
|---|---|
| **Sample rate** | 16000 Hz |
| **Canali** | 1 (mono) |
| **Formato** | PCM int16 (little-endian) |
| **Block size** | 8000 frames (0.5 secondi) |
