"""Lightweight semantic intent matcher using token overlap and cosine-like similarity.

This intentionally uses a deterministic offline algorithm so the feature works without
external ML packages or a database. It provides the missing semantic fast-path layer
while keeping the whole daemon self-contained.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .skill_registry import SkillRegistry

STOPWORDS = {
    "a", "ad", "ai", "al", "alla", "alle", "allo", "apri", "avvia", "che", "chi", "ci",
    "con", "da", "del", "della", "dello", "di", "e", "ed", "fra", "gi", "gli", "il",
    "in", "la", "le", "lo", "lui", "mi", "ne", "o", "per", "piu", "più", "poi", "puoi",
    "quando", "quale", "qui", "se", "si", "su", "tra", "un", "una", "uno", "voglio",
    "vorrei", "ti", "tu", "come", "cosa", "dimmi", "fammi", "fa", "fai", "non", "perche",
}


def _normalize_text(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s']", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _tokenize(text: str) -> List[str]:
    norm = _normalize_text(text)
    if not norm:
        return []
    return [token for token in norm.split() if token]


def _content_tokens(text: str) -> List[str]:
    tokens = []
    for token in _tokenize(text):
        if token in STOPWORDS or len(token) <= 2:
            continue
        tokens.append(token)
    return tokens


def _build_vector(tokens: Iterable[str]) -> Dict[str, float]:
    counts: Dict[str, float] = {}
    for token in tokens:
        counts[token] = counts.get(token, 0.0) + 1.0
    return counts


def _cosine_similarity(left: Dict[str, float], right: Dict[str, float]) -> float:
    if not left or not right:
        return 0.0

    dot = sum(left.get(k, 0.0) * v for k, v in right.items())
    left_norm = math.sqrt(sum(v * v for v in left.values()))
    right_norm = math.sqrt(sum(v * v for v in right.values()))

    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


class VectorIntentMatcher:
    """Semantic matcher for colloquial phrases using weighted token overlap.

    This is a lightweight semantic fallback for the old regex-based FastPathDispatcher.
    It estimates similarity with a cosine-style score over token frequencies and major
    synonyms/variants present in the skill triggers.
    """

    def __init__(self, registry: Optional[SkillRegistry] = None):
        self.registry = registry or SkillRegistry.from_default_directory()

    def _skill_vectors(self):
        for skill in self.registry.skills:
            intent = str(skill.get("intent", ""))
            triggers = skill.get("triggers", []) or []
            vectors = []
            for trigger in triggers:
                vectors.append((str(trigger), _build_vector(_tokenize(str(trigger)))))
            yield intent, vectors

    def match(self, text: str, min_score: float = 0.35) -> Optional[Dict[str, Any]]:
        norm_text = _normalize_text(text)
        if not norm_text:
            return None

        target_tokens = _content_tokens(norm_text)
        if not target_tokens:
            return None

        target_vector = _build_vector(target_tokens)

        best_intent = None
        best_score = 0.0
        best_match = None

        for intent, trigger_vectors in self._skill_vectors():
            for trigger_text, trigger_vector in trigger_vectors:
                trigger_tokens = _content_tokens(trigger_text)
                overlap = len(set(target_tokens) & set(trigger_tokens))

                # Reject generic short phrases that only share a single generic word such as
                # "dimmi" or "cosa"; this avoids false positives like "Dimmi qualcosa".
                if len(target_tokens) <= 2 and overlap < 2:
                    continue

                score = _cosine_similarity(target_vector, trigger_vector)
                if score > best_score:
                    best_score = score
                    best_intent = intent
                    best_match = trigger_text

        if best_intent and best_score >= min_score:
            skill = self.registry.find_by_intent(best_intent)
            return {
                "intent": best_intent,
                "score": float(best_score),
                "matched_text": best_match,
                "skill": skill,
            }

        return None

    def match_params(self, text: str) -> Optional[Dict[str, Any]]:
        res = self.match(text)
        if not res:
            return None
        skill = res.get("skill") or {}
        params = dict(skill.get("params", {}))
        return {"intent": res["intent"], "params": params, "score": res["score"]}
