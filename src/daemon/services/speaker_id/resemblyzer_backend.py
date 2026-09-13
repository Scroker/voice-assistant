"""Resemblyzer (GE2E d-vector) speaker embedding backend."""

from __future__ import annotations

import gc
import logging
from typing import Optional
import numpy as np

logger = logging.getLogger("VoiceAssistant.SpeakerID.Resemblyzer")


class ResemblyzerBackend:
    """Speaker embedding backend using Resemblyzer VoiceEncoder."""

    name: str = "resemblyzer"
    sample_rate: int = 16000
    min_samples: int = 6400  # 0.4s @ 16kHz

    def __init__(self) -> None:
        self._encoder = None
        self._preprocess_wav = None

    def is_available(self) -> bool:
        """Check if torch and resemblyzer are installed and model weights exist."""
        try:
            import torch  # noqa: F401
            import resemblyzer
            from pathlib import Path
            weights = Path(resemblyzer.__file__).resolve().parent / "pretrained.pt"
            return weights.is_file()
        except (ImportError, Exception) as exc:
            logger.debug("Resemblyzer not available: %s", exc)
            return False

    def is_downloading(self) -> bool:
        """Check if backend model weights are currently downloading."""
        return False

    def load(self) -> None:
        """Lazy-load the Resemblyzer VoiceEncoder on CPU."""
        if self._encoder is not None:
            return

        try:
            from resemblyzer import VoiceEncoder, preprocess_wav
            self._preprocess_wav = preprocess_wav
            self._encoder = VoiceEncoder("cpu")
            logger.info("Resemblyzer VoiceEncoder loaded on CPU.")
        except Exception as exc:
            logger.error("Failed to load Resemblyzer VoiceEncoder: %s", exc)
            self._encoder = None
            self._preprocess_wav = None
            raise

    def unload(self) -> None:
        """Unload the encoder from memory."""
        self._encoder = None
        self._preprocess_wav = None
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
        logger.info("Resemblyzer VoiceEncoder unloaded.")

    def preprocess(self, pcm: np.ndarray) -> np.ndarray:
        """Preprocess audio (trim silence and normalize) returning useful speech samples."""
        if pcm is None or len(pcm) == 0:
            return np.empty(0, dtype=np.float32)

        if self._encoder is None or self._preprocess_wav is None:
            self.load()

        pcm_1d = np.asarray(pcm, dtype=np.float32).flatten()
        if len(pcm_1d) == 0:
            return np.empty(0, dtype=np.float32)

        try:
            return self._preprocess_wav(pcm_1d, source_sr=self.sample_rate)
        except Exception as exc:
            logger.error("Error preprocessing audio for speaker ID: %s", exc)
            return pcm_1d

    def embed(self, pcm: np.ndarray) -> Optional[np.ndarray]:
        """Compute L2-normalized 256-dim embedding vector."""
        if pcm is None or len(pcm) == 0:
            return None

        if self._encoder is None or self._preprocess_wav is None:
            self.load()

        pcm_1d = np.asarray(pcm, dtype=np.float32).flatten()
        if len(pcm_1d) == 0:
            return None

        try:
            processed_wav = self._preprocess_wav(pcm_1d, source_sr=self.sample_rate)
            if len(processed_wav) < self.min_samples:
                logger.debug(
                    "Useful speech too short for embedding: %d samples < min %d",
                    len(processed_wav),
                    self.min_samples,
                )
                return None

            raw_emb = self._encoder.embed_utterance(processed_wav)
            norm = np.linalg.norm(raw_emb)
            if norm < 1e-9:
                return None
            return (raw_emb / norm).astype(np.float32)
        except Exception as exc:
            logger.error("Error computing speaker embedding: %s", exc)
            return None
