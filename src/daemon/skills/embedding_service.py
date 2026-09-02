"""Embedding service for offline semantic matching.

This is a thin lightweight abstraction that provides the interface expected by the
roadmap without depending on heavy external libraries. It exposes a tiny vector
representation based on token frequencies, which is sufficient for offline local intent matching.
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Sequence


def _tokenize(text: str) -> List[str]:
    return [token for token in text.lower().replace("'", " ").replace("-", " ").split() if token]


class EmbeddingService:
    """Simple local embedding service using sparse token vectors."""

    def __init__(self):
        self._cache: Dict[str, Dict[str, float]] = {}

    def embed(self, text: str) -> Dict[str, float]:
        if text in self._cache:
            return self._cache[text]
        vector: Dict[str, float] = {}
        for token in _tokenize(text):
            vector[token] = vector.get(token, 0.0) + 1.0
        self._cache[text] = vector
        return vector

    def similarity(self, left: Sequence[str], right: Sequence[str]) -> float:
        vector_a = self.embed(" ".join(left))
        vector_b = self.embed(" ".join(right))
        if not vector_a or not vector_b:
            return 0.0

        dot = sum(vector_a.get(key, 0.0) * value for key, value in vector_b.items())
        a_norm = math.sqrt(sum(v * v for v in vector_a.values()))
        b_norm = math.sqrt(sum(v * v for v in vector_b.values()))
        if a_norm == 0 or b_norm == 0:
            return 0.0
        return dot / (a_norm * b_norm)

    def cosine_similarity(self, text_a: str, text_b: str) -> float:
        return self.similarity(_tokenize(text_a), _tokenize(text_b))
