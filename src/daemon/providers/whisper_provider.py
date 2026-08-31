import os
import numpy as np
from .base import STTProvider

class WhisperProvider(STTProvider):
    MODELS_DIR = os.path.expanduser("~/.local/share/voice-assistant/models")
    
    def __init__(self, model_size: str, hardware: str, extra: dict, progress_callback=None, models_dir: str = None):
        if models_dir and len(models_dir.strip()) > 0:
            self.MODELS_DIR = os.path.expanduser(models_dir)
        else:
            self.MODELS_DIR = os.path.expanduser("~/.local/share/voice-assistant/models")
        print(f"Inizializzazione WhisperProvider (Modello: {model_size}, HW: {hardware}, dir: {self.MODELS_DIR})...")
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            print("ERRORE CRITICO: pacchetto faster-whisper non installato.")
            self.model = None
            raise RuntimeError("Pacchetto 'faster-whisper' non installato.")

        device = "cuda" if hardware == "cuda" else "cpu"
        compute_type = "float16" if device == "cuda" else "int8"
        
        import sys
        import re
        import threading
        
        if not hasattr(sys, '_va_downloads'):
            sys._va_downloads = {}

        model_folder_name = f"whisper-{model_size}" if not model_size.startswith("whisper-") else model_size
        target_dir = os.path.join(self.MODELS_DIR, model_folder_name)

        thread_id = threading.get_ident()
        if progress_callback:
            sys._va_downloads[thread_id] = {
                'model_size': model_size,
                'cb': progress_callback,
                'target_dir': target_dir,
                'state': {'pct': -1}
            }

        class GlobalTqdmRedirector:
            def __init__(self, original_stderr):
                self.original_stderr = original_stderr
                self.pattern_size = re.compile(r'([\d.]+)\s*([kMGT]?B)(?!/s)', re.IGNORECASE)

            def write(self, buf):
                self.original_stderr.write(buf.replace('\r', '\n'))
                downloads = getattr(sys, '_va_downloads', {})
                if not downloads:
                    return

                current_tid = threading.get_ident()
                matching_infos = []
                if current_tid in downloads:
                    matching_infos.append(downloads[current_tid])
                elif len(downloads) == 1:
                    matching_infos.append(next(iter(downloads.values())))
                else:
                    matching_infos = list(downloads.values())

                sizes = {
                    "tiny": 75, "tiny.en": 75,
                    "base": 140, "base.en": 140,
                    "small": 466, "small.en": 466,
                    "medium": 1500, "medium.en": 1500,
                    "large-v1": 3100, "large-v2": 3100,
                    "large-v3": 3100, "large": 3100
                }

                for info in matching_infos:
                    ms = info.get('model_size', '')
                    cb = info.get('cb')
                    tdir = info.get('target_dir')
                    state = info.get('state')
                    if not (ms and cb and state):
                        continue

                    clean_ms = ms.replace("whisper-", "").strip()
                    total_mb = sizes.get(clean_ms, sizes.get(ms, 140))
                    percent = None

                    # 1. Calcola la percentuale dai MB realmente trasferiti (es. "28.1MB", ignorando "47.2kB/s")
                    match_size = self.pattern_size.search(buf)
                    if match_size:
                        val = float(match_size.group(1))
                        unit = match_size.group(2).upper()
                        
                        if unit == "B": dl_mb = val / (1024 * 1024)
                        elif unit == "KB": dl_mb = val / 1024
                        elif unit == "MB": dl_mb = val
                        elif unit == "GB": dl_mb = val * 1024
                        else: dl_mb = val
                        
                        percent = min(99, max(0, int((dl_mb / total_mb) * 100)))

                    # 2. Fallback: dimensione cartella su disco (es. file già decompressi/spostati)
                    if percent is None and tdir and os.path.isdir(tdir):
                        try:
                            total_bytes = sum(
                                os.path.getsize(os.path.join(r, f))
                                for r, _, files in os.walk(tdir)
                                for f in files
                            )
                            dl_mb = total_bytes / (1024 * 1024)
                            percent = min(99, max(0, int((dl_mb / total_mb) * 100)))
                        except Exception:
                            percent = None

                    if percent is not None and percent > state['pct']:
                        state['pct'] = percent
                        try:
                            cb(percent)
                        except Exception as e:
                            print(f"Errore callback progresso ({ms}): {e}", flush=True)

            def flush(self):
                self.original_stderr.flush()

        # Installiamo il redirector globale una sola volta
        if not hasattr(sys.stderr, '_is_global_redirector'):
            sys.stderr = GlobalTqdmRedirector(sys.stderr)
            sys.stderr._is_global_redirector = True
        
        # Monkey-patch tqdm per forzare l'uso del nostro stderr
        try:
            import tqdm
            if not hasattr(tqdm.tqdm, '_is_patched_for_voice_assistant'):
                original_init = tqdm.tqdm.__init__
                def patched_init(self, *args, **kwargs):
                    kwargs['file'] = sys.stderr
                    kwargs['disable'] = False
                    kwargs['ncols'] = 100
                    original_init(self, *args, **kwargs)
                tqdm.tqdm.__init__ = patched_init
                tqdm.tqdm._is_patched_for_voice_assistant = True
        except Exception:
            pass

        
        from faster_whisper import WhisperModel, download_model
        import shutil

        os.makedirs(self.MODELS_DIR, exist_ok=True)

        # Migrazione vecchie cartelle HuggingFace (models--Systran--faster-whisper-*)
        old_hf_dir = os.path.join(self.MODELS_DIR, f"models--Systran--faster-whisper-{model_size}")
        if not os.path.exists(os.path.join(target_dir, "model.bin")) and os.path.isdir(old_hf_dir):
            snapshots_dir = os.path.join(old_hf_dir, "snapshots")
            if os.path.isdir(snapshots_dir):
                for snap in os.listdir(snapshots_dir):
                    snap_path = os.path.join(snapshots_dir, snap)
                    if os.path.exists(os.path.join(snap_path, "model.bin")):
                        print(f"Migrazione modello Whisper da '{snap_path}' a '{target_dir}'...")
                        shutil.copytree(snap_path, target_dir, dirs_exist_ok=True)
                        shutil.rmtree(old_hf_dir, ignore_errors=True)
                        break

        try:
            if not (os.path.isdir(target_dir) and os.path.exists(os.path.join(target_dir, "model.bin"))):
                print(f"Scaricamento modello Whisper '{model_size}' in '{target_dir}'...")
                download_model(model_size, output_dir=target_dir)
                
            self.model = WhisperModel(target_dir, device=device, compute_type=compute_type)
            print(f"Modello Whisper '{model_folder_name}' caricato con successo da {target_dir}.")
        except Exception as e:
            print(f"Errore caricamento modello Whisper: {e}")
            self.model = None
            raise RuntimeError(f"Errore caricamento/download modello Whisper {model_size}: {e}")
        finally:
            if hasattr(sys, '_va_downloads'):
                sys._va_downloads.pop(thread_id, None)

        self.audio_buffer = bytearray()
        
    def process_chunk(self, data: bytes) -> tuple[str, str]:
        if not self.model:
            return "", ""
            
        # Accumuliamo l'audio nel buffer.
        # A differenza di Vosk, Whisper lavora meglio su segmenti interi (batch).
        self.audio_buffer.extend(data)
        
        # Poiché stiamo accumulando, non c'è testo intermedio.
        return "", ""

    def flush_and_transcribe(self) -> str:
        """Forza la trascrizione del buffer accumulato (es. a fine frase)."""
        if not self.model or not self.audio_buffer:
            return ""
            
        # Converte byte (int16 PCM) in array numpy float32 normalizzato (-1.0, 1.0)
        audio_np = np.frombuffer(self.audio_buffer, np.int16).flatten().astype(np.float32) / 32768.0
        
        print(f"Whisper sta trascrivendo {len(audio_np)/16000:.1f} secondi di audio...")
        try:
            segments, info = self.model.transcribe(audio_np, beam_size=5, language="it")
            text = " ".join([segment.text for segment in segments]).strip()
        except Exception as e:
            print(f"Errore durante la trascrizione Whisper: {e}")
            text = ""
            
        self.audio_buffer.clear()
        return text

    def reset(self):
        self.audio_buffer.clear()

    @classmethod
    def get_available_models(cls) -> list[dict]:
        return [
            {"id": "tiny", "name": "Tiny (~75MB - Multilingua, Veloce)", "lang": "multilingual", "lang_text": "Multilingual", "size_text": "~75MB"},
            {"id": "tiny.en", "name": "Tiny English (~75MB - Solo Inglese)", "lang": "en", "lang_text": "English", "size_text": "~75MB"},
            {"id": "base", "name": "Base (~140MB - Bilanciato, Consigliato)", "lang": "multilingual", "lang_text": "Multilingual", "size_text": "~140MB"},
            {"id": "base.en", "name": "Base English (~140MB - Solo Inglese)", "lang": "en", "lang_text": "English", "size_text": "~140MB"},
            {"id": "small", "name": "Small (~466MB - Buona accuratezza)", "lang": "multilingual", "lang_text": "Multilingual", "size_text": "~466MB"},
            {"id": "small.en", "name": "Small English (~466MB - Solo Inglese)", "lang": "en", "lang_text": "English", "size_text": "~466MB"},
            {"id": "medium", "name": "Medium (~1.5GB - Alta accuratezza)", "lang": "multilingual", "lang_text": "Multilingual", "size_text": "~1.5GB"},
            {"id": "medium.en", "name": "Medium English (~1.5GB - Solo Inglese)", "lang": "en", "lang_text": "English", "size_text": "~1.5GB"},
            {"id": "large-v3", "name": "Large v3 (~3.1GB - Massima accuratezza)", "lang": "multilingual", "lang_text": "Multilingual", "size_text": "~3.1GB"}
        ]

