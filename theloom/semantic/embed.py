"""In-process embedder (no forked-worker subsystem — embedding runs inline).

Contract: model nomic-embed-text-v1.5 (768-dim), ``search_document: `` /
``search_query: `` prefixes, embedding text ``[entityType] name. observations…``,
SHA-256 content hash of the untruncated text, 30k-char truncation preferring a
sentence boundary in the last 20%.

Numerics note (documented): fastembed runs its own ONNX build; agreement with a
q8-quantized ONNX build on this corpus is ≈ 0.97 cosine. Previously-stored
document vectors are kept verbatim on import (so stored vectors are identical);
only live query vectors differ, which a rank-only comparison absorbs. fastembed
output is unnormalized — we L2-normalize (equivalent to ``normalize: true``).
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from typing import Any

import numpy as np

MODEL_ID = "nomic-ai/nomic-embed-text-v1.5"
EMBEDDING_DIMENSIONS = 768
EMBEDDING_VERSION = MODEL_ID
DOCUMENT_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "
MAX_EMBEDDING_CHARS = 30_000


def build_embedding_text(entity: dict[str, Any]) -> str:
    """Build the embedding text: `[type] name. obs1 obs2 …`."""
    from theloom.graph.metadata import coerce_observation

    observations = " ".join(coerce_observation(o) for o in entity.get("observations") or [])
    return f"[{entity['entityType']}] {entity['name']}. {observations}"


def compute_content_hash(entity: dict[str, Any]) -> str:
    return hashlib.sha256(build_embedding_text(entity).encode("utf-8")).hexdigest()


def truncate_text(text: str) -> str:
    if len(text) <= MAX_EMBEDDING_CHARS:
        return text
    truncated = text[:MAX_EMBEDDING_CHARS]
    last_sentence_end = max(
        truncated.rfind(". "),
        truncated.rfind(".\n"),
        truncated.rfind("! "),
        truncated.rfind("? "),
    )
    if last_sentence_end > MAX_EMBEDDING_CHARS * 0.8:
        return truncated[: last_sentence_end + 1]
    return truncated


class Embedder:
    """Lazy fastembed wrapper; loads the model on first use."""

    def __init__(self) -> None:
        self._model: Any = None

    def _ensure_model(self) -> Any:
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(MODEL_ID)
        return self._model

    def _embed(self, texts: list[str]) -> list[list[float]]:
        model = self._ensure_model()
        vectors: list[list[float]] = []
        for vector in model.embed(texts):
            array = np.asarray(vector, dtype=np.float32)
            norm = float(np.linalg.norm(array))
            if norm > 0:
                array = array / norm
            vectors.append([float(x) for x in array])
        return vectors

    def embed_document(self, text: str) -> list[float]:
        return self._embed([DOCUMENT_PREFIX + truncate_text(text)])[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._embed([DOCUMENT_PREFIX + truncate_text(t) for t in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._embed([QUERY_PREFIX + truncate_text(text)])[0]


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    return Embedder()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    va = np.asarray(a, dtype=np.float64)
    vb = np.asarray(b, dtype=np.float64)
    denominator = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if denominator == 0:
        return 0.0
    return float(np.dot(va, vb) / denominator)
