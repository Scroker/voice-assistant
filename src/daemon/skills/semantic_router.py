"""Semantic intent router using a small ONNX sentence-embedding model.

Replaces the bag-of-words `VectorIntentMatcher` with real sentence embeddings
(all-MiniLM-L6-v2, exported to ONNX) for colloquial phrases and STT transcription
errors that plain token overlap cannot generalize to.

Runtime dependencies are `onnxruntime` + `tokenizers` only (no PyTorch, no
sentence-transformers): the ONNX graph exported by `optimum` only returns raw
token embeddings (`last_hidden_state`), so mean pooling over the attention mask
and L2 normalization -- normally done inside `sentence-transformers` -- are
reimplemented here in numpy.

If the model files are not available locally (offline first run, download still
in progress, etc.) this module transparently falls back to `VectorIntentMatcher`
so the Fast-Path never breaks; it just loses the semantic-generalization benefit
until the model is downloaded.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, List, Optional

import numpy as np

from .skill_registry import SkillRegistry
from .vector_intent_matcher import VectorIntentMatcher

logger = logging.getLogger("VoiceAssistant.SemanticRouter")

HF_REPO = "sentence-transformers/all-MiniLM-L6-v2"
MODEL_FILENAME = "onnx/model.onnx"
TOKENIZER_FILENAME = "tokenizer.json"

# Calibrated empirically on this model: paraphrases of the same intent score
# 0.72-0.90+, unrelated conversational sentences top out around 0.45-0.56.
DEFAULT_MIN_SCORE = 0.62
MAX_SEQ_LENGTH = 64


def _default_model_dir() -> str:
    return os.path.expanduser("~/.local/share/voice-assistant/models/embeddings/all-MiniLM-L6-v2")


class SemanticIntentRouter:
    """Matches free text against skill triggers using MiniLM sentence embeddings.

    Interface-compatible with `VectorIntentMatcher.match()` so it can be dropped
    into `FastPathDispatcher` without changing the caller.
    """

    DEFAULT_MIN_SCORE = DEFAULT_MIN_SCORE  # re-exported as a class attribute for callers

    def __init__(
        self,
        registry: Optional[SkillRegistry] = None,
        model_dir: Optional[str] = None,
        auto_download: bool = True,
    ):
        self.registry = registry or SkillRegistry.from_default_directory()
        self.model_dir = model_dir or _default_model_dir()
        self._auto_download = auto_download

        self._lock = threading.Lock()
        self._session = None
        self._tokenizer = None
        self._available = False

        self._intents: List[str] = []
        self._triggers: List[str] = []
        self._prototype_vectors: Optional[np.ndarray] = None

        self._fallback = VectorIntentMatcher(self.registry)

        self._try_load()

    # ------------------------------------------------------------------
    # Model loading
    # ------------------------------------------------------------------

    def _model_paths(self) -> tuple:
        return (
            os.path.join(self.model_dir, "model.onnx"),
            os.path.join(self.model_dir, "tokenizer.json"),
        )

    def _ensure_model_files(self) -> bool:
        onnx_path, tokenizer_path = self._model_paths()
        if os.path.exists(onnx_path) and os.path.exists(tokenizer_path):
            return True

        if not self._auto_download:
            return False

        try:
            from huggingface_hub import hf_hub_download

            os.makedirs(self.model_dir, exist_ok=True)
            logger.info(f"[SemanticRouter] Scaricamento modello '{HF_REPO}' da HuggingFace...")
            dl_onnx = hf_hub_download(repo_id=HF_REPO, filename=MODEL_FILENAME, local_dir=self.model_dir)
            dl_tok = hf_hub_download(repo_id=HF_REPO, filename=TOKENIZER_FILENAME, local_dir=self.model_dir)

            import shutil

            if os.path.abspath(dl_onnx) != os.path.abspath(onnx_path):
                shutil.copy2(dl_onnx, onnx_path)
            if os.path.abspath(dl_tok) != os.path.abspath(tokenizer_path):
                shutil.copy2(dl_tok, tokenizer_path)
            return os.path.exists(onnx_path) and os.path.exists(tokenizer_path)
        except Exception as e:
            logger.warning(f"[SemanticRouter] Download modello embedding fallito, uso fallback bag-of-words: {e}")
            return False

    def _try_load(self) -> None:
        try:
            if not self._ensure_model_files():
                return

            import onnxruntime as ort
            from tokenizers import Tokenizer

            onnx_path, tokenizer_path = self._model_paths()
            self._session = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
            tokenizer = Tokenizer.from_file(tokenizer_path)
            tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
            tokenizer.enable_truncation(max_length=MAX_SEQ_LENGTH)
            self._tokenizer = tokenizer

            self._build_prototypes()
            self._available = True
            logger.info(f"[SemanticRouter] Router semantico ONNX caricato ({len(self._triggers)} trigger indicizzati).")
        except Exception as e:
            logger.warning(f"[SemanticRouter] Caricamento router semantico fallito, uso fallback bag-of-words: {e}")
            self._session = None
            self._tokenizer = None
            self._available = False

    def _build_prototypes(self) -> None:
        intents: List[str] = []
        triggers: List[str] = []
        for skill in self.registry.skills:
            intent = str(skill.get("intent", ""))
            for trigger in skill.get("triggers", []) or []:
                intents.append(intent)
                triggers.append(str(trigger))

        self._intents = intents
        self._triggers = triggers
        self._prototype_vectors = self._embed(triggers) if triggers else None

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------

    def _embed(self, texts: List[str]) -> np.ndarray:
        encodings = self._tokenizer.encode_batch(texts)
        input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        token_type_ids = np.array([e.type_ids for e in encodings], dtype=np.int64)

        outputs = self._session.run(
            ["last_hidden_state"],
            {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "token_type_ids": token_type_ids,
            },
        )[0]

        mask = attention_mask[:, :, None].astype(np.float32)
        summed = (outputs * mask).sum(axis=1)
        counts = np.clip(mask.sum(axis=1), 1e-9, None)
        mean_pooled = summed / counts

        norms = np.linalg.norm(mean_pooled, axis=1, keepdims=True)
        return mean_pooled / np.clip(norms, 1e-9, None)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def available(self) -> bool:
        return self._available

    def match(self, text: str, min_score: float = DEFAULT_MIN_SCORE) -> Optional[Dict[str, Any]]:
        text = (text or "").strip()
        if not text:
            return None

        if not self._available:
            return self._fallback.match(text, min_score=min(min_score, 0.35))

        with self._lock:
            if self._prototype_vectors is None or len(self._triggers) == 0:
                return None
            query_vector = self._embed([text])[0]
            scores = self._prototype_vectors @ query_vector

        best_idx = int(np.argmax(scores))
        best_score = float(scores[best_idx])
        if best_score < min_score:
            return None

        best_intent = self._intents[best_idx]
        skill = self.registry.find_by_intent(best_intent)
        return {
            "intent": best_intent,
            "score": best_score,
            "matched_text": self._triggers[best_idx],
            "skill": skill,
        }

    def match_params(self, text: str) -> Optional[Dict[str, Any]]:
        res = self.match(text)
        if not res:
            return None
        skill = res.get("skill") or {}
        params = dict(skill.get("params", {}))
        return {"intent": res["intent"], "params": params, "score": res["score"]}
