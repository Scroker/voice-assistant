"""
Unified LLM Streaming Service supporting In-Daemon Local GGUF models (via llama-cpp-python),
External HTTP services (Ollama, LM Studio), OpenAI API, and Anthropic Claude API.
"""
import os
import json
import logging
import asyncio
import concurrent.futures
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

        sys_prompt = system_prompt or "Sei un assistente vocale italiano rapido e conciso. Rispondi in massimo 2 frasi brevi e dirette. Non divagare mai."
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": prompt}
        ]

        try:
            response_stream = llm.create_chat_completion(
                messages=messages,
                max_tokens=100,
                temperature=0.3,
                stream=True
            )

            for chunk in response_stream:
                choices = chunk.get("choices", [])
                if choices:
                    delta = choices[0].get("delta", {})
                    token = delta.get("content", "")
                    if token:
                        yield token
        except Exception as e:
            logger.error(f"[LocalGGUF] Errore durante create_chat_completion: {e}")
            yield "Errore nella generazione della risposta."


class LLMServiceManager:
    """
    Manager for LLM Streaming Services.
    Routes to Local GGUF, Ollama, OpenAI (GPT-4o/DeepSeek/Groq), or Anthropic Claude.
    """
    def __init__(self, settings_observer: Optional[Any] = None, mcp_manager: Optional[Any] = None):
        self.settings_observer = settings_observer
        self.mcp_manager = mcp_manager
        self.local_gguf_provider = LocalGGUFProvider()

    def get_config(self) -> Dict[str, Any]:
        mode = "local" # Default: "local" (GGUF in-daemon) or "ollama" / "openai" / "anthropic" / "http"
        endpoint = "http://localhost:11434/api/generate"
        model_name = "Llama-3.2-1B-Instruct-Q4_K_M.gguf"
        temperature = 0.3
        api_key = ""
        system_prompt = "Sei un assistente vocale veloce e conciso. Rispondi SEMPRE in massimo 1 frase breve e diretta (massimo 10 parole). Non spiegare mai il tuo ragionamento, non aggiungere mai preamboli, spiegazioni o saluti."

        if self.settings_observer:
            mode = self.settings_observer.get("llm-mode", mode)
            endpoint = self.settings_observer.get("llm-endpoint", endpoint)
            model_name = self.settings_observer.get("llm-model", model_name)
            temperature = self.settings_observer.get("llm-temperature", temperature)
            system_prompt = self.settings_observer.get("llm-system-prompt", system_prompt)
            api_key = self.settings_observer.get("llm-api-key", api_key)

        # Dynamic System Clock Injection
        import datetime
        now = datetime.datetime.now()
        days_it = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
        months_it = ["", "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno", "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"]
        clock_context = f"Data e ora corrente di sistema: {days_it[now.weekday()]} {now.day} {months_it[now.month]} {now.year}, ore {now.strftime('%H:%M')}."

        system_prompt = f"{system_prompt}\n\n{clock_context}"

        if self.mcp_manager and self.mcp_manager.enabled:
            mcp_tools_prompt = self.mcp_manager.format_system_prompt_tools()
            if mcp_tools_prompt:
                system_prompt = f"{system_prompt}\n\n{mcp_tools_prompt}"

        return {
            "mode": mode,
            "endpoint": endpoint,
            "model_name": model_name,
            "temperature": temperature,
            "api_key": api_key,
            "system_prompt": system_prompt
        }

    def _parse_tool_call(self, text: str) -> Optional[Dict[str, Any]]:
        """Parses JSON tool call emitted by LLM in response text."""
        if not text:
            return None
        text_clean = text.strip()
        if text_clean.startswith("{") and text_clean.endswith("}"):
            try:
                data = json.loads(text_clean)
                if "tool" in data:
                    return data
            except Exception:
                pass

        import re
        match = re.search(r'```(?:json)?\s*(\{\s*"tool"\s*:.*?\})\s*```', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except Exception:
                pass

        match_inline = re.search(r'(\{\s*"tool"\s*:\s*"[^"]+"\s*(?:,\s*"args"\s*:\s*\{.*?\})?\s*\})', text, re.DOTALL)
        if match_inline:
            try:
                return json.loads(match_inline.group(1))
            except Exception:
                pass

        return None

    def _execute_tool_sync(self, tool_name: str, args: Dict[str, Any]) -> str:
        """Executes an MCP tool synchronously, handling nested asyncio event loops cleanly."""
        if not self.mcp_manager:
            return ""

        coro = self.mcp_manager.execute_tool(tool_name, args)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(lambda: asyncio.run(coro))
                return future.result()
        else:
            return asyncio.run(coro)

    def stream_tokens(self, prompt: str) -> Generator[str, None, None]:
        """
        Invia il prompt al provider selezionato (Local GGUF, Ollama, OpenAI API, Anthropic API)
        e produce un flusso di token in tempo reale, eseguendo eventuali tool call MCP.
        """
        config = self.get_config()
        mode = config["mode"]
        accumulated_tokens = []

        def _yield_and_track(token: str):
            accumulated_tokens.append(token)
            return token

        # 1. In-Daemon Local GGUF Mode
        if mode == "local":
            try:
                for token in self.local_gguf_provider.stream_tokens(prompt, system_prompt=config["system_prompt"]):
                    yield _yield_and_track(token)
                
                full_resp = "".join(accumulated_tokens)
                tool_call = self._parse_tool_call(full_resp)
                if tool_call and self.mcp_manager:
                    tool_name = tool_call.get("tool")
                    args = tool_call.get("args", {})
                    try:
                        res = self._execute_tool_sync(tool_name, args)
                        yield f"\n{res}"
                    except Exception as e:
                        logger.error(f"Errore durante l'esecuzione del tool {tool_name}: {e}")
                return
            except Exception as e:
                logger.warning(f"[LLM] Errore esecuzione Local GGUF in-daemon: {e}. Fallback su HTTP/Ollama...")

        # 2. Anthropic Claude API Mode
        if mode == "anthropic" or "anthropic.com" in config["endpoint"]:
            endpoint = config["endpoint"] if "anthropic.com" in config["endpoint"] else "https://api.anthropic.com/v1/messages"
            model_name = config["model_name"] if config["model_name"] and not config["model_name"].endswith(".gguf") else "claude-3-5-sonnet-20241022"
            headers = {
                "Content-Type": "application/json",
                "x-api-key": config["api_key"],
                "anthropic-version": "2023-06-01"
            }
            payload = {
                "model": model_name,
                "max_tokens": 300,
                "system": config["system_prompt"],
                "messages": [{"role": "user", "content": prompt}],
                "stream": True
            }
            req_data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(endpoint, data=req_data, headers=headers, method="POST")

            try:
                with urllib.request.urlopen(req, timeout=10.0) as resp:
                    for line in resp:
                        if not line: continue
                        line_str = line.decode('utf-8').strip()
                        if not line_str or not line_str.startswith("data: "): continue
                        line_str = line_str[6:].strip()
                        try:
                            data = json.loads(line_str)
                            if data.get("type") == "content_block_delta":
                                delta = data.get("delta", {})
                                token = delta.get("text", "")
                                if token:
                                    yield _yield_and_track(token)
                        except json.JSONDecodeError:
                            continue
            except Exception as e:
                logger.error(f"[LLM] Errore streaming Anthropic API: {e}")
                yield f"Errore durante la chiamata ad Anthropic API: {e}"
            return

        # 3. External HTTP / Ollama / OpenAI API Mode
        endpoint = config["endpoint"]
        model_name = config["model_name"]
        
        headers = {"Content-Type": "application/json"}
        if config["api_key"]:
            headers["Authorization"] = f"Bearer {config['api_key']}"

        if mode == "openai" or "openai.com" in endpoint or "/v1/chat/completions" in endpoint:
            if mode == "openai" and "localhost" in endpoint:
                endpoint = "https://api.openai.com/v1/chat/completions"
                model_name = model_name if model_name and not model_name.endswith(".gguf") else "gpt-4o-mini"
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
                        elif "message" in data and isinstance(data["message"], dict):
                            token = data["message"].get("content", "")
                        elif "choices" in data and len(data["choices"]) > 0:
                            delta = data["choices"][0].get("delta", {})
                            token = delta.get("content", "")

                        if token:
                            yield _yield_and_track(token)
                    except json.JSONDecodeError:
                        continue

            full_resp = "".join(accumulated_tokens)
            tool_call = self._parse_tool_call(full_resp)
            if tool_call and self.mcp_manager:
                tool_name = tool_call.get("tool")
                args = tool_call.get("args", {})
                try:
                    res = self._execute_tool_sync(tool_name, args)
                    yield f"\n{res}"
                except Exception as e:
                    logger.error(f"Errore durante l'esecuzione del tool {tool_name}: {e}")

        except urllib.error.URLError as e:
            logger.error(f"[LLM] Errore connessione all'endpoint {endpoint}: {e}")
            yield f"Impossibile connettersi al server LLM su {endpoint}."
        except Exception as e:
            logger.error(f"[LLM] Errore durante lo streaming LLM: {e}")
            yield "Si è verificato un errore durante l'elaborazione della risposta."
