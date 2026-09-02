import sys
import os
import time
import threading
import logging
import urllib.request
import numpy as np
from core.logger import ErrorCollector
from .base import STTProvider

logger = logging.getLogger("VoiceAssistant.STT.Whisper")

def setup_tqdm_patch():
    if getattr(sys, '_va_patched', False):
        return
    sys._va_active_downloads = {}
    sys._va_patched = True

    targets = []
    try:
        import tqdm.std
        targets.append(tqdm.std.tqdm)
    except Exception:
        pass
    try:
        import huggingface_hub.file_download
        targets.append(huggingface_hub.file_download.tqdm)
    except Exception:
        pass
    try:
        import faster_whisper.utils
        targets.append(faster_whisper.utils.disabled_tqdm)
    except Exception:
        pass

    for cls in targets:
        orig_init = cls.__init__

        def make_init(o_init):
            def patched_init(self, *args, **kwargs):
                try:
                    o_init(self, *args, **kwargs)
                except Exception:
                    pass
                self._va_tid = threading.get_ident()
                if not hasattr(self, 'n'):
                    self.n = 0
            return patched_init

        def patched_update(self, n=1):
            if not hasattr(self, 'n'):
                self.n = 0
            self.n += n
            tid = getattr(self, '_va_tid', threading.get_ident())
            downloads = getattr(sys, '_va_active_downloads', {})
            total = getattr(self, 'total', None)
            if tid in downloads and total and total > 5 * 1024 * 1024:
                info = downloads[tid]
                pct = min(99, max(0, int((self.n / total) * 100)))
                if pct > info['last_pct']:
                    info['last_pct'] = pct
                    try:
                        info['cb'](pct)
                    except Exception as e:
                        logger.warning(f"Interruzione download callback ({tid}): {e}")
                        raise e
            return n

        cls.__init__ = make_init(orig_init)
        cls.update = patched_update

setup_tqdm_patch()

class WhisperProvider(STTProvider):
    MODELS_DIR = os.path.expanduser("~/.local/share/voice-assistant/models")
    
    def __init__(self, model_size: str, hardware: str, extra: dict, progress_callback=None, models_dir: str = None, download_only: bool = False):
        if models_dir and len(models_dir.strip()) > 0:
            self.MODELS_DIR = os.path.expanduser(models_dir)
        else:
            self.MODELS_DIR = os.path.expanduser("~/.local/share/voice-assistant/models")

        if not download_only:
            logger.info(f"Inizializzazione WhisperProvider ({model_size}) e Silero VAD (ONNX)...")
        else:
            logger.info(f"Scaricamento background modello Whisper '{model_size}' e Silero VAD...")

        try:
            from faster_whisper import WhisperModel, download_model
            import onnxruntime as ort
        except ImportError:
            logger.error("ERRORE CRITICO: pacchetti 'faster-whisper' o 'onnxruntime' non installati.")
            ErrorCollector.record_error(*sys.exc_info(), component="VoiceAssistant.STT.Whisper")
            self.model = None
            raise RuntimeError("Pacchetti 'faster-whisper' o 'onnxruntime' non installati.")

        # 1. --- Inizializzazione Silero VAD via ONNX Runtime (Zero-Bloat) ---
        self.vad_session = None
        vad_path = os.path.join(self.MODELS_DIR + "/stt", "silero_vad.onnx")
        
        try:
            os.makedirs(self.MODELS_DIR + "/stt", exist_ok=True)
            if not os.path.exists(vad_path):
                logger.info("Scaricamento di Silero VAD (ONNX) in corso...")
                vad_url = "https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx"
                urllib.request.urlretrieve(vad_url, vad_path)

            opts = ort.SessionOptions()
            opts.inter_op_num_threads = 1
            opts.intra_op_num_threads = 1
            self.vad_session = ort.InferenceSession(vad_path, providers=['CPUExecutionProvider'], sess_options=opts)
            
            # Stati e contatori per Silero v5
            self._vad_state = np.zeros((2, 1, 128), dtype=np.float32)
            self._vad_context = np.zeros(64, dtype=np.float32)
            self._vad_consecutive = 0
            self._silence_counter = 0
            
            logger.info("Silero VAD (ONNX) caricato e pronto in memoria.")
        except Exception as e:
            logger.warning(f"Impossibile caricare Silero VAD ONNX (uso fallback RMS): {e}")
            self.vad_session = None

        device = "cuda" if hardware == "cuda" else "cpu"
        compute_type = "float16" if device == "cuda" else "int8"
        
        stt_dir = os.path.join(self.MODELS_DIR, "stt")
        os.makedirs(stt_dir, exist_ok=True)
        
        model_folder_name = f"whisper-{model_size}" if not model_size.startswith("whisper-") else model_size
        target_dir = os.path.join(stt_dir, model_folder_name)
        legacy_dir = os.path.join(self.MODELS_DIR, model_folder_name)
        if not os.path.exists(os.path.join(target_dir, "model.bin")) and os.path.exists(os.path.join(legacy_dir, "model.bin")):
            target_dir = legacy_dir

        import shutil
        old_hf_dir = os.path.join(self.MODELS_DIR, f"models--Systran--faster-whisper-{model_size}")
        if not os.path.exists(os.path.join(target_dir, "model.bin")) and os.path.isdir(old_hf_dir):
            snapshots_dir = os.path.join(old_hf_dir, "snapshots")
            if os.path.isdir(snapshots_dir):
                for snap in os.listdir(snapshots_dir):
                    snap_path = os.path.join(snapshots_dir, snap)
                    if os.path.exists(os.path.join(snap_path, "model.bin")):
                        logger.info(f"Migrazione modello Whisper da '{snap_path}' a '{target_dir}'...")
                        shutil.copytree(snap_path, target_dir, dirs_exist_ok=True)
                        shutil.rmtree(old_hf_dir, ignore_errors=True)
                        break

        thread_id = threading.get_ident()
        downloads = getattr(sys, '_va_active_downloads', None)
        if progress_callback and downloads is not None:
            downloads[thread_id] = {'cb': progress_callback, 'last_pct': -1}

        try:
            if not (os.path.isdir(target_dir) and os.path.exists(os.path.join(target_dir, "model.bin"))):
                logger.info(f"Scaricamento modello Whisper '{model_size}' in '{target_dir}'...")
                download_model(model_size, output_dir=target_dir)
                
            if not download_only:
                self.model = WhisperModel(target_dir, device=device, compute_type=compute_type)
                logger.info(f"Modello Whisper '{model_folder_name}' caricato con successo da {target_dir}.")
            else:
                self.model = None
                logger.info(f"Modello Whisper '{model_folder_name}' scaricato con successo in {target_dir}.")
        except Exception as e:
            logger.error(f"Errore caricamento modello Whisper: {e}")
            ErrorCollector.record_error(*sys.exc_info(), component="VoiceAssistant.STT.Whisper")
            self.model = None
            if os.path.exists(target_dir) and not os.path.exists(os.path.join(target_dir, "model.bin")):
                logger.info(f"Rimozione cartella download incompleto per Whisper: {target_dir}")
                shutil.rmtree(target_dir, ignore_errors=True)
            raise RuntimeError(f"Errore caricamento/download modello Whisper {model_size}: {e}")
        finally:
            if downloads is not None:
                downloads.pop(thread_id, None)

        self.audio_buffer = bytearray()
        
    def process_chunk(self, data: bytes) -> tuple[str, str]:
        if not self.model:
            return "", ""
            
        self.audio_buffer.extend(data)
        
        if not hasattr(self, '_vad_buffer'):
            self._vad_buffer = bytearray()
        self._vad_buffer.extend(data)
        
        voice_detected_in_this_chunk = False

        # 1. Rilevamento tramite Silero VAD (ONNX Runtime) con blocchi da 576 campioni (64 context + 512 nuovi)
        if self.vad_session is not None:
            try:
                while len(self._vad_buffer) >= 1024:
                    chunk_bytes = self._vad_buffer[:1024]
                    self._vad_buffer = self._vad_buffer[1024:]
                    
                    audio_np = np.frombuffer(chunk_bytes, np.int16).astype(np.float32) / 32768.0
                    
                    if not hasattr(self, '_vad_context'):
                        self._vad_context = np.zeros(64, dtype=np.float32)
                        
                    input_chunk = np.concatenate([self._vad_context, audio_np])
                    self._vad_context = audio_np[-64:]
                    
                    input_data = np.expand_dims(input_chunk, axis=0) # Shape: (1, 576)
                    sr_data = np.array(16000, dtype=np.int64)
                    
                    ort_inputs = {
                        'input': input_data,
                        'sr': sr_data,
                        'state': self._vad_state
                    }
                    
                    ort_outs = self.vad_session.run(None, ort_inputs)
                    speech_prob = ort_outs[0][0][0]
                    self._vad_state = ort_outs[1]
                    
                    if not hasattr(self, '_vad_consecutive'):
                        self._vad_consecutive = 0
                    if not hasattr(self, '_silence_counter'):
                        self._silence_counter = 0

                    # Soglia reattiva a 0.3 + watchdog anti-addormentamento
                    if speech_prob > 0.3:
                        self._vad_consecutive += 1
                        self._silence_counter = 0
                    else:
                        self._vad_consecutive = max(0, self._vad_consecutive - 1)
                        self._silence_counter += 1
                        
                        if self._silence_counter > 100: # ~3 secondi di silenzio ininterrotto
                            self._vad_state = np.zeros((2, 1, 128), dtype=np.float32)
                            self._silence_counter = 0
                            
                    if self._vad_consecutive >= 1:
                        voice_detected_in_this_chunk = True
                        
            except Exception as e:
                logger.debug(f"Errore runtime ONNX VAD: {e}")
                self.vad_session = None

        # 2. Fallback di sicurezza RMS se ONNX non è disponibile
        if self.vad_session is None:
            if len(data) > 0:
                audio_np_rms = np.frombuffer(data, np.int16).astype(np.float32)
                if np.sqrt(np.mean(audio_np_rms**2)) > 250:
                    voice_detected_in_this_chunk = True

        # Gestione del flusso per mantenere vivo il main.py
        if voice_detected_in_this_chunk:
            self._last_fake_partial = f"speech_detected_{int(time.time() * 10)}"
            return "", self._last_fake_partial
            
        if hasattr(self, '_last_fake_partial'):
            return "", self._last_fake_partial
            
        return "", ""

    def flush_and_transcribe(self) -> str:
        if not self.model or not self.audio_buffer:
            return ""
            
        audio_np = np.frombuffer(self.audio_buffer, np.int16).flatten().astype(np.float32) / 32768.0
        logger.info(f"Whisper sta trascrivendo {len(audio_np)/16000:.1f} secondi di audio...")
        
        try:
            segments, info = self.model.transcribe(audio_np, beam_size=1, language="it")
            text = " ".join([segment.text for segment in segments]).strip()
        except Exception as e:
            logger.error(f"Errore durante la trascrizione Whisper: {e}")
            ErrorCollector.record_error(*sys.exc_info(), component="VoiceAssistant.STT.Whisper")
            text = ""
            
        self.reset()
        return text

    def reset(self):
        self.audio_buffer.clear()
        if hasattr(self, '_vad_buffer'):
            self._vad_buffer.clear()
        if hasattr(self, '_last_fake_partial'):
            del self._last_fake_partial
        self._vad_state = np.zeros((2, 1, 128), dtype=np.float32)
        self._vad_context = np.zeros(64, dtype=np.float32)
        self._vad_consecutive = 0
        self._silence_counter = 0

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