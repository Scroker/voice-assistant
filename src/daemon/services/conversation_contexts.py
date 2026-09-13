import json
import logging
import os
import re
import threading
import time
import uuid
from typing import Dict, List, Optional, Any

from core.path_utils import get_data_dir

logger = logging.getLogger("VoiceAssistant.ContextManager")

_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class ConversationContext:
    def __init__(self, context_id: str, title: str = ""):
        self.context_id = context_id
        self.title = title or ("Nuova chat" if context_id != "voice" else "Comandi Vocali")
        self.created_at = time.time()
        self.updated_at = self.created_at
        # history array for LLM context building: [{"role": "user", "content": "..."}, ...]
        self.messages: List[Dict[str, Any]] = []
        self.lock = threading.RLock()

    def add_message(self, role: str, content: str, metadata: Optional[Dict] = None):
        with self.lock:
            msg = {
                "role": role,
                "content": content,
                "timestamp": time.time(),
            }
            if metadata:
                msg["metadata"] = metadata
            self.messages.append(msg)
            self.updated_at = time.time()
            # Imposta un titolo auto dalla prima frase dell'utente se vuoto e se non è voice
            if self.context_id != "voice" and self.title == "Nuova chat" and role == "user":
                self.title = content[:30] + ("..." if len(content) > 30 else "")

    def get_messages_for_llm(self, max_messages: int = 20, max_age_seconds: Optional[int] = None) -> List[Dict[str, str]]:
        """Returns messages formatted for LLM generation"""
        with self.lock:
            now = time.time()
            valid_msgs = []
            for msg in reversed(self.messages):
                if max_age_seconds and (now - msg["timestamp"] > max_age_seconds):
                    continue
                valid_msgs.insert(0, {"role": msg["role"], "content": msg["content"]})
                if len(valid_msgs) >= max_messages:
                    break
            return valid_msgs

    def to_dict(self) -> Dict:
        with self.lock:
            return {
                "id": self.context_id,
                "title": self.title,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "messages": list(self.messages)
            }

    def from_dict(self, data: Dict):
        with self.lock:
            self.title = data.get("title", self.title)
            self.created_at = data.get("created_at", self.created_at)
            self.updated_at = data.get("updated_at", self.updated_at)
            self.messages = list(data.get("messages", []))


class ConversationContextManager:
    """Thread-safe manager for multi-chat contexts and persistence."""

    @staticmethod
    def is_valid_chat_id(context_id: str) -> bool:
        return isinstance(context_id, str) and bool(_ID_RE.match(context_id))

    def __init__(self, base_dir: str = ""):
        self.base_dir = base_dir or str(get_data_dir())
        self.store_dir = os.path.join(self.base_dir, "conversations")
        os.makedirs(self.store_dir, exist_ok=True)

        self.contexts: Dict[str, ConversationContext] = {}
        self.lock = threading.RLock()

        # Inizializza o carica i contesti
        self._load_all()

        # Assicura sempre l'esistenza di 'voice' in RAM
        if "voice" not in self.contexts:
            self.contexts["voice"] = ConversationContext("voice")

    def _get_file_path(self, context_id: str) -> str:
        if not self.is_valid_chat_id(context_id):
            raise ValueError(f"Invalid conversation ID: {context_id}")
        path = os.path.join(self.store_dir, f"{context_id}.json")
        real_store = os.path.realpath(self.store_dir) + os.sep
        if not os.path.realpath(path).startswith(real_store):
            raise ValueError(f"Path traversal detected: {context_id}")
        return path

    def _load_all(self):
        with self.lock:
            for fname in os.listdir(self.store_dir):
                if fname.endswith(".json"):
                    ctx_id = fname[:-5]
                    if not self.is_valid_chat_id(ctx_id):
                        logger.warning(f"Ignoring non-UUID conversation file: {fname}")
                        continue
                    fpath = os.path.join(self.store_dir, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            ctx = ConversationContext(ctx_id)
                            ctx.from_dict(data)
                            self.contexts[ctx_id] = ctx
                    except Exception as e:
                        logger.error(f"Failed to load conversation {ctx_id}: {e}")

    def save(self, context_id: str) -> bool:
        """Save a specific context to disk atomically (skips 'voice')."""
        if not self.is_valid_chat_id(context_id) or context_id == "voice":
            return False

        with self.lock:
            ctx = self.contexts.get(context_id)
            if not ctx:
                return False
            data = ctx.to_dict()

        try:
            fpath = self._get_file_path(context_id)
        except ValueError:
            return False

        tmp_fpath = fpath + ".tmp"
        try:
            with open(tmp_fpath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_fpath, fpath)
            return True
        except Exception as e:
            logger.error(f"Failed to save conversation {context_id}: {e}")
            if os.path.exists(tmp_fpath):
                try:
                    os.remove(tmp_fpath)
                except Exception:
                    pass
            return False

    def create(self, title: str = "") -> str:
        with self.lock:
            ctx_id = str(uuid.uuid4())
            ctx = ConversationContext(ctx_id, title)
            self.contexts[ctx_id] = ctx
            self.save(ctx_id)
            return ctx_id

    def get(self, context_id: str) -> Optional[ConversationContext]:
        with self.lock:
            return self.contexts.get(context_id)

    def delete(self, context_id: str) -> bool:
        if not self.is_valid_chat_id(context_id) or context_id == "voice":
            return False
        with self.lock:
            existed = context_id in self.contexts
            if existed:
                del self.contexts[context_id]
            try:
                fpath = self._get_file_path(context_id)
            except ValueError:
                return False
            file_existed = os.path.exists(fpath)
            if file_existed:
                try:
                    os.remove(fpath)
                except Exception as e:
                    logger.error(f"Failed to delete conversation file {fpath}: {e}")
            return existed or file_existed

    def clear(self, context_id: str) -> bool:
        if not self.is_valid_chat_id(context_id) and context_id != "voice":
            return False
        with self.lock:
            ctx = self.get(context_id)
            if not ctx:
                return False
            with ctx.lock:
                ctx.messages.clear()
                ctx.updated_at = time.time()
            if context_id != "voice":
                return self.save(context_id)
            return True

    def list_conversations(self) -> List[Dict]:
        """Return metadata of all persistent conversations (skips 'voice')."""
        with self.lock:
            sorted_ctxs = sorted(
                [ctx for k, ctx in self.contexts.items() if k != "voice"],
                key=lambda x: x.updated_at,
                reverse=True
            )
            return [
                {"id": ctx.context_id, "title": ctx.title, "updated_at": ctx.updated_at}
                for ctx in sorted_ctxs
            ]
