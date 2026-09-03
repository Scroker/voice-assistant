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

# Gestione flag debug da riga di comando
DEBUG_MODE = "--debug" in sys.argv or "-d" in sys.argv
SILERO_INTERNAL_THREADS = 2
SILERO_EXTERNAL_THREADS = 1

# ==========================================
# 1. ARCHITETTURA PIPELINE MODULARE
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
    def __init__(self, sample_rate=16000, vad_threshold=0.45, rms_threshold=0.007, pre_pad_ms=400, enabled=True):
        super().__init__("Silero VAD (V5)", enabled)
        self.threshold = vad_threshold
        self.rms_threshold = rms_threshold
        self.fs = sample_rate
        
        # Inizializzazione Buffer Circolare per il Pre-padding
        chunk_duration_ms = (512 / self.fs) * 1000
        max_history = int(pre_pad_ms / chunk_duration_ms)
        self.history_buffer = collections.deque(maxlen=max_history)
        self.is_speaking = False
        
        model_path = "silero_vad.onnx"
        if not os.path.exists(model_path):
            print("⏳ Download del modello Silero VAD in corso...")
            url = "https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx"
            urllib.request.urlretrieve(url, model_path)
            
        options = ort.SessionOptions()
        options.intra_op_num_threads = SILERO_INTERNAL_THREADS if 'SILERO_INTERNAL_THREADS' in globals() else 1
        options.inter_op_num_threads = SILERO_EXTERNAL_THREADS if 'SILERO_EXTERNAL_THREADS' in globals() else 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        
        self.session = ort.InferenceSession(model_path, sess_options=options)
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros(64, dtype=np.float32)

    def _apply_effect(self, audio_chunk):
        # 1. Conserviamo sempre il chunk nel buffer storico
        self.history_buffer.append(audio_chunk)
        
        audio_float32 = audio_chunk.flatten().astype(np.float32)
        
        # 2. Gate RMS: Calcola l'energia prima di attivare la rete neurale
        rms_value = np.sqrt(np.mean(audio_float32**2))
        probability = 0.0
        
        if rms_value >= self.rms_threshold:
            if len(audio_float32) > 512:
                audio_float32 = audio_float32[:512]
            elif len(audio_float32) < 512:
                audio_float32 = np.pad(audio_float32, (0, 512 - len(audio_float32)))

            input_data = np.concatenate([self.context, audio_float32])
            self.context = audio_float32[-64:]
            input_tensor = np.expand_dims(input_data, axis=0)
            sr_tensor = np.array(self.fs, dtype=np.int64)

            inputs = {'input': input_tensor, 'sr': sr_tensor, 'state': self.state}
            out, state_out = self.session.run(None, inputs)
            self.state = state_out
            probability = out[0][0]
        
        # 3. Gestione logica Voce / Silenzio con recupero Buffer
        if probability > self.threshold:
            if not self.is_speaking:
                self.is_speaking = True
                # L'utente ha iniziato a parlare: scarica l'intero buffer storico
                return np.vstack(list(self.history_buffer))
            else:
                return audio_chunk
        else:
            self.is_speaking = False
            return np.array([], dtype=audio_chunk.dtype)

class DynamicSpeakerIdentification(AudioProcessor):
    def __init__(self, threshold=0.65, fs=16000, max_history=19, enabled=True):
        super().__init__("Dynamic Speaker Identification", enabled)
        self.fs = fs
        self.threshold = threshold
        self.max_history = max_history  # Max campioni nel buffer rotativo (es. 19 + 1 ancora = 20 totali)
        self.encoder = VoiceEncoder()
        
        self.speakers = {} # Dizionario: nome -> {'anchor': vector, 'history': matrix}
        self.current_utterance_chunks = []
        self.last_speech_time = time.time()
        self.silence_threshold = 0.8  
        
        self.lock = threading.Lock()
        self.embed_queue = queue.Queue()
        
        self.load_profiles_from_disk()
        
        self.worker_thread = threading.Thread(target=self._background_worker, daemon=True)
        self.worker_thread.start()

    def load_profiles_from_disk(self, directory="."):
        with self.lock:
            for file in os.listdir(directory):
                if file.endswith(".npz"):
                    name = os.path.splitext(file)[0]
                    path = os.path.join(directory, file)
                    try:
                        data = np.load(path)
                        if 'anchor' in data and 'history' in data:
                            anchor = data['anchor']
                            history = data['history']
                            self.speakers[name] = {'anchor': anchor, 'history': history}
                            print(f"📁 [PROFILO ANCORATO] '{name}' caricato (Ancora fissa + {len(history)} campioni storici)")
                        else:
                            # Retrocompatibilità per vecchi file .npz senza distinzione
                            embeddings = data['embeddings']
                            anchor = embeddings[0]
                            history = embeddings[1:] if len(embeddings) > 1 else np.empty((0, 256))
                            self.speakers[name] = {'anchor': anchor, 'history': history}
                            np.savez(path, anchor=anchor, history=history)
                            print(f"📁 [MIGRAZIONE] '{name}' convertito in struttura Ancora+FIFO")
                    except Exception as e:
                        print(f"⚠️ Errore nel caricamento di {file}: {e}")
                elif file.endswith(".npy"):
                    name = os.path.splitext(file)[0]
                    path = os.path.join(directory, file)
                    try:
                        emb = np.load(path)
                        emb = emb / np.linalg.norm(emb)
                        self.speakers[name] = {'anchor': emb, 'history': np.empty((0, 256))}
                        np.savez(f"{name}.npz", anchor=emb, history=np.empty((0, 256)))
                        print(f"📁 [MIGRAZIONE .NPY] '{name}' impostato come Ancora fissa")
                    except Exception as e:
                        print(f"⚠️ Errore migrazione {file}: {e}")
            
            if not self.speakers:
                print("⚠️ Nessun profilo vocale trovato nella cartella corrente.")

    def register_speaker(self, name, raw_audio):
        processed_wav = preprocess_wav(raw_audio.flatten(), source_sr=self.fs)
        embedding = self.encoder.embed_utterance(processed_wav)
        embedding = embedding / np.linalg.norm(embedding)
        
        # La primissima registrazione diventa l'ANCORA fissa e inviolabile
        anchor = embedding
        history = np.empty((0, 256)) # Nessun campione storico iniziale
        
        with self.lock:
            self.speakers[name] = {'anchor': anchor, 'history': history}
        np.savez(f"{name}.npz", anchor=anchor, history=history)
        print(f"\n✨ [REGISTRAZIONE] Soggetto '{name}' registrato come Ancora principale in '{name}.npz'!")

    def get_reference_embedding(self, speaker_data):
        """Fonde l'ancora fissa con la media ponderata della history recente"""
        anchor = speaker_data['anchor']
        history = speaker_data['history']
        
        if len(history) == 0:
            return anchor
            
        # Media ponderata della history (dà più peso ai campioni recenti)
        n = len(history)
        weights = np.linspace(0.4, 1.0, n)
        weights /= weights.sum()
        history_mean = np.average(history, axis=0, weights=weights)
        history_mean = history_mean / np.linalg.norm(history_mean)
        
        # Combina l'ancora iniziale (es. 50%) con la history recente (es. 50%)
        combined = (0.5 * anchor) + (0.5 * history_mean)
        return combined / np.linalg.norm(combined)

    def update_speaker_profile(self, name, new_embedding):
        """Aggiunge il nuovo embedding alla history FIFO, lasciando intatta l'ancora"""
        with self.lock:
            if name in self.speakers:
                anchor = self.speakers[name]['anchor']
                history = self.speakers[name]['history']
                
                # Aggiunge in coda alla history
                if len(history) == 0:
                    history = np.array([new_embedding])
                else:
                    history = np.vstack([history, new_embedding])
                    if len(history) > self.max_history:
                        history = history[1:] # Rimuove il più vecchio dalla history, l'ancora è salva!
                
                self.speakers[name] = {'anchor': anchor, 'history': history}
                np.savez(f"{name}.npz", anchor=anchor, history=history)
                print(f"🧬 [AUTO-APPRENDIMENTO] Ancora protetta + History aggiornata ({len(history)}/{self.max_history} campioni).", flush=True)

    def _apply_effect(self, audio_chunk):
        if not self.enabled:
            return audio_chunk

        current_time = time.time()
        
        # Il controllo del timeout deve avvenire SEMPRE, anche se c'è silenzio
        if current_time - self.last_speech_time > self.silence_threshold:
            if len(self.current_utterance_chunks) > 0:
                full_utterance = np.concatenate(self.current_utterance_chunks)
                self.current_utterance_chunks = []
                
                if len(full_utterance) >= int(self.fs * 0.4):
                    self.embed_queue.put(full_utterance)
                
        # Accodiamo i campioni audio SOLO se il VAD li ha lasciati passare
        if len(audio_chunk) > 0:
            self.current_utterance_chunks.append(audio_chunk.flatten())
            self.last_speech_time = current_time
        
        return audio_chunk

    def _background_worker(self):
        while True:
            try:
                full_utterance = self.embed_queue.get()
                if full_utterance is None: break

                processed_utterance = preprocess_wav(full_utterance, source_sr=self.fs)
                if len(processed_utterance) < int(self.fs * 0.4): continue
                    
                current_embedding = self.encoder.embed_utterance(processed_utterance)
                current_embedding = current_embedding / np.linalg.norm(current_embedding)
                
                best_match = "Sconosciuto"
                highest_score = 0.0
                
                with self.lock:
                    speakers_snapshot = {name: self.get_reference_embedding(data) for name, data in self.speakers.items()}

                for name, ref_emb in speakers_snapshot.items():
                    similarity = np.dot(current_embedding, ref_emb)
                    if similarity > highest_score:
                        highest_score = similarity
                        best_match = name
                
                if highest_score >= self.threshold:
                    if DEBUG_MODE:
                        print(f"\n👤 [RICONOSCIUTO] {best_match} (Affinità: {highest_score:.2f})".ljust(80), flush=True)
                    
                    if highest_score >= self.threshold + 0.05 and best_match != "Sconosciuto":
                        self.update_speaker_profile(best_match, current_embedding)
                elif DEBUG_MODE:
                    print(f"\n❓ [SCONOSCIUTO] Affinità max: {highest_score:.2f}".ljust(80), flush=True)
                    
            except Exception as e:
                print(f"⚠️ Errore nel worker di riconoscimento: {e}")


class AudioPipeline:
    def __init__(self):
        self.blocks = []
    def add_block(self, block):
        self.blocks.append(block)
    def process_stream(self, audio_chunk):
        processed = audio_chunk
        for block in self.blocks:
            processed = block.process(processed)
        return processed
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

def audio_callback(indata, frames, time, status):
    if status:
        print(status, file=sys.stderr)
    audio_queue.put(indata.copy())

def cleanup_aec():
    global loaded_pactl_module_index
    if loaded_pactl_module_index is not None:
        print(f"\n🧹 Rimozione modulo AEC (Index PulseAudio: {loaded_pactl_module_index})...")
        try:
            env = os.environ.copy()
            if "XDG_RUNTIME_DIR" not in env:
                env["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"
            subprocess.run(
                ["pactl", "unload-module", str(loaded_pactl_module_index)],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True
            )
            print("✅ AEC disattivato correttamente.")
        except Exception as e:
            print(f"⚠️ Errore durante la rimozione AEC: {e}")

atexit.register(cleanup_aec)

def ensure_pipewire_aec():
    global loaded_pactl_module_index
    keywords = ["echo-cancel", "echo", "cancel", "aec"]
    
    def scan_devices():
        sd._terminate()
        sd._initialize()
        for i, dev in enumerate(sd.query_devices()):
            name_lower = dev['name'].lower()
            if any(kw in name_lower for kw in keywords) and dev['max_input_channels'] > 0:
                return i
        return None

    env = os.environ.copy()
    if "XDG_RUNTIME_DIR" not in env:
        env["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"

    dev_id = scan_devices()
    
    if dev_id is not None:
        print(f"✅ Trovato dispositivo AEC esistente (ID: {dev_id})")
    else:
        print("⚙️ Attivazione dinamica AEC via pactl...")
        try:
            result = subprocess.run(
                ["pactl", "load-module", "module-echo-cancel", "aec_method=webrtc"],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                check=True
            )
            
            match = re.search(r'\d+', result.stdout)
            if match:
                loaded_pactl_module_index = int(match.group())

            time.sleep(1.0) 
            dev_id = scan_devices()
            print(f"✅ AEC attivato con successo (ID Dispositivo: {dev_id} | Index Modulo: {loaded_pactl_module_index})")
                
        except Exception as e:
            print(f"⚠️ Impossibile caricare AEC on-demand: {e}")
            return None

    # --- FORZATURA GLOBALE A PRIORI ---
    # A prescindere che fosse già attivo o appena caricato, impostiamo i default di sistema
    try:
        sinks_res = subprocess.run(["pactl", "list", "short", "sinks"], env=env, stdout=subprocess.PIPE, text=True)
        sources_res = subprocess.run(["pactl", "list", "short", "sources"], env=env, stdout=subprocess.PIPE, text=True)
        
        target_sink = None
        target_source = None
        
        for line in sinks_res.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and "echo-cancel" in parts[1]:
                target_sink = parts[1]
                break
                
        for line in sources_res.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and "echo-cancel" in parts[1]:
                target_source = parts[1]
                break

        if target_sink:
            subprocess.run(["pactl", "set-default-sink", target_sink], env=env, stderr=subprocess.DEVNULL)
            print(f"🔊 Sink predefinito impostato su: {target_sink}")
            
        if target_source:
            subprocess.run(["pactl", "set-default-source", target_source], env=env, stderr=subprocess.DEVNULL)
            print(f"🎤 Source predefinita impostata su: {target_source}")
            
    except Exception as e:
        print(f"⚠️ Impossibile impostare i default di sistema per l'AEC: {e}")

    return dev_id

if __name__ == "__main__":
    pipeline = AudioPipeline()
    
    pipeline.add_block(BandpassFilter(fs=SAMPLE_RATE, enabled=True))
    pipeline.add_block(AGCBlock(target_dbfs=-AGC_TARGET_DBFS, enabled=True))
    pipeline.add_block(SileroVADBlock(vad_threshold=VAD_THRESHOLD, rms_threshold=RMS_THRESHOLD, sample_rate=SAMPLE_RATE, enabled=True))    
    speaker_block = DynamicSpeakerIdentification(threshold=DSI_THRESHOLD, fs=SAMPLE_RATE, enabled=True)
    pipeline.add_block(speaker_block)
    
    if not speaker_block.speakers:
        print("\n" + "="*50)
        print("🎙️ CONFIGURAZIONE INIZIALE: Nessun profilo trovato.")
        name = input("👉 Inserisci il nome del primo utente da registrare: ").strip()
        if not name:
            name = "Utente"
        
        input(f"Premi INVIO e pronuncia una frase continua per 6 secondi...")
        print("🔴 Registrazione in corso...")
        
        raw_audio = sd.rec(int(6.0 * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=CHANNELS, dtype='float32')
        sd.wait()
        
        filtered_audio = raw_audio
        for block in pipeline.blocks[:-2]:
            processed_chunks = []
            for i in range(0, len(filtered_audio), 512):
                chunk = filtered_audio[i:i+512]
                if len(chunk) > 0:
                    res = block.process(chunk.reshape(-1, 1))
                    if len(res) > 0:
                        processed_chunks.append(res.flatten())
            if processed_chunks:
                filtered_audio = np.concatenate(processed_chunks).reshape(-1, 1)

        speaker_block.register_speaker(name, filtered_audio)
        print("="*50 + "\n")

    device_id = ensure_pipewire_aec()
    
    if device_id is not None:
        device_info = sd.query_devices(device_id)
        native_sr = int(device_info['default_samplerate'])
        print(f"✅ Utilizzo dispositivo ID: {device_id} | {native_sr} Hz")
    else:
        native_sr = SAMPLE_RATE
        print("⚠️ Impossibile attivare AEC. Uso il microfono predefinito.")

    native_blocksize = int(TARGET_BLOCK_SIZE * (native_sr / SAMPLE_RATE))

    print(f"🎙️ Avvio stream microfono... (Debug Mode: {'ON' if DEBUG_MODE else 'OFF'})")

    try:
        print_counter = 0
        with sd.InputStream(device=device_id,
                            samplerate=native_sr, 
                            channels=CHANNELS, 
                            blocksize=native_blocksize,
                            dtype='float32',
                            callback=audio_callback):
            
            while True:
                raw_chunk = audio_queue.get()
                
                if native_sr != SAMPLE_RATE:
                    step = int(native_sr / SAMPLE_RATE)
                    chunk_1d = raw_chunk.flatten()
                    raw_chunk = chunk_1d[::step].reshape(-1, 1).astype(np.float32)

                vol = np.max(np.abs(raw_chunk))
                processed_chunk = pipeline.process_stream(raw_chunk)
                
                # Esegue il rendering grafico a schermo solo se avviato con --debug o -d
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