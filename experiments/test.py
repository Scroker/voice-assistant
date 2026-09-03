import os
import sys
import queue
import threading
import urllib.request
import numpy as np
import subprocess
import time
import atexit
import re
import collections
import sounddevice as sd
import onnxruntime as ort
import scipy.signal
from scipy.signal import butter, sosfilt, sosfilt_zi
from resemblyzer import VoiceEncoder, preprocess_wav
import openwakeword
from openwakeword.model import Model

# Gestione flag debug da riga di comando
DEBUG_MODE = "--debug" in sys.argv or "-d" in sys.argv
SILERO_INTERNAL_THREADS = 2
SILERO_EXTERNAL_THREADS = 1

# ==========================================
# 1. ARCHITETTURA PIPELINE MODULARE E KWS
# ==========================================

class AudioProcessor:
    def __init__(self, name, enabled=True):
        self.name = name
        self.enabled = enabled

    def process(self, audio_chunk):
        if not self.enabled: return audio_chunk
        return self._apply_effect(audio_chunk)

    def _apply_effect(self, audio_chunk):
        raise NotImplementedError()


class BandpassFilter(AudioProcessor):
    def __init__(self, lowcut=80.0, highcut=7900.0, fs=16000, enabled=True):
        super().__init__("Filtro Passa-Banda", enabled)
        self.fs = fs
        nyq = 0.5 * fs
        if highcut >= nyq: highcut = nyq - 10.0 
        
        self.sos = butter(4, [lowcut / nyq, highcut / nyq], btype='band', output='sos')
        self.zi = sosfilt_zi(self.sos)

    def _apply_effect(self, audio_chunk):
        chunk_1d = audio_chunk.flatten() 
        filtered, self.zi = sosfilt(self.sos, chunk_1d, zi=self.zi)
        return filtered.reshape(-1, 1).astype(np.float32)


class AGCBlock(AudioProcessor):
    def __init__(self, target_dbfs=-15.0, alpha=0.1, enabled=True):
        super().__init__("Automatic Gain Control", enabled)
        self.target_rms = 10 ** (target_dbfs / 20.0)
        self.current_gain = 1.0
        self.alpha = alpha

    def _apply_effect(self, audio_chunk):
        rms = np.sqrt(np.mean(audio_chunk**2))
        if rms > 0.0001: 
            desired_gain = self.target_rms / rms
            self.current_gain = (self.alpha * desired_gain) + ((1 - self.alpha) * self.current_gain)
            self.current_gain = min(self.current_gain, 15.0) 
            audio_chunk = audio_chunk * self.current_gain
            audio_chunk = np.clip(audio_chunk, -1.0, 1.0)
        return audio_chunk.astype(np.float32)


class SileroVADBlock(AudioProcessor):
    def __init__(self, sample_rate=16000, vad_threshold=0.45, rms_threshold=0.002, enabled=True):
        super().__init__("Silero VAD (V5)", enabled)
        self.threshold = vad_threshold
        self.rms_threshold = rms_threshold
        self.fs = sample_rate
        
        model_path = "silero_vad.onnx"
        if not os.path.exists(model_path):
            print("⏳ Download del modello Silero VAD in corso...")
            urllib.request.urlretrieve("https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx", model_path)
            
        options = ort.SessionOptions()
        options.intra_op_num_threads = SILERO_INTERNAL_THREADS if 'SILERO_INTERNAL_THREADS' in globals() else 1
        options.inter_op_num_threads = SILERO_EXTERNAL_THREADS if 'SILERO_EXTERNAL_THREADS' in globals() else 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        
        self.session = ort.InferenceSession(model_path, sess_options=options)
        self.reset_states()

    def reset_states(self):
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros(64, dtype=np.float32)

    def _apply_effect(self, audio_chunk):
        audio_float32 = audio_chunk.flatten().astype(np.float32)
        rms_value = np.sqrt(np.mean(audio_float32**2))
        
        if rms_value >= self.rms_threshold:
            if len(audio_float32) > 512: audio_float32 = audio_float32[:512]
            elif len(audio_float32) < 512: audio_float32 = np.pad(audio_float32, (0, 512 - len(audio_float32)))

            input_data = np.concatenate([self.context, audio_float32])
            self.context = audio_float32[-64:]
            
            inputs = {
                'input': np.expand_dims(input_data, axis=0), 
                'sr': np.array(self.fs, dtype=np.int64), 
                'state': self.state
            }
            out, self.state = self.session.run(None, inputs)
            if out[0][0] > self.threshold:
                return audio_chunk
                
        return np.array([], dtype=audio_chunk.dtype)


class DynamicSpeakerIdentification(AudioProcessor):
    def __init__(self, threshold=0.75, fs=16000, max_history=19, enabled=True):
        super().__init__("Dynamic Speaker Identification", enabled)
        self.fs = fs
        self.threshold = threshold
        self.max_history = max_history
        self.encoder = VoiceEncoder()
        
        self.speakers = {}
        self.lock = threading.Lock()
        self.embed_queue = queue.Queue()
        
        self.load_profiles_from_disk()
        threading.Thread(target=self._background_worker, daemon=True).start()

    def load_profiles_from_disk(self, directory="."):
        with self.lock:
            for file in os.listdir(directory):
                if file.endswith(".npz"):
                    name = os.path.splitext(file)[0]
                    try:
                        data = np.load(os.path.join(directory, file))
                        self.speakers[name] = {'anchor': data['anchor'], 'history': data['history']}
                    except Exception as e:
                        print(f"⚠️ Errore caricamento {file}: {e}")

    def register_speaker(self, name, raw_audio):
        processed_wav = preprocess_wav(raw_audio.flatten(), source_sr=self.fs)
        embedding = self.encoder.embed_utterance(processed_wav)
        embedding = embedding / np.linalg.norm(embedding)
        
        with self.lock:
            self.speakers[name] = {'anchor': embedding, 'history': np.empty((0, 256))}
        np.savez(f"{name}.npz", anchor=embedding, history=np.empty((0, 256)))
        print(f"\n✨ Soggetto '{name}' registrato!")

    def process_full_utterance(self, full_audio):
        """Metodo chiamato dalla pipeline quando il comando è completo"""
        self.embed_queue.put(full_audio)

    def _apply_effect(self, audio_chunk):
        # Questo blocco non processa più i chunk stream, riceve la frase completa
        return audio_chunk

    def get_reference_embedding(self, speaker_data):
        anchor, history = speaker_data['anchor'], speaker_data['history']
        if len(history) == 0: return anchor
        weights = np.linspace(0.4, 1.0, len(history))
        history_mean = np.average(history, axis=0, weights=weights / weights.sum())
        combined = (0.5 * anchor) + (0.5 * history_mean / np.linalg.norm(history_mean))
        return combined / np.linalg.norm(combined)

    def _background_worker(self):
        while True:
            full_utterance = self.embed_queue.get()
            if full_utterance is None: break

            processed_utterance = preprocess_wav(full_utterance, source_sr=self.fs)
            if len(processed_utterance) < int(self.fs * 0.4): continue
                
            current_embedding = self.encoder.embed_utterance(processed_utterance)
            current_embedding = current_embedding / np.linalg.norm(current_embedding)
            
            best_match, highest_score = "Sconosciuto", 0.0
            with self.lock:
                snapshots = {n: self.get_reference_embedding(d) for n, d in self.speakers.items()}

            for name, ref_emb in snapshots.items():
                sim = np.dot(current_embedding, ref_emb)
                if sim > highest_score:
                    highest_score, best_match = sim, name
            
            if highest_score >= self.threshold:
                if DEBUG_MODE: print(f"\n👤 [RICONOSCIUTO] {best_match} ({highest_score:.2f})")
                if highest_score >= self.threshold + 0.05:
                    with self.lock:
                        history = self.speakers[best_match]['history']
                        history = np.vstack([history, current_embedding]) if len(history) > 0 else np.array([current_embedding])
                        if len(history) > self.max_history: history = history[1:]
                        self.speakers[best_match]['history'] = history
                        np.savez(f"{best_match}.npz", anchor=self.speakers[best_match]['anchor'], history=history)
            elif DEBUG_MODE:
                print(f"\n❓ [SCONOSCIUTO] Affinità max: {highest_score:.2f}")


class AudioPipeline:
    def __init__(self, sample_rate=16000, wakeword="alexa"):
        self.blocks = []
        self.fs = sample_rate
        self.state = "WAITING"
        
        print(f"⏳ Inizializzazione OpenWakeWord ({wakeword})...")
        openwakeword.utils.download_models()
        self.oww_model = Model(wakeword_models=[wakeword], inference_framework="onnx")
        self.wakeword_name = wakeword
        
        # Buffer circolare e di cattura uniti
        self.ww_buffer = collections.deque(maxlen=int((1.5 * self.fs) / 512))
        self.cmd_buffer = []
        
        self.last_speech_time = time.time()
        self.silence_timeout = 1.2
        self.rms_threshold = 0.002

    def add_block(self, block):
        self.blocks.append(block)

    def get_block(self, block_type):
        return next((b for b in self.blocks if isinstance(b, block_type)), None)

    def process_stream(self, audio_chunk):
        processed = audio_chunk
        
        # 1. Filtri AEC e Pre-processing sempre attivi
        for block in self.blocks:
            if isinstance(block, (BandpassFilter, AGCBlock)):
                processed = block.process(processed)
                
        audio_float32 = processed.flatten().astype(np.float32)
        rms = np.sqrt(np.mean(audio_float32**2))
        
        vad_block = self.get_block(SileroVADBlock)
        speaker_block = self.get_block(DynamicSpeakerIdentification)
        
        if self.state == "WAITING":
            self.ww_buffer.append(audio_float32)
            
            # KWS lavora solo se l'energia supera il gate (Zero CPU nel silenzio)
            if rms >= self.rms_threshold:
                prediction = self.oww_model.predict(audio_float32)
                if prediction.get(self.wakeword_name, 0.0) > 0.5:
                    print(f"\n🔔 [WAKEWORD] '{self.wakeword_name.upper()}' rilevata! Inizio ascolto...")
                    self.state = "RECORDING"
                    self.cmd_buffer = list(self.ww_buffer) # Incolla la wakeword!
                    self.last_speech_time = time.time()
                    if vad_block: vad_block.reset_states()
                    
            return np.array([], dtype=np.float32) # Visualizer mostra silenzio

        elif self.state == "RECORDING":
            self.cmd_buffer.append(audio_float32)
            is_speaking = False
            
            if vad_block:
                vad_out = vad_block.process(processed)
                if len(vad_out) > 0:
                    self.last_speech_time = time.time()
                    is_speaking = True
                    
            if time.time() - self.last_speech_time > self.silence_timeout:
                print("\n🛑 [FINE COMANDO] Silenzio rilevato. Avvio elaborazione...")
                full_audio = np.concatenate(self.cmd_buffer)
                self.cmd_buffer = []
                self.ww_buffer.clear()
                self.state = "WAITING"
                
                if len(full_audio) >= int(self.fs * 1.5) and speaker_block:
                    speaker_block.process_full_utterance(full_audio)
                else:
                    print("⚠️ Comando troppo breve per l'identificazione.")
            
            # Ritorna audio pieno solo se il VAD lo convalida, per aggiornare l'interfaccia
            return processed if is_speaking else np.array([], dtype=np.float32)

# ==========================================
# 2. CONFIGURAZIONE E ACQUISIZIONE AUDIO
# ==========================================

SAMPLE_RATE = 16000
CHANNELS = 1
TARGET_BLOCK_SIZE = 512
DSI_THRESHOLD = 0.75
RMS_THRESHOLD = 0.002
VAD_THRESHOLD = 0.4
AGC_TARGET_DBFS = 15.0 
audio_queue = queue.Queue()
loaded_pactl_module_index = None

def audio_callback(indata, frames, time_info, status):
    if status: print(status, file=sys.stderr)
    audio_queue.put(indata.copy())

def cleanup_aec():
    global loaded_pactl_module_index
    if loaded_pactl_module_index is not None:
        try:
            env = os.environ.copy()
            if "XDG_RUNTIME_DIR" not in env: env["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"
            subprocess.run(["pactl", "unload-module", str(loaded_pactl_module_index)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print("✅ AEC disattivato correttamente.")
        except Exception: pass

atexit.register(cleanup_aec)

def ensure_pipewire_aec():
    # [Logica AEC invariata dal tuo script originario...]
    global loaded_pactl_module_index
    keywords = ["echo-cancel", "echo", "cancel", "aec"]
    
    def scan_devices():
        sd._terminate(); sd._initialize()
        for i, dev in enumerate(sd.query_devices()):
            if any(kw in dev['name'].lower() for kw in keywords) and dev['max_input_channels'] > 0: return i
        return None

    env = os.environ.copy()
    if "XDG_RUNTIME_DIR" not in env: env["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"

    dev_id = scan_devices()
    if dev_id is not None:
        print(f"✅ Trovato dispositivo AEC esistente (ID: {dev_id})")
    else:
        print("⚙️ Attivazione dinamica AEC via pactl...")
        try:
            res = subprocess.run(["pactl", "load-module", "module-echo-cancel", "aec_method=webrtc"], env=env, stdout=subprocess.PIPE, text=True)
            match = re.search(r'\d+', res.stdout)
            if match: loaded_pactl_module_index = int(match.group())
            time.sleep(1.0) 
            dev_id = scan_devices()
        except Exception as e:
            print(f"⚠️ Impossibile caricare AEC: {e}")
            return None
    return dev_id

if __name__ == "__main__":
    pipeline = AudioPipeline(sample_rate=SAMPLE_RATE, wakeword="alexa")
    
    pipeline.add_block(BandpassFilter(fs=SAMPLE_RATE, enabled=True))
    pipeline.add_block(AGCBlock(target_dbfs=-AGC_TARGET_DBFS, enabled=True))
    pipeline.add_block(SileroVADBlock(vad_threshold=VAD_THRESHOLD, rms_threshold=RMS_THRESHOLD, sample_rate=SAMPLE_RATE, enabled=True))    
    
    speaker_block = DynamicSpeakerIdentification(threshold=DSI_THRESHOLD, fs=SAMPLE_RATE, enabled=True)
    pipeline.add_block(speaker_block)
    
    if not speaker_block.speakers:
        print("\n" + "="*50)
        print("🎙️ CONFIGURAZIONE INIZIALE: Nessun profilo trovato.")
        name = input("👉 Inserisci il nome del primo utente da registrare: ").strip() or "Utente"
        input(f"Premi INVIO e pronuncia una frase continua per 6 secondi...")
        print("🔴 Registrazione in corso...")
        
        raw_audio = sd.rec(int(6.0 * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=CHANNELS, dtype='float32')
        sd.wait()
        
        # Filtra la registrazione iniziale
        filtered_audio = raw_audio
        for block in pipeline.blocks:
            if isinstance(block, (BandpassFilter, AGCBlock)):
                processed_chunks = [block.process(filtered_audio[i:i+512].reshape(-1, 1)).flatten() for i in range(0, len(filtered_audio), 512) if len(filtered_audio[i:i+512]) > 0]
                filtered_audio = np.concatenate(processed_chunks).reshape(-1, 1)

        speaker_block.register_speaker(name, filtered_audio)
        print("="*50 + "\n")

    device_id = ensure_pipewire_aec()
    native_sr = int(sd.query_devices(device_id)['default_samplerate']) if device_id is not None else SAMPLE_RATE
    native_blocksize = int(TARGET_BLOCK_SIZE * (native_sr / SAMPLE_RATE))

    print(f"\n🎙️ Avvio stream microfono... (Debug Mode: {'ON' if DEBUG_MODE else 'OFF'})")

    try:
        print_counter = 0
        with sd.InputStream(device=device_id, samplerate=native_sr, channels=CHANNELS, blocksize=native_blocksize, dtype='float32', callback=audio_callback):
            while True:
                raw_chunk = audio_queue.get()
                
                if native_sr != SAMPLE_RATE:
                    raw_chunk = raw_chunk.flatten()[::int(native_sr / SAMPLE_RATE)].reshape(-1, 1).astype(np.float32)

                vol = np.max(np.abs(raw_chunk))
                processed_chunk = pipeline.process_stream(raw_chunk)
                
                if DEBUG_MODE:
                    print_counter += 1
                    if print_counter >= 15:
                        print_counter = 0
                        bar = "█" * int(min(vol, 1.0) * 40) 
                        status = "🗣️ VOCE" if len(processed_chunk) > 0 else "🔇 SILENZIO"
                        print(f"{status.ljust(12)} | Vol: {vol:.3f} | {bar}".ljust(80), end="\r")
                    
    except KeyboardInterrupt:
        print("\n\n🛑 Stream interrotto dall'utente. Uscita in corso.")
        sys.exit(0)