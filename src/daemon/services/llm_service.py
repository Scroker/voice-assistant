"""
Unified LLM Streaming Service supporting In-Daemon Local GGUF models (via llama-cpp-python),
External HTTP services (Ollama, LM Studio), OpenAI API, and Anthropic Claude API.
"""
import os
import re
import json
import datetime
import logging
import urllib.request
import urllib.parse
import urllib.error
from typing import Generator, Optional, Dict, Any, List

from core.async_bridge import run_async
from core.data_loader import load_json_data, load_text_data
from core.locale_utils import get_system_language

logger = logging.getLogger("VoiceAssistant.LLM")

def fetch_huggingface_models(query: str = "", limit: int = 100) -> list:
    """
    Effettua una query alle API REST di Hugging Face per cercare o elencare i modelli GGUF più popolari.
    """
    base_url = "https://huggingface.co/api/models?filter=gguf&sort=downloads&direction=-1&limit=" + str(limit)
    if query.strip():
        base_url += f"&search={urllib.parse.quote(query.strip())}"

    models = []
    seen_ids = set()

    try:
        req = urllib.request.Request(base_url, headers={"User-Agent": "Mozilla/5.0 VoiceAssistant/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            for item in data:
                repo_id = item.get("id") or item.get("modelId")
                if not repo_id:
                    continue
                repo_name = repo_id.split("/")[-1]
                clean_name = repo_name.replace("-GGUF", "").replace("-gguf", "")
                filename = f"{clean_name}-Q4_K_M.gguf"
                model_id = f"{repo_id}:{filename}"
                if model_id not in seen_ids and filename not in seen_ids:
                    seen_ids.add(model_id)
                    downloads = item.get("downloads", 0)
                    likes = item.get("likes", 0)
                    models.append({
                        "id": model_id,
                        "provider": "llm",
                        "name": repo_id,
                        "subtitle": f"Hugging Face • {downloads:,} downloads • {likes} likes",
                        "repo": repo_id,
                        "file": filename,
                        "size_text": "GGUF",
                        "url": f"https://huggingface.co/{repo_id}/resolve/main/{filename}"
                    })
    except Exception as e:
        logger.warning(f"Impossibile interrogare Hugging Face API: {e}")

    return models

# File già verificati come completi in questo processo: (percorso, dimensione).
# `load_model()` invoca il controllo a ogni richiesta all'LLM, quindi senza cache si
# pagherebbe una richiesta HTTP di verifica per ogni singola domanda dell'utente.
_VERIFIED_COMPLETE: set = set()


PART_MARKER_SUFFIX = ".part"


def _write_part_marker(target_path: str, total_size: int) -> None:
    """Segnala che il file è un download incompleto, annotando la dimensione attesa.

    Permette di riconoscere un download interrotto senza rete e impedisce a
    `ModelRegistry.reconcile_with_disk()` di censire il file parziale come modello
    installato (era la causa di modelli "installati" che non si caricavano).
    """
    try:
        with open(target_path + PART_MARKER_SUFFIX, "w", encoding="utf-8") as f:
            f.write(str(int(total_size)))
    except Exception as e:
        logger.warning(f"[download_llm_model] Impossibile scrivere il marcatore di download parziale: {e}")


def _read_part_marker(target_path: str) -> Optional[int]:
    """Dimensione attesa annotata da un download interrotto, se presente."""
    try:
        with open(target_path + PART_MARKER_SUFFIX, "r", encoding="utf-8") as f:
            value = f.read().strip()
        return int(value) if value.isdigit() else None
    except FileNotFoundError:
        return None
    except Exception:
        return None


def _clear_part_marker(target_path: str) -> None:
    try:
        os.remove(target_path + PART_MARKER_SUFFIX)
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning(f"[download_llm_model] Impossibile rimuovere il marcatore di download parziale: {e}")


def _remote_content_length(url: str) -> Optional[int]:
    """Dimensione reale del file remoto, o None se non determinabile (offline, server muto)."""
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0 VoiceAssistant/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            # Hugging Face espone la dimensione reale del file LFS in x-linked-size;
            # Content-Length dopo il redirect vale per gli altri host.
            for header in ("x-linked-size", "Content-Length"):
                value = resp.headers.get(header)
                if value and str(value).isdigit():
                    return int(value)
    except Exception as e:
        logger.warning(f"[download_llm_model] Impossibile determinare la dimensione remota di {url}: {e}")
    return None


def _lookup_catalog_id(filename: str) -> Optional[str]:
    """Cerca il repo:filename originale di un modello GGUF già registrato come installato.

    `llm-model` viene salvato come nome file "nudo" (vedi model_selector.py), quindi il
    repository di origine va recuperato altrove: prima dal catalogo curato (sempre
    disponibile, indipendente da cosa è installato), poi dal registro dei modelli, che
    conserva il repo:filename in extra.catalog_id per i modelli importati manualmente.
    """
    try:
        from services.catalog_manager import resolve_gguf_catalog_id
        catalog_id = resolve_gguf_catalog_id(filename)
        if catalog_id:
            return catalog_id
    except Exception as e:
        logger.warning(f"[download_llm_model] Catalogo GGUF non consultabile per {filename}: {e}")

    try:
        from core.model_registry import get_model_registry
        registry = get_model_registry()
        for entry in registry.get_installed_models("gguf"):
            if entry.get("id") == filename or entry.get("filename") == filename or (entry.get("extra") or {}).get("filename") == filename:
                catalog_id = (entry.get("extra") or {}).get("catalog_id")
                if catalog_id:
                    return catalog_id
    except Exception as e:
        logger.warning(f"[download_llm_model] Impossibile consultare model_registry per {filename}: {e}")
    return None

def download_llm_model(model_name: str, progress_callback=None, models_dir: Optional[str] = None) -> str:
    models_dir = models_dir or os.path.expanduser("~/.local/share/voice-assistant/models/llm")
    os.makedirs(models_dir, exist_ok=True)

    repo = None
    filename = None
    url = None

    if ":" in model_name:
        repo, filename = model_name.split(":", 1)
        url = f"https://huggingface.co/{repo}/resolve/main/{filename}"
    elif "/" in model_name:
        parts = model_name.split("/")
        if len(parts) >= 2 and parts[-1].endswith(".gguf"):
            repo = "/".join(parts[:-1])
            filename = parts[-1]
            url = f"https://huggingface.co/{repo}/resolve/main/{filename}"
        elif len(parts) == 2:
            repo = model_name
            try:
                tree_url = f"https://huggingface.co/api/models/{repo}/tree/main"
                t_req = urllib.request.Request(tree_url, headers={"User-Agent": "Mozilla/5.0 VoiceAssistant/1.0"})
                with urllib.request.urlopen(t_req, timeout=5) as t_resp:
                    tree_data = json.loads(t_resp.read().decode("utf-8"))
                    gguf_files = [f for f in tree_data if isinstance(f, dict) and f.get("path", "").endswith(".gguf")]
                    if gguf_files:
                        q4 = next((f for f in gguf_files if "q4_k_m" in f["path"].lower() or "q4_0" in f["path"].lower()), gguf_files[0])
                        filename = q4["path"]
                        url = f"https://huggingface.co/{repo}/resolve/main/{filename}"
            except Exception as e:
                logger.warning(f"Errore ispezione repo tree Hugging Face per {repo}: {e}")

    if not url:
        # `model_name` è un nome file "nudo" (senza repo): prima di tentare un guess
        # sull'org HuggingFace, verifica se il registro dei modelli installati conosce
        # già il repo di origine reale (salvato come extra.catalog_id al primo download).
        # Senza questo controllo, un modello pubblicato fuori dall'org "bartowski"
        # (es. Qwen/Qwen2.5-3B-Instruct-GGUF) genera sempre un URL errato -> HTTP 401.
        catalog_id = _lookup_catalog_id(model_name)
        if catalog_id and ":" in catalog_id:
            repo, filename = catalog_id.split(":", 1)
            url = f"https://huggingface.co/{repo}/resolve/main/{filename}"

    if not url:
        repo = f"bartowski/{model_name.replace('.gguf', '')}-GGUF" if "/" not in model_name else model_name
        filename = model_name if model_name.endswith(".gguf") else f"{model_name}.gguf"
        url = f"https://huggingface.co/{repo}/resolve/main/{filename}"

    target_path = os.path.join(models_dir, filename)
    existing_size = os.path.getsize(target_path) if os.path.exists(target_path) else 0

    def _report(pct: int):
        if not progress_callback:
            return
        try:
            import inspect
            sig = inspect.signature(progress_callback)
            params = [p for p in sig.parameters.values() if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)]
            if len(params) >= 2:
                progress_callback(model_name, pct)
            else:
                progress_callback(pct)
        except Exception:
            try:
                progress_callback(pct)
            except TypeError:
                progress_callback(model_name, pct)

    # Un file già presente è considerato completo solo se la sua dimensione coincide con
    # quella reale del file remoto. L'euristica precedente ("più di 10 MB = completo")
    # dichiarava completo qualunque download interrotto oltre quella soglia: llama.cpp
    # riceveva un GGUF troncato e il download non veniva mai ripreso.
    if existing_size > 0:
        if (target_path, existing_size) in _VERIFIED_COMPLETE:
            _report(100)
            return target_path

        # Il marcatore lasciato da un download interrotto dice la dimensione attesa senza
        # bisogno della rete; solo in sua assenza (file legacy) si interroga il server.
        expected_size = _read_part_marker(target_path) or _remote_content_length(url)
        if expected_size and existing_size >= expected_size:
            _clear_part_marker(target_path)
            _VERIFIED_COMPLETE.add((target_path, existing_size))
            _report(100)
            return target_path

        if expected_size:
            logger.info(
                f"[download_llm_model] {filename} incompleto ({existing_size}/{expected_size} byte): ripresa del download..."
            )
        elif existing_size > 10_000_000:
            # Dimensione remota non verificabile (offline o server muto): non blocchiamo
            # l'uso di un modello plausibilmente già completo.
            logger.warning(
                f"[download_llm_model] Impossibile verificare la dimensione remota di {filename}: "
                f"uso il file locale esistente ({existing_size} byte)."
            )
            _report(100)
            return target_path

    logger.info(f"[download_llm_model] Download {filename} da {repo} ({url}), offset={existing_size}B...")
    headers = {
        "User-Agent": "Mozilla/5.0 VoiceAssistant/1.0",
    }
    if existing_size > 0:
        headers["Range"] = f"bytes={existing_size}-"

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req) as resp:
            # 206 Partial Content → resume; 200 → ricominciamo da capo
            if resp.status == 200 and existing_size > 0:
                existing_size = 0

            total_len = resp.headers.get("Content-Range") or resp.headers.get("Content-Length")
            if resp.headers.get("Content-Range"):
                # Content-Range: bytes 0-N/TOTAL
                total_size = int(resp.headers["Content-Range"].split("/")[-1])
            else:
                total_size = int(total_len) if total_len else 0

            if total_size > 0:
                _write_part_marker(target_path, total_size)

            downloaded = existing_size
            block_size = 1024 * 1024
            mode = "ab" if existing_size > 0 else "wb"
            with open(target_path, mode) as f:
                while True:
                    buffer = resp.read(block_size)
                    if not buffer:
                        break
                    downloaded += len(buffer)
                    f.write(buffer)
                    if total_size > 0 and progress_callback:
                        pct = int((downloaded / total_size) * 100)
                        _report(min(99, max(0, pct)))

        if total_size > 0 and downloaded < total_size:
            # Il server ha chiuso la connessione prima di inviare tutti i byte attesi.
            # Senza questo controllo un download troncato veniva considerato riuscito
            # e registrato come modello "installato" (file corrotto/incompleto).
            raise IOError(
                f"Download incompleto: ricevuti {downloaded}/{total_size} byte per {filename}"
            )

        _clear_part_marker(target_path)
        _VERIFIED_COMPLETE.add((target_path, downloaded))
        _report(100)
        return target_path
    except Exception as e:
        logger.error(f"[download_llm_model] Errore download HTTP: {e}")
        # Non elimina il file parziale: il prossimo avvio riprenderà dal punto interrotto
        raise e

class LocalGGUFProvider:
    """
    In-Daemon LLM Runner for GGUF models using llama-cpp-python and HuggingFace download.
    Executes lightweight GGUF models (e.g. Llama-3.2-1B / 3B) directly within the daemon process
    without requiring Ollama or external services.
    """
    DEFAULT_MODEL_REPO = "bartowski/Llama-3.2-1B-Instruct-GGUF"
    DEFAULT_MODEL_FILE = "Llama-3.2-1B-Instruct-Q4_K_M.gguf"

    def __init__(self, models_dir: Optional[str] = None, model_manager: Optional[Any] = None):
        self.models_dir = models_dir or os.path.expanduser("~/.local/share/voice-assistant/models/llm")
        os.makedirs(self.models_dir, exist_ok=True)
        self.model_manager = model_manager
        self._llm = None
        self._loaded_model_path = None

    def ensure_model_downloaded(self, repo_id: str = DEFAULT_MODEL_REPO, filename: str = DEFAULT_MODEL_FILE) -> str:
        """Scarica il file GGUF da HuggingFace se non è presente in locale."""
        model_identifier = f"{repo_id}:{filename}" if (repo_id and "/" in repo_id) else filename
        return download_llm_model(model_identifier, models_dir=self.models_dir)

    def load_model(self, model_file: str = DEFAULT_MODEL_FILE, repo_id: str = DEFAULT_MODEL_REPO):
        model_path = self.ensure_model_downloaded(repo_id=repo_id, filename=model_file)
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
            if self.model_manager:
                self.model_manager.register_instance("llm", self, self.unload_model)
            return self._llm
        except ImportError:
            logger.error("[LocalGGUF] Il modulo 'llama-cpp-python' non è ancora installato.")
            raise RuntimeError("Installa 'llama-cpp-python' per eseguire i modelli GGUF direttamente nel demone.")
        except Exception as e:
            logger.error(f"[LocalGGUF] Errore inizializzazione llama-cpp: {e}")
            raise e

    def unload_model(self):
        """Release the in-process llama.cpp handle while retaining the model file on disk."""
        self._llm = None
        self._loaded_model_path = None

    def is_model_present(self, filename: str = DEFAULT_MODEL_FILE) -> bool:
        """Verifica se il file GGUF è già presente sul disco locale."""
        model_path = os.path.join(self.models_dir, filename)
        return os.path.exists(model_path) and os.path.getsize(model_path) > 50000000

    def stream_tokens(self, prompt: str, system_prompt: str = "", model_name: Optional[str] = None) -> Generator[str, None, None]:
        if self.model_manager:
            self.model_manager.update_active_timestamp()

        target_file = self.DEFAULT_MODEL_FILE
        target_repo = self.DEFAULT_MODEL_REPO
        if model_name:
            if ":" in model_name:
                target_repo, target_file = model_name.split(":", 1)
            elif "/" in model_name:
                parts = model_name.split("/")
                if len(parts) >= 2 and parts[-1].endswith(".gguf"):
                    target_repo = "/".join(parts[:-1])
                    target_file = parts[-1]
                else:
                    target_repo = model_name
            elif model_name.endswith((".gguf", ".bin")):
                target_file = model_name
                target_repo = ""

        if not self.is_model_present(filename=target_file):
            logger.info(f"[LocalGGUF] Primo avvio: il modello GGUF {target_file} non è presente. Avvio download...")
            yield "Sto scaricando il modello di intelligenza artificiale locale per la prima volta. Attendere prego."

        try:
            llm = self.load_model(model_file=target_file, repo_id=target_repo)
        except Exception as e:
            logger.error(f"[LocalGGUF] Impossibile caricare il modello {target_file}: {e}")
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


def format_clock_context(lang: str = "", now: Optional[datetime.datetime] = None) -> str:
    """Formatta la data e l'ora corrente in base ai template in data/locales/date_time_formats.json."""
    if now is None:
        now = datetime.datetime.now()

    if not lang:
        try:
            lang = get_system_language(default="it")
        except Exception:
            lang = "it"

    formats = load_json_data("locales/date_time_formats.json", fallback_default={}) or {}

    locale_data = (
        formats.get(lang)
        or formats.get(lang.split("_")[0])
        or formats.get("it")
        or formats.get("default")
        or {}
    )

    days = locale_data.get("days", [
        "Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"
    ])
    months = locale_data.get("months", [
        "", "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno",
        "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"
    ])
    template = locale_data.get(
        "clock_template",
        "Data e ora corrente di sistema: {day_name} {day} {month_name} {year}, ore {time}."
    )

    day_idx = now.weekday()
    month_idx = now.month

    day_name = days[day_idx] if 0 <= day_idx < len(days) else str(day_idx)
    month_name = months[month_idx] if 0 <= month_idx < len(months) else str(month_idx)
    time_str = now.strftime("%H:%M")

    try:
        return template.format(
            day_name=day_name,
            day=now.day,
            month_name=month_name,
            year=now.year,
            time=time_str
        )
    except Exception as e:
        logger.warning(f"Errore formattazione template orologio: {e}")
        return f"Data e ora corrente di sistema: {now.strftime('%Y-%m-%d %H:%M')}."


def load_default_providers() -> Dict[str, Any]:
    """Carica i provider LLM predefiniti da data/llm/default_providers.json."""
    return load_json_data("llm/default_providers.json", fallback_default={}) or {}


def normalize_endpoint(endpoint: str, mode: str = "") -> str:
    """Normalizza gli endpoint noti ai relativi percorsi chat completions standard."""
    ep = (endpoint or "").strip().rstrip("/")
    if not ep:
        return ep
    if ep in ("http://localhost:11434", "http://127.0.0.1:11434"):
        return f"{ep}/v1/chat/completions"
    if ep == "https://api.openai.com":
        return f"{ep}/v1/chat/completions"
    if ep == "https://api.deepseek.com":
        return f"{ep}/v1/chat/completions"
    if ep in ("https://ollama.com", "https://ollama.com/v1"):
        return f"{ep}/chat/completions" if ep.endswith("/v1") else f"{ep}/v1/chat/completions"
    if ep == "https://api.groq.com/openai":
        return f"{ep}/v1/chat/completions"
    return ep


def apply_prompt_template(
    template: str,
    clock_context: str = "",
    mcp_tools_prompt: str = "",
    context: str = ""
) -> str:
    """Interpola o appende i contesti di orologio, tool MCP e memoria nel prompt di sistema."""
    prompt = template
    has_clock = "{clock}" in prompt
    has_tools = "{tools_definition}" in prompt
    has_context = "{context}" in prompt

    if has_clock:
        prompt = prompt.replace("{clock}", clock_context.strip())
    if has_tools:
        prompt = prompt.replace("{tools_definition}", mcp_tools_prompt.strip())
    if has_context:
        prompt = prompt.replace("{context}", context.strip())

    if not has_clock and clock_context.strip():
        prompt = f"{prompt.strip()}\n\n{clock_context.strip()}"
    if not has_tools and mcp_tools_prompt.strip():
        prompt = f"{prompt.strip()}\n\n{mcp_tools_prompt.strip()}"
    if not has_context and context.strip():
        prompt = f"{prompt.strip()}\n\n{context.strip()}"

    prompt = re.sub(r'\n{3,}', '\n\n', prompt).strip()
    return prompt


def _is_legacy_default_prompt(prompt: str) -> bool:
    """Riconosce i vecchi prompt hardcoded nel codice sorgente e schemi per favorire il nuovo prompt universale."""
    if not prompt:
        return False
    p = prompt.strip()
    legacy_defaults = [
        "Sei un assistente vocale integrato per GNOME Shell. Rispondi in modo breve, amichevole e preciso in lingua italiana.",
        "Sei un assistente vocale rapido e conciso. Rispondi in massimo 2 frasi brevi e dirette. Non divagare mai.",
        "Sei un assistente vocale veloce e conciso. Rispondi SEMPRE in massimo 1 frase breve e diretta (massimo 10 parole). Non spiegare mai il tuo ragionamento, non aggiungere mai preamboli, spiegazioni o saluti."
    ]
    return p in legacy_defaults


class OpenAICompatibleClient:
    """
    Client unificato per endpoint compatibili con OpenAI (/v1/chat/completions).
    Supporta streaming SSE di token, chiamate native tool_calls, e fallback
    per streaming legacy JSON-per-line (ad es. Ollama /api/generate).
    """
    def __init__(
        self,
        endpoint: str,
        api_key: str = "",
        model_name: str = "",
        temperature: float = 0.3,
        api_key_header: str = "Authorization",
        api_key_prefix: str = "Bearer ",
        timeout: float = 10.0,
    ):
        self.endpoint = endpoint
        self.api_key = api_key
        self.model_name = model_name
        self.temperature = temperature
        self.api_key_header = api_key_header or "Authorization"
        self.api_key_prefix = api_key_prefix if api_key_prefix is not None else "Bearer "
        self.timeout = timeout

    def build_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key and self.api_key_header:
            headers[self.api_key_header] = f"{self.api_key_prefix}{self.api_key}"
        return headers

    def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        headers = self.build_headers()
        is_ollama_legacy = "/api/generate" in self.endpoint or (
            "/api/chat" not in self.endpoint
            and "/v1" not in self.endpoint
            and ":11434" in self.endpoint
            and not self.endpoint.endswith("/completions")
        )

        payload: Dict[str, Any] = {
            "model": self.model_name,
            "stream": True,
        }

        if is_ollama_legacy:
            sys_msg = next((m["content"] for m in messages if m.get("role") == "system"), "")
            usr_msg = next((m["content"] for m in messages if m.get("role") == "user"), "")
            payload["prompt"] = usr_msg
            if sys_msg:
                payload["system"] = sys_msg
            payload["options"] = {"temperature": self.temperature}
        else:
            payload["messages"] = messages
            payload["temperature"] = self.temperature
            if tools:
                payload["tools"] = tools
                payload["tool_choice"] = "auto"

        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.endpoint, data=req_data, headers=headers, method="POST")

        accumulated_tool_calls: Dict[int, Dict[str, Any]] = {}

        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            for line in resp:
                if not line:
                    continue
                line_str = line.decode("utf-8").strip()
                if not line_str:
                    continue

                if line_str.startswith("data: "):
                    line_str = line_str[6:].strip()
                    if line_str == "[DONE]":
                        break

                try:
                    data = json.loads(line_str)
                except json.JSONDecodeError:
                    continue

                # 1. Fallback Ollama legacy /api/generate
                if "response" in data:
                    token = data.get("response", "")
                    if token:
                        yield {"type": "token", "content": token}
                    continue

                # 2. Fallback Ollama /api/chat
                if "message" in data and isinstance(data["message"], dict):
                    msg = data["message"]
                    token = msg.get("content", "")
                    if token:
                        yield {"type": "token", "content": token}
                    if "tool_calls" in msg and msg["tool_calls"]:
                        for tc in msg["tool_calls"]:
                            fn = tc.get("function", {})
                            name = fn.get("name")
                            args = fn.get("arguments", {})
                            if isinstance(args, str):
                                try:
                                    args = json.loads(args)
                                except Exception:
                                    args = {}
                            if name:
                                yield {"type": "tool_call", "tool": name, "args": args}
                    continue

                # 3. Standard OpenAI /v1/chat/completions format
                choices = data.get("choices", [])
                if choices:
                    delta = choices[0].get("delta", {})
                    token = delta.get("content", "")
                    if token:
                        yield {"type": "token", "content": token}

                    tool_calls_chunk = delta.get("tool_calls", [])
                    for tc in tool_calls_chunk:
                        idx = tc.get("index", 0)
                        if idx not in accumulated_tool_calls:
                            accumulated_tool_calls[idx] = {
                                "name": "",
                                "arguments_str": ""
                            }
                        fn = tc.get("function", {})
                        if "name" in fn and fn["name"]:
                            accumulated_tool_calls[idx]["name"] += fn["name"]
                        if "arguments" in fn and fn["arguments"]:
                            accumulated_tool_calls[idx]["arguments_str"] += fn["arguments"]

        # Flush accumulated native tool calls
        for idx, tc_data in accumulated_tool_calls.items():
            name = tc_data.get("name", "")
            arg_str = tc_data.get("arguments_str", "")
            args = {}
            if arg_str:
                try:
                    args = json.loads(arg_str)
                except Exception:
                    logger.warning(f"Impossibile parsare argomenti tool '{name}': {arg_str}")
            if name:
                yield {"type": "tool_call", "tool": name, "args": args}


class LLMServiceManager:
    """
    Manager per servizi streaming LLM unificati.
    Gestisce Local GGUF (in-daemon llama.cpp), Anthropic API, e il client unificato
    OpenAI-compatibile (Ollama, OpenAI, Groq, DeepSeek, server HTTP locali).
    """
    def __init__(self, settings_observer: Optional[Any] = None, mcp_manager: Optional[Any] = None,
                 model_manager: Optional[Any] = None):
        self.settings_observer = settings_observer
        self.mcp_manager = mcp_manager
        self.local_gguf_provider = LocalGGUFProvider(model_manager=model_manager)

    def get_config(self) -> Dict[str, Any]:
        providers_data = load_default_providers()

        mode = "local"
        if self.settings_observer:
            mode = self.settings_observer.get("llm-mode", mode)

        provider_cfg = providers_data.get(mode, {})
        default_endpoint = provider_cfg.get("endpoint", "http://localhost:11434/v1/chat/completions")
        default_model = provider_cfg.get("default_model", "Llama-3.2-1B-Instruct-Q4_K_M.gguf")
        default_temp = provider_cfg.get("temperature", 0.3)
        api_key_header = provider_cfg.get("api_key_header", "Authorization")
        api_key_prefix = provider_cfg.get("api_key_prefix", "Bearer ")

        endpoint = default_endpoint
        model_name = default_model
        temperature = default_temp
        api_key = ""

        # Caricamento del prompt di sistema universale da file esterno
        base_system_prompt = load_text_data("prompts/system_prompt.md", fallback_default="").strip()
        if not base_system_prompt:
            base_system_prompt = (
                "You are a voice assistant integrated into the GNOME desktop environment.\n\n"
                "{tools_definition}\n\n{context}\n\n{clock}"
            )
        system_prompt = base_system_prompt

        if self.settings_observer:
            mode = self.settings_observer.get("llm-mode", mode)
            endpoint = self.settings_observer.get("llm-endpoint", endpoint)
            model_name = self.settings_observer.get("llm-model", model_name)
            temperature = self.settings_observer.get("llm-temperature", temperature)
            custom_prompt = self.settings_observer.get("llm-system-prompt", "")
            if custom_prompt and custom_prompt.strip() and not _is_legacy_default_prompt(custom_prompt):
                system_prompt = custom_prompt.strip()
            api_key = self.settings_observer.get("llm-api-key", api_key)

        if mode in ("openai", "anthropic", "deepseek", "ollama_cloud", "custom"):
            try:
                from core.cloud_config import get_cloud_config
                c_cfg = get_cloud_config().get_provider_config("llm", mode)
                if c_cfg.get("api_key"):
                    api_key = c_cfg["api_key"]
                if c_cfg.get("endpoint"):
                    endpoint = c_cfg["endpoint"]
                if c_cfg.get("model"):
                    model_name = c_cfg["model"]
            except Exception as e:
                logger.debug("Errore lettura cloud_config in LLMService: %s", e)

        endpoint = normalize_endpoint(endpoint, mode=mode)

        if mode != "local" and model_name.endswith(".gguf") and provider_cfg.get("default_model"):
            model_name = provider_cfg["default_model"]

        # Iniezione dinamica orologio localizzato
        lang = get_system_language(default="it")
        clock_context = format_clock_context(lang=lang)

        # Iniezione definizioni tool MCP
        mcp_tools_prompt = ""
        if self.mcp_manager and self.mcp_manager.enabled:
            mcp_tools_prompt = self.mcp_manager.format_system_prompt_tools() or ""

        system_prompt = apply_prompt_template(
            system_prompt,
            clock_context=clock_context,
            mcp_tools_prompt=mcp_tools_prompt,
            context=""
        )

        return {
            "mode": mode,
            "endpoint": endpoint,
            "model_name": model_name,
            "temperature": temperature,
            "api_key": api_key,
            "api_key_header": api_key_header,
            "api_key_prefix": api_key_prefix,
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
        """Executes an MCP tool synchronously via the shared background event loop."""
        if not self.mcp_manager:
            return ""
        return run_async(self.mcp_manager.execute_tool(tool_name, args))

    def _stream_anthropic(self, config: Dict[str, Any], prompt: str, _yield_and_track: Any) -> Generator[str, None, None]:
        """Streaming per Anthropic Claude Messages API."""
        endpoint = config["endpoint"] if "anthropic.com" in config["endpoint"] else "https://api.anthropic.com/v1/messages"
        model_name = config["model_name"] if config["model_name"] and not config["model_name"].endswith(".gguf") else "claude-haiku-4-5-20251001"
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

        accumulated_tokens = []
        try:
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                for line in resp:
                    if not line:
                        continue
                    line_str = line.decode('utf-8').strip()
                    if not line_str or not line_str.startswith("data: "):
                        continue
                    line_str = line_str[6:].strip()
                    try:
                        data = json.loads(line_str)
                        if data.get("type") == "content_block_delta":
                            delta = data.get("delta", {})
                            token = delta.get("text", "")
                            if token:
                                accumulated_tokens.append(token)
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

        except Exception as e:
            logger.error(f"[LLM] Errore streaming Anthropic API: {e}")
            yield f"Errore durante la chiamata ad Anthropic API: {e}"

    def stream_tokens(self, prompt: str) -> Generator[str, None, None]:
        """
        Invia il prompt al provider selezionato (Local GGUF, Anthropic API, o client unificato OpenAI-compatibile)
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
                try:
                    stream_gen = self.local_gguf_provider.stream_tokens(
                        prompt,
                        system_prompt=config["system_prompt"],
                        model_name=config.get("model_name"),
                    )
                except TypeError:
                    stream_gen = self.local_gguf_provider.stream_tokens(
                        prompt,
                        system_prompt=config["system_prompt"],
                    )
                for token in stream_gen:
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
                logger.warning(f"[LLM] Errore esecuzione Local GGUF in-daemon: {e}. Fallback su HTTP...")

        # 2. Anthropic Claude API Mode
        if mode == "anthropic" or "anthropic.com" in config["endpoint"]:
            yield from self._stream_anthropic(config, prompt, _yield_and_track)
            return

        # 3. Unified OpenAI-Compatible HTTP Streaming (OpenAI, Ollama, Groq, DeepSeek, Local HTTP)
        endpoint = config["endpoint"]
        tools_schema = self.mcp_manager.get_tools_schema() if (self.mcp_manager and self.mcp_manager.enabled) else None
        if not tools_schema:
            tools_schema = None

        messages = [
            {"role": "system", "content": config["system_prompt"]},
            {"role": "user", "content": prompt}
        ]

        client_timeout = 30.0 if (mode == "ollama" or "localhost" in endpoint or "127.0.0.1" in endpoint) else 15.0
        client = OpenAICompatibleClient(
            endpoint=endpoint,
            api_key=config.get("api_key", ""),
            model_name=config.get("model_name", ""),
            temperature=config.get("temperature", 0.3),
            api_key_header=config.get("api_key_header", "Authorization"),
            api_key_prefix=config.get("api_key_prefix", "Bearer "),
            timeout=client_timeout,
        )

        try:
            native_tool_executed = False
            for event in client.stream_chat(messages, tools=tools_schema):
                if event.get("type") == "token":
                    yield _yield_and_track(event.get("content", ""))
                elif event.get("type") == "tool_call":
                    tool_name = event.get("tool")
                    args = event.get("args", {})
                    if self.mcp_manager and tool_name:
                        try:
                            res = self._execute_tool_sync(tool_name, args)
                            yield f"\n{res}"
                            native_tool_executed = True
                        except Exception as e:
                            logger.error(f"Errore durante l'esecuzione del native tool {tool_name}: {e}")

            # Fallback text parsing for models emitting tool call as JSON in conversational text
            if not native_tool_executed:
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

        except urllib.error.HTTPError as e:
            err_detail = ""
            try:
                body = e.read().decode("utf-8")
                try:
                    err_json = json.loads(body)
                    if isinstance(err_json, dict):
                        err_obj = err_json.get("error", err_json)
                        if isinstance(err_obj, dict):
                            err_detail = err_obj.get("message", "") or err_obj.get("detail", "")
                        elif isinstance(err_obj, str):
                            err_detail = err_obj
                except Exception:
                    err_detail = body.strip()[:200]
            except Exception:
                pass

            logger.error(f"[LLM] Errore HTTP {e.code} ({e.reason}) all'endpoint {endpoint}: {err_detail}")
            if e.code == 401:
                detail_msg = f" ({err_detail})" if err_detail else ""
                yield f"Errore di autenticazione (HTTP 401 Unauthorized) su {endpoint}{detail_msg}. Verifica che la chiave API nelle Preferenze sia valida."
            elif e.code == 403:
                detail_msg = f" ({err_detail})" if err_detail else ""
                yield f"Accesso negato (HTTP 403 Forbidden) su {endpoint}{detail_msg}."
            elif e.code == 404:
                detail_msg = f" ({err_detail})" if err_detail else ""
                yield f"Risorsa o modello non trovato (HTTP 404 Not Found) su {endpoint}{detail_msg}. Verifica che il modello configurato esista sul provider."
            elif e.code == 429:
                detail_msg = f" ({err_detail})" if err_detail else ""
                yield f"Limite di richieste superato o credito esaurito (HTTP 429 Too Many Requests) su {endpoint}{detail_msg}."
            else:
                detail_msg = f": {err_detail}" if err_detail else ""
                yield f"Errore dal server LLM (HTTP {e.code}: {e.reason}) su {endpoint}{detail_msg}."
        except urllib.error.URLError as e:
            logger.error(f"[LLM] Errore connessione all'endpoint {endpoint}: {e}")
            yield f"Impossibile connettersi al server LLM su {endpoint} ({e.reason})."
        except Exception as e:
            logger.error(f"[LLM] Errore durante lo streaming LLM: {e}")
            yield "Si è verificato un errore durante l'elaborazione della risposta."
