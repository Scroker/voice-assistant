"""
Unified LLM Streaming Service supporting both In-Daemon Local GGUF models (via llama-cpp-python)
and External HTTP services (Ollama, LM Studio, OpenAI API).
"""
import os
import json
import logging
import urllib.request
import urllib.error
from typing import Generator, Optional, Dict, Any

logger = logging.getLogger("VoiceAssistant.LLM")

class LocalGGUFProvider:
    """
    In-Daemon LLM Runner for GGUF models using llama-cpp-python and HuggingFace download.
    Executes lightweight GGUF models (e.g. Llama-3.2-1B / 3B) directly within the daemon process
    without requiring Ollama or external services.
    """
    DEFAULT_MODEL_REPO = "bartowski/Llama-3.2-1B-Instruct-GGUF"
    DEFAULT_MODEL_FILE = "Llama-3.2-1B-Instruct-Q4_K_M.gguf"

    def __init__(self, models_dir: Optional[str] = None):
        self.models_dir = models_dir or os.path.expanduser("~/.local/share/voice-assistant/models/llm")
        os.makedirs(self.models_dir, exist_ok=True)
        self._llm = None
        self._loaded_model_path = None

    def ensure_model_downloaded(self, repo_id: str = DEFAULT_MODEL_REPO, filename: str = DEFAULT_MODEL_FILE) -> str:
        """Scarica il file GGUF da HuggingFace se non è presente in locale."""
        model_path = os.path.join(self.models_dir, filename)
        if os.path.exists(model_path) and os.path.getsize(model_path) > 0:
            return model_path

        logger.info(f"[LocalGGUF] Scaricamento del modello GGUF '{filename}' da {repo_id}...")
        try:
            from huggingface_hub import hf_hub_download
            downloaded_path = hf_hub_download(
                repo_id=repo_id,
                filename=filename,
                local_dir=self.models_dir
            )
            return downloaded_path
        except Exception as e:
            logger.error(f"[LocalGGUF] Errore durante il download da HuggingFace: {e}")
            raise e

    def load_model(self, model_file: str = DEFAULT_MODEL_FILE):
        model_path = self.ensure_model_downloaded(filename=model_file)
        if self._llm and self._loaded_model_path == model_path:
            return self._llm

        try:
            from llama_cpp import Llama
            logger.info(f"[LocalGGUF] Caricamento in memoria del modello GGUF: {model_path}")
            self._llm = Llama(
                model_path=model_path,
                n_ctx=2048,
                n_threads=max(1, (os.cpu_count() or 4) - 1),
                verbose=False
            )
            self._loaded_model_path = model_path
            return self._llm
        except ImportError:
            logger.error("[LocalGGUF] Il modulo 'llama-cpp-python' non è ancora installato.")
            raise RuntimeError("Installa 'llama-cpp-python' per eseguire i modelli GGUF direttamente nel demone.")
        except Exception as e:
            logger.error(f"[LocalGGUF] Errore inizializzazione llama-cpp: {e}")
            raise e

    def is_model_present(self, filename: str = DEFAULT_MODEL_FILE) -> bool:
        """Verifica se il file GGUF è già presente sul disco locale."""
        model_path = os.path.join(self.models_dir, filename)
        return os.path.exists(model_path) and os.path.getsize(model_path) > 50000000

    def stream_tokens(self, prompt: str, system_prompt: str = "") -> Generator[str, None, None]:
        if not self.is_model_present():
            logger.info("[LocalGGUF] Primo avvio: il modello GGUF non è presente. Avvio download...")
            yield "Sto scaricando il modello di intelligenza artificiale locale per la prima volta. Attendere prego."

        try:
            llm = self.load_model()
        except Exception as e:
            logger.error(f"[LocalGGUF] Impossibile caricare il modello: {e}")
            yield "Errore nel caricamento del modello locale. Verifica l'installazione delle dipendenze."
            return

        formatted_prompt = f"<|system|>\n{system_prompt}<|end|>\n<|user|>\n{prompt}<|end|>\n<|assistant|>\n"
        
        response_stream = llm.create_completion(
            prompt=formatted_prompt,
            max_tokens=512,
            stop=["<|end|>", "<|user|>", "</s>"],
            stream=True
        )

        for chunk in response_stream:
            text = chunk.get("choices", [{}])[0].get("text", "")
            if text:
                yield text


class LLMServiceManager:
    """
    Manager for LLM Streaming Services. Routes to either In-Daemon Local GGUF or External HTTP (Ollama/LM Studio).
    """
    def __init__(self, settings_observer: Optional[Any] = None):
        self.settings_observer = settings_observer
        self.local_gguf_provider = LocalGGUFProvider()

    def get_config(self) -> Dict[str, Any]:
        mode = "local" # Default: "local" (GGUF in-daemon) or "ollama" / "http"
        endpoint = "http://localhost:11434/api/generate"
        model_name = "Llama-3.2-1B-Instruct-Q4_K_M.gguf"
        temperature = 0.3
        system_prompt = "Sei un assistente vocale italiano conciso e utile. Rispondi sempre in italiano con frasi brevi e naturali, senza formattazione speciale se non necessaria."

        if self.settings_observer:
            mode = self.settings_observer.get("llm-mode", mode)
            endpoint = self.settings_observer.get("llm-endpoint", endpoint)
            model_name = self.settings_observer.get("llm-model-name", model_name)
            temperature = self.settings_observer.get("llm-temperature", temperature)
            system_prompt = self.settings_observer.get("llm-system-prompt", system_prompt)

        return {
            "mode": mode,
            "endpoint": endpoint,
            "model_name": model_name,
            "temperature": temperature,
            "system_prompt": system_prompt
        }

    def stream_tokens(self, prompt: str) -> Generator[str, None, None]:
        """
        Invia il prompt al provider selezionato (Local GGUF in-daemon o Ollama/HTTP)
        e produce un flusso di token in tempo reale.
        """
        config = self.get_config()
        mode = config["mode"]

        # 1. In-Daemon Local GGUF Mode
        if mode == "local":
            try:
                for token in self.local_gguf_provider.stream_tokens(prompt, system_prompt=config["system_prompt"]):
                    yield token
                return
            except Exception as e:
                logger.warning(f"[LLM] Errore esecuzione Local GGUF in-daemon: {e}. Fallback su HTTP/Ollama...")

        # 2. External HTTP / Ollama / LM Studio Mode (Fallback o impostazione utente)
        endpoint = config["endpoint"]
        model_name = config["model_name"]
        
        if "/v1/chat/completions" in endpoint:
            payload = {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": config["system_prompt"]},
                    {"role": "user", "content": prompt}
                ],
                "temperature": config["temperature"],
                "stream": True
            }
        else:
            payload = {
                "model": model_name,
                "prompt": prompt,
                "system": config["system_prompt"],
                "options": {
                    "temperature": config["temperature"]
                },
                "stream": True
            }

        headers = {"Content-Type": "application/json"}
        req_data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(endpoint, data=req_data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                for line in resp:
                    if not line:
                        continue
                    line_str = line.decode('utf-8').strip()
                    if not line_str:
                        continue

                    if line_str.startswith("data: "):
                        line_str = line_str[6:].strip()
                        if line_str == "[DONE]":
                            break

                    try:
                        data = json.loads(line_str)
                        token = ""
                        if "response" in data:
                            token = data["response"]
                        elif "choices" in data and len(data["choices"]) > 0:
                            delta = data["choices"][0].get("delta", {})
                            token = delta.get("content", "")

                        if token:
                            yield token
                    except json.JSONDecodeError:
                        continue

        except urllib.error.URLError as e:
            logger.error(f"[LLM] Errore connessione all'endpoint {endpoint}: {e}")
            yield f"Impossibile connettersi al server LLM locale su {endpoint}."
        except Exception as e:
            logger.error(f"[LLM] Errore durante lo streaming LLM: {e}")
            yield "Si è verificato un errore durante l'elaborazione della risposta."
