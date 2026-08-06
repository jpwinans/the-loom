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

from theloom.config import get_embedder_override, load_config

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
    """Lazy fastembed wrapper; loads the model on first use.

    ``cache_dir`` pins where fastembed stores the downloaded model files —
    without it fastembed falls back to its own default (typically under the
    process's cwd or a per-invocation temp location), so the one-shot CLI
    would re-pay the ~500MB HuggingFace download on every invocation."""

    def __init__(self, cache_dir: str | None = None) -> None:
        self._model: Any = None
        self._cache_dir = cache_dir

    def _ensure_model(self) -> Any:
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(MODEL_ID, cache_dir=self._cache_dir)
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
def _default_embedder() -> Embedder:
    """The real, process-wide fastembed-backed embedder, its model cache
    pinned via the single config path (theloom/config.py: modelCacheDir /
    LOOM_MODEL_CACHE_DIR). Built once and cached; use ``get_embedder()`` to
    fetch it, not this directly."""
    return Embedder(cache_dir=load_config().model_cache_dir)


def get_embedder() -> Embedder | Any:
    """The active embedder: the config-installed override
    (:func:`theloom.config.set_embedder_override`), if a test has set one,
    otherwise the real embedder. This is the one call every embedding call
    site makes — inject a fake through the config override, not by
    monkeypatching this function's name in each importing module."""
    override = get_embedder_override()
    return override if override is not None else _default_embedder()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine of the angle between two vectors, or 0.0 when there is no
    comparable signal — an empty vector, a zero vector, or two vectors of
    different width. Stored vector widths are not enforced (a graph can hold
    embeddings from an older or other model), and callers score a proposal
    against every stored vector, so a width mismatch is ordinary input to be
    scored 0 rather than an error to raise mid-loop."""
    if len(a) != len(b):
        return 0.0
    va = np.asarray(a, dtype=np.float64)
    vb = np.asarray(b, dtype=np.float64)
    denominator = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if denominator == 0:
        return 0.0
    return float(np.dot(va, vb) / denominator)
