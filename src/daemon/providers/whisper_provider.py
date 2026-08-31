import os
import threading
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
        
        model_folder_name = f"whisper-{model_size}" if not model_size.startswith("whisper-") else model_size
        target_dir = os.path.join(self.MODELS_DIR, model_folder_name)

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

        stop_event = threading.Event()
        monitor_thread = None

        if progress_callback and not (os.path.isdir(target_dir) and os.path.exists(os.path.join(target_dir, "model.bin"))):
            def monitor_progress():
                sizes = {
                    "tiny": 75, "tiny.en": 75,
                    "base": 140, "base.en": 140,
                    "small": 466, "small.en": 466,
                    "medium": 1500, "medium.en": 1500,
                    "large-v1": 3100, "large-v2": 3100,
                    "large-v3": 3100, "large": 3100
                }
                clean_ms = model_size.replace("whisper-", "").strip()
                total_mb = sizes.get(clean_ms, 140)
                total_bytes_expected = total_mb * 1024 * 1024
                
                last_pct = -1
                while not stop_event.is_set():
                    if os.path.isdir(target_dir):
                        try:
                            current_bytes = sum(
                                os.path.getsize(os.path.join(r, f))
                                for r, _, files in os.walk(target_dir)
                                for f in files
                            )
                            pct = min(99, max(0, int((current_bytes / total_bytes_expected) * 100)))
                            if pct > last_pct:
                                last_pct = pct
                                try:
                                    progress_callback(pct)
                                except Exception as e:
                                    print(f"Errore callback monitor ({model_size}): {e}", flush=True)
                        except Exception:
                            pass
                    stop_event.wait(0.3)

            monitor_thread = threading.Thread(target=monitor_progress, daemon=True)
            monitor_thread.start()

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
            stop_event.set()
            if monitor_thread and monitor_thread.is_alive():
                monitor_thread.join(timeout=1.0)

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

