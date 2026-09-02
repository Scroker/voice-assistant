"""Very light local vector store for skill matching.

This provides a deduplicated in-memory vector index for offline semantic matching.
It intentionally avoids external dependencies to stay portable and easy to test.
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Tuple


class VectorStore:
    """Simple deduplicated vector store."""

    def __init__(self):
        self._entries: List[Tuple[str, str, Dict[str, float]]] = []

    def add(self, key: str, value: str, vector: Dict[str, float]) -> None:
        if not key:
            return
        # deduplicate by key
        self._entries = [item for item in self._entries if item[0] != key]
        self._entries.append((key, value, dict(vector)))

    def cosine_similarity(self, left: Dict[str, float], right: Dict[str, float]) -> float:
        if not left or not right:
            return 0.0
        dot = sum(left.get(k, 0.0) * v for k, v in right.items())
        left_norm = math.sqrt(sum(v * v for v in left.values()))
        right_norm = math.sqrt(sum(v * v for v in right.values()))
        if left_norm == 0 or right_norm == 0:
            return 0.0
        return dot / (left_norm * right_norm)

    def search(self, vector: Dict[str, float], top_k: int = 3) -> List[Tuple[str, float, str]]:
        results: List[Tuple[str, float, str]] = []
        for key, value, candidate in self._entries:
            score = self.cosine_similarity(vector, candidate)
            results.append((key, score, value))
        results.sort(key=lambda item: item[1], reverse=True)
        return results[:top_k]

    def __len__(self) -> int:
        return len(self._entries)
