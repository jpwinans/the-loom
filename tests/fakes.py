"""Shared test doubles for the embedder seam.

Every test that needs an embedder without the real fastembed model used to
define its own local stub class — seven near-identical copies scattered
across the test suite. ``FakeEmbedder`` replaces all of them: construct it
with a fixed vector for tests that don't care what the query says, or with a
name-keyed mapping for tests where a per-entity search (gaps/clusters/scope)
needs a real similarity shape. ``FailingEmbedder`` pins the failure path
(embed_entity's status=error transition, ingestion's embeddingError).

Install either through ``theloom.config.set_embedder_override`` (the one
injection point every ``get_embedder()`` call site defers to) or, for tests
that patch the name directly, via
``monkeypatch.setattr("theloom.operations.semantic.get_embedder", lambda: fake)``.
"""

from __future__ import annotations


class FakeEmbedder:
    """A deterministic stand-in for :class:`theloom.semantic.embed.Embedder`.

    - ``FakeEmbedder([1.0, 0.0])`` — one fixed vector for every call,
      regardless of the query/document text.
    - ``FakeEmbedder({"alpha": [...], "beta": [...]})`` — the vector is
      chosen by the input text's first token (``text.split()[0]``), for
      tests where a single fixed vector can't shape a real similarity
      ordering between distinct entities.

    Counts calls to each method so a test can assert a redundant re-embed
    did or didn't happen.
    """

    def __init__(self, vectors: list[float] | dict[str, list[float]]) -> None:
        self._vectors = vectors
        self.query_calls = 0
        self.document_calls = 0

    def _resolve(self, text: str) -> list[float]:
        if isinstance(self._vectors, dict):
            return self._vectors[text.split()[0]]
        return self._vectors

    def embed_query(self, text: str) -> list[float]:
        self.query_calls += 1
        return self._resolve(text)

    def embed_document(self, text: str) -> list[float]:
        self.document_calls += 1
        return self._resolve(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_document(t) for t in texts]


class FailingEmbedder:
    """Raises from every embed call — the model-unavailable path."""

    def __init__(self, message: str = "embedding backend unavailable") -> None:
        self.message = message

    def embed_query(self, text: str) -> list[float]:
        raise RuntimeError(self.message)

    def embed_document(self, text: str) -> list[float]:
        raise RuntimeError(self.message)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError(self.message)
