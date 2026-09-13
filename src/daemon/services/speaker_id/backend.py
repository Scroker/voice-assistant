"""Protocol and factory for speaker embedding extraction backends."""

from __future__ import annotations

import logging
from typing import Optional, Protocol, runtime_checkable
import numpy as np

logger = logging.getLogger("VoiceAssistant.SpeakerID.Backend")


@runtime_checkable
class SpeakerEmbeddingBackend(Protocol):
    """Protocol for speaker embedding models."""

    name: str
    sample_rate: int
    min_samples: int

    def is_available(self) -> bool:
        """Return True if backend dependencies are installed and accessible."""
        ...

    def load(self) -> None:
        """Lazy-load the model into memory."""
        ...

    def unload(self) -> None:
        """Unload the model and free memory."""
        ...

    def preprocess(self, pcm: np.ndarray) -> np.ndarray:
        """Preprocess audio (e.g. trim silence) returning speech samples."""
        ...

    def embed(self, pcm: np.ndarray) -> Optional[np.ndarray]:
        """Compute L2-normalized speaker embedding vector for 16kHz float32 audio.

        Args:
            pcm: 1D or 2D numpy array of float32 samples in range [-1.0, 1.0] at 16kHz.

        Returns:
            1D numpy array of shape (embedding_dim,) normalized to unit length,
            or None if useful speech length is less than min_samples.
        """
        ...


def create_backend(name: str = "resemblyzer") -> SpeakerEmbeddingBackend:
    """Create a speaker embedding backend by name."""
    if name == "resemblyzer":
        from .resemblyzer_backend import ResemblyzerBackend
        return ResemblyzerBackend()
    raise ValueError(f"Unknown speaker embedding backend: {name}")
