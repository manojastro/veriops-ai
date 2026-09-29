"""Embedding providers.

``HashingEmbedder`` (default) is a deterministic feature-hashing embedder over normalised unigrams,
bigrams and character trigrams. It needs no network or model download, which keeps local
development and CI hermetic. Its semantic quality is far below a neural embedding model — hybrid
retrieval (keyword ranking + reranking) compensates, and ``OpenAIEmbedder`` can be enabled with
``EMBEDDING_PROVIDER=openai|azure_openai`` (requesting 384 dimensions to match the schema).
"""

from __future__ import annotations

import hashlib
import itertools
import re
import threading
from collections import OrderedDict
from typing import Any, Protocol

import numpy as np

from veriops.shared.config import EmbeddingProvider, Settings

_TOKEN = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")
_STOP = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "for",
        "is",
        "are",
        "be",
        "with",
        "by",
        "as",
        "at",
        "from",
        "this",
        "that",
        "it",
        "its",
        "was",
        "were",
    ]
)


class Embedder(Protocol):
    dimensions: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def tokenize(text: str) -> list[str]:
    tokens = [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]
    return [_stem(t) for t in tokens]


def _stem(token: str) -> str:
    for suffix in ("ations", "ation", "ing", "ies", "es", "ed", "s"):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            return token[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return token


class HashingEmbedder:
    def __init__(self, dimensions: int = 384) -> None:
        self.dimensions = dimensions

    def _features(self, text: str) -> list[tuple[str, float]]:
        tokens = tokenize(text)
        feats = [(f"u:{t}", 1.0) for t in tokens]
        feats += [(f"b:{a}_{b}", 0.7) for a, b in itertools.pairwise(tokens)]
        for t in tokens:
            padded = f"#{t}#"
            feats += [(f"c:{padded[i : i + 3]}", 0.25) for i in range(len(padded) - 2)]
        return feats

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vec = np.zeros(self.dimensions, dtype=np.float32)
            for feature, weight in self._features(text):
                digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
                index = int.from_bytes(digest[:4], "little") % self.dimensions
                sign = 1.0 if digest[4] & 1 else -1.0
                vec[index] += sign * weight
            norm = float(np.linalg.norm(vec))
            out.append((vec / norm if norm else vec).tolist())
        return out


class OpenAIEmbedder:
    _client: Any

    def __init__(self, settings: Settings) -> None:
        import openai

        self.dimensions = settings.embedding_dimensions
        self.model = settings.embedding_model
        if settings.embedding_provider == EmbeddingProvider.AZURE_OPENAI:
            self._client = openai.AzureOpenAI(
                azure_endpoint=settings.azure_openai_endpoint or "",
                api_key=settings.azure_openai_api_key.get_secret_value() if settings.azure_openai_api_key else "",
                api_version=settings.azure_openai_api_version,
            )
        else:
            self._client = openai.OpenAI(
                api_key=settings.openai_api_key.get_secret_value() if settings.openai_api_key else None,
                base_url=settings.openai_base_url,
            )

    def embed(self, texts: list[str]) -> list[list[float]]:
        response = self._client.embeddings.create(model=self.model, input=texts, dimensions=self.dimensions)
        return [d.embedding for d in response.data]


class CachedEmbedder:
    """LRU cache in front of any embedder (embeddings of identical text never change)."""

    def __init__(self, inner: Embedder, size: int = 4096) -> None:
        self.inner = inner
        self.dimensions = inner.dimensions
        self._cache: OrderedDict[str, list[float]] = OrderedDict()
        self._size = size
        self._lock = threading.Lock()

    def embed(self, texts: list[str]) -> list[list[float]]:
        keys = [hashlib.sha256(t.encode()).hexdigest() for t in texts]
        with self._lock:
            missing = [(i, t) for i, (k, t) in enumerate(zip(keys, texts, strict=True)) if k not in self._cache]
        if missing:
            vectors = self.inner.embed([t for _, t in missing])
            with self._lock:
                for (i, _), vec in zip(missing, vectors, strict=True):
                    self._cache[keys[i]] = vec
                while len(self._cache) > self._size:
                    self._cache.popitem(last=False)
        with self._lock:
            return [self._cache[k] for k in keys]


def build_embedder(settings: Settings) -> Embedder:
    if settings.embedding_provider == EmbeddingProvider.HASHING:
        return CachedEmbedder(HashingEmbedder(settings.embedding_dimensions))
    return CachedEmbedder(OpenAIEmbedder(settings))
