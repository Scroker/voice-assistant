import os
import json
import shutil
import urllib.request
import zipfile
try:
    from vosk import Model, KaldiRecognizer
except ImportError:
    Model = None
    KaldiRecognizer = None

from .base import STTProvider

class VoskProvider(STTProvider):
    MODELS_DIR = os.path.expanduser("~/.local/share/voice-assistant/models")
    
    MODEL_MAPPINGS = {
        "it": "vosk-model-small-it-0.22",
        "en": "vosk-model-small-en-us-0.15",
        "small-it": "vosk-model-small-it-0.22",
        "large-it": "vosk-model-it-0.22",
        "small-en": "vosk-model-small-en-us-0.15",
        "large-en": "vosk-model-en-us-0.22",
    }

    def __init__(self, model_name: str, hardware: str, extra: dict, progress_callback=None, models_dir: str = None):
        if models_dir and len(models_dir.strip()) > 0:
            self.MODELS_DIR = os.path.expanduser(models_dir)
        else:
            self.MODELS_DIR = os.path.expanduser("~/.local/share/voice-assistant/models")
        print(f"Inizializzazione VoskProvider con modello '{model_name}' (dir: {self.MODELS_DIR})...")
        
        target_name = self.MODEL_MAPPINGS.get(model_name, model_name)
        if not target_name.startswith("vosk-model-"):
            target_name = f"vosk-model-{target_name}"
            
        model_path = os.path.join(self.MODELS_DIR, target_name)
        old_model_path = os.path.expanduser(f"~/.cache/vosk/{target_name}")
        
        if not os.path.isdir(model_path) and os.path.isdir(old_model_path):
            print(f"Modello trovato nella vecchia posizione ({old_model_path}), uso quello.")
            model_path = old_model_path
            
        self.model = self._load_or_download_model(target_name, model_path, progress_callback)
        self.recognizer = KaldiRecognizer(self.model, 16000)

    def _load_or_download_model(self, target_name: str, model_path: str, progress_callback=None):
        if os.path.exists(model_path) and os.path.isdir(model_path):
            try:
                model = Model(model_path=model_path)
                return model
            except Exception as e:
                print(f"Attenzione: Modello Vosk in '{model_path}' è corrotto o non valido: {e}")
                print(f"Rimuovo la cartella corrotta e procedo al download automatico...")
                shutil.rmtree(model_path, ignore_errors=True)

        return self._download_model(target_name, os.path.join(self.MODELS_DIR, target_name), progress_callback)

    def _download_model(self, target_name: str, target_dir: str, progress_callback=None):
        os.makedirs(self.MODELS_DIR, exist_ok=True)
        url = f"https://alphacephei.com/vosk/models/{target_name}.zip"
        zip_path = os.path.join(self.MODELS_DIR, f"{target_name}.zip")
        
        print(f"Avvio download automatico modello Vosk da {url}...")
        if progress_callback:
            progress_callback(0)
            
        max_retries = 10
        chunk_size = 256 * 1024
        last_pct = -1

        for attempt in range(1, max_retries + 1):
            downloaded = os.path.getsize(zip_path) if os.path.exists(zip_path) else 0
            try:
                headers = {'User-Agent': 'Mozilla/5.0'}
                if downloaded > 0:
                    headers['Range'] = f"bytes={downloaded}-"

                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=30) as response:
                    content_len = int(response.headers.get('Content-Length', 0))
                    if response.status == 206:
                        total_bytes = downloaded + content_len
                    else:
                        total_bytes = content_len
                        downloaded = 0

                    mode = 'ab' if (downloaded > 0 and response.status == 206) else 'wb'
                    with open(zip_path, mode) as f:
                        while True:
                            chunk = response.read(chunk_size)
                            if not chunk:
                                break
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total_bytes > 0 and progress_callback:
                                pct = min(99, max(0, int((downloaded / total_bytes) * 100)))
                                if pct > last_pct:
                                    last_pct = pct
                                    progress_callback(pct)
                break
            except Exception as e:
                print(f"Avviso: Interruzione download Vosk (tentativo {attempt}/{max_retries}): {e}")
                if attempt == max_retries:
                    raise Exception(f"Impossibile scaricare il modello Vosk dopo {max_retries} tentativi: {e}")
                import time
                time.sleep(2)
                
        try:
            print(f"Download completato. Estrazione di '{zip_path}'...")
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(self.MODELS_DIR)
                
            if progress_callback:
                progress_callback(100)
                
            print(f"Estrazione completata. Caricamento modello da '{target_dir}'...")
            return Model(model_path=target_dir)
            
        except Exception as e:
            print(f"Errore durante il download/estrazione del modello Vosk: {e}")
            if os.path.exists(zip_path):
                try: os.remove(zip_path)
                except: pass
            shutil.rmtree(target_dir, ignore_errors=True)
            raise RuntimeError(f"Impossibile scaricare o caricare il modello Vosk '{target_name}': {e}")
        finally:
            if os.path.exists(zip_path):
                try: os.remove(zip_path)
                except: pass

    def process_chunk(self, data: bytes) -> tuple[str, str]:
        if self.recognizer.AcceptWaveform(data):
            res_json = self.recognizer.Result()
            res = json.loads(res_json)
            return res.get("text", "").strip(), ""
        else:
            partial_json = self.recognizer.PartialResult()
            partial = json.loads(partial_json)
            return "", partial.get("partial", "").strip()

    def reset(self):
        self.recognizer.Reset()

    def flush_and_transcribe(self) -> str:
        res_json = self.recognizer.FinalResult()
        res = json.loads(res_json)
        return res.get("text", "").strip()

    @classmethod
    def get_available_models(cls) -> list[dict]:
        url = "https://alphacephei.com/vosk/models/model-list.json"
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=6) as response:
                data = json.loads(response.read().decode('utf-8'))
                models = []
                for item in data:
                    if item.get("obsolete") == "true":
                        continue
                    m_id = item.get("name")
                    m_lang = item.get("lang", "en")
                    m_lang_text = item.get("lang_text", m_lang.upper())
                    m_size = item.get("size_text", "")
                    m_url = item.get("url", f"https://alphacephei.com/vosk/models/{m_id}.zip")
                    m_type = item.get("type", "")
                    
                    models.append({
                        "id": m_id,
                        "name": f"{m_lang_text} - {m_id} ({m_size})" if m_size else f"{m_lang_text} - {m_id}",
                        "lang": m_lang,
                        "lang_text": m_lang_text,
                        "size_text": m_size,
                        "url": m_url,
                        "type": m_type
                    })
                
                def _sort_key(m):
                    lang = m["lang"].lower()
                    if lang == "it": return (0, m["name"])
                    if lang == "en": return (1, m["name"])
                    return (2, m["lang_text"], m["name"])
                
                models.sort(key=_sort_key)
                return models
        except Exception as e:
            print(f"Avviso: Impossibile recuperare la lista modelli Vosk online ({e}). Uso lista locale.")
            return [
                {"id": "vosk-model-small-it-0.22", "name": "Italian - vosk-model-small-it-0.22 (47.4MiB)", "lang": "it", "lang_text": "Italian", "size_text": "47.4MiB", "url": "https://alphacephei.com/vosk/models/vosk-model-small-it-0.22.zip", "type": "small"},
                {"id": "vosk-model-it-0.22", "name": "Italian - vosk-model-it-0.22 (1.2GiB)", "lang": "it", "lang_text": "Italian", "size_text": "1.2GiB", "url": "https://alphacephei.com/vosk/models/vosk-model-it-0.22.zip", "type": "big"},
                {"id": "vosk-model-small-en-us-0.15", "name": "English - vosk-model-small-en-us-0.15 (40MiB)", "lang": "en", "lang_text": "English", "size_text": "40MiB", "url": "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip", "type": "small"},
                {"id": "vosk-model-en-us-0.22", "name": "English - vosk-model-en-us-0.22 (1.8GiB)", "lang": "en", "lang_text": "English", "size_text": "1.8GiB", "url": "https://alphacephei.com/vosk/models/vosk-model-en-us-0.22.zip", "type": "big"}
            ]


