"""
Unified LLM Streaming Service connecting to Ollama, LM Studio, or OpenAI-compatible local APIs.
Does not load heavy LLM weights inside the daemon/extension process.
"""
import json
import logging
import urllib.request
import urllib.error
from typing import Generator, Optional, Dict, Any

logger = logging.getLogger("VoiceAssistant.LLM")

class LLMServiceManager:
    """
    Client for external local LLM services (Ollama, LM Studio, LocalAI).
    Streams text response tokens over HTTP.
    """
    def __init__(self, settings_observer: Optional[Any] = None):
        self.settings_observer = settings_observer

    def get_config(self) -> Dict[str, Any]:
        endpoint = "http://localhost:11434/api/generate"
        model_name = "llama3.2:1b"
        temperature = 0.3
        system_prompt = "Sei un assistente vocale italiano conciso e utile. Rispondi sempre in italiano con frasi brevi e naturali, senza formattazione speciale se non necessaria."

        if self.settings_observer:
            endpoint = self.settings_observer.get("llm-endpoint", endpoint)
            model_name = self.settings_observer.get("llm-model-name", model_name)
            temperature = self.settings_observer.get("llm-temperature", temperature)
            system_prompt = self.settings_observer.get("llm-system-prompt", system_prompt)

        return {
            "endpoint": endpoint,
            "model_name": model_name,
            "temperature": temperature,
            "system_prompt": system_prompt
        }

    def stream_tokens(self, prompt: str) -> Generator[str, None, None]:
        """
        Invia il prompt al server LLM locale e produce un flusso di token in tempo reale.
        """
        config = self.get_config()
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
