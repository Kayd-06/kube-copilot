"""Pluggable text embedders. FastEmbed is the default; HashEmbedder is for tests."""

from __future__ import annotations

import hashlib
import logging
import math
from typing import Protocol, runtime_checkable

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

# bge-small-en-v1.5 produces 384-dimensional vectors.
DEFAULT_DIMENSION = 384
DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"


@runtime_checkable
class Embedder(Protocol):
    """Minimal embedding interface used by the store and retriever."""

    @property
    def dimension(self) -> int: ...

    @property
    def model_name(self) -> str: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _l2_normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return vector
    return [value / norm for value in vector]


class HashEmbedder:
    """Deterministic bag-of-words embedder. No model download, CPU-only.

    Tokens that two texts share land on the same dimensions, so cosine
    similarity is a useful (if rough) lexical overlap score. Used in tests
    and as a last-resort fallback when FastEmbed cannot be loaded.
    """

    def __init__(self, dimension: int = DEFAULT_DIMENSION, model_name: str = "hash") -> None:
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        self._dimension = dimension
        self._model_name = model_name

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        return self._model_name

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [_l2_normalize(self._vector(text)) for text in texts]

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self._dimension
        for token in _tokenize(text):
            digest = hashlib.md5(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "little") % self._dimension
            # A little extra weight for longer tokens (error reasons, image names).
            vector[index] += 1.0 + min(len(token), 16) / 16.0
        if all(value == 0.0 for value in vector):
            # Empty input still needs a valid unit-ish vector.
            vector[0] = 1.0
        return vector


def _tokenize(text: str) -> list[str]:
    lowered = (text or "").lower()
    tokens: list[str] = []
    current: list[str] = []
    for char in lowered:
        if char.isalnum():
            current.append(char)
        else:
            if current:
                tokens.append("".join(current))
                current = []
    if current:
        tokens.append("".join(current))
    return tokens


class FastEmbedEmbedder:
    """CPU-only FastEmbed wrapper. Models are downloaded on first use."""

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise RuntimeError(
                "fastembed is not installed. Add it with pip install fastembed, "
                "or set EMBEDDING_BACKEND=fake for tests."
            ) from exc
        logger.info("Loading FastEmbed model %s (CPU)", model_name)
        self._model = TextEmbedding(model_name=model_name)
        self._model_name = model_name
        self._dimension = DEFAULT_DIMENSION
        # Probe one short string so we learn the real dimension.
        probe = self.embed(["dimension probe"])
        if probe and probe[0]:
            self._dimension = len(probe[0])

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        return self._model_name

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for raw in self._model.embed(texts):
            vectors.append([float(value) for value in raw])
        return vectors


def build_embedder(settings: Settings | None = None) -> Embedder:
    """Factory for the configured embedding backend."""
    settings = settings or get_settings()
    backend = (settings.embedding_backend or "fastembed").strip().lower()
    if backend in {"fake", "hash", "deterministic"}:
        return HashEmbedder(dimension=DEFAULT_DIMENSION, model_name="hash")
    if backend in {"fastembed", "default", ""}:
        return FastEmbedEmbedder(model_name=settings.embedding_model)
    raise ValueError(
        f"Unknown EMBEDDING_BACKEND={settings.embedding_backend!r}. "
        "Use 'fastembed' or 'fake'."
    )
