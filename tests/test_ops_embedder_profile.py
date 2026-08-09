"""Desire 8 (claude-desires.md): `embedder-profile` reports the CONFIGURED
embedder's live-measured similarity landscape — observed unrelated-pair
baseline, observed related-pair range, and a cutoff calibrated from those
observations — never a hard-coded constant.

Pinned here at the command layer (theloom.operations.semantic.embedder_profile):
the shape a caller gets back, that it is wired into the registry, and — the
actual pass bar — that editing the probe corpus or swapping the embedder
changes the reported numbers, proving the command measures rather than recites.
"""

from __future__ import annotations

import math

import pytest

from theloom.operations.semantic import EmbedderProfileInput, embedder_profile
from theloom.semantic import landscape
from theloom.store.multigraph import MultiGraph


class _MappedEmbedder:
    """Full per-text control, like the one in test_semantic_landscape.py --
    duplicated locally rather than imported so this test file (command-layer
    concerns) doesn't reach into the core module's test internals."""

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self._vectors = vectors
        self.query_calls = 0
        self.document_calls = 0

    def embed_query(self, text: str) -> list[float]:
        self.query_calls += 1
        return self._vectors[text]

    def embed_document(self, text: str) -> list[float]:
        self.document_calls += 1
        return self._vectors[text]


def _vector_for_cosine(cosine: float) -> list[float]:
    return [cosine, math.sqrt(max(0.0, 1 - cosine**2)), 0.0]


_TINY_CORPUS_LOW = (("q", "far"),)
_TINY_CORPUS_HIGH = (("q", "close"),)


def _tiny_vectors() -> dict[str, list[float]]:
    return {
        "q": [1.0, 0.0, 0.0],
        "far": _vector_for_cosine(0.1),
        "close": _vector_for_cosine(0.9),
        "probe": [1.0, 0.0, 0.0],
        "dimension probe": [1.0, 0.0, 0.0],
    }


def _patch_tiny_corpus(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(landscape, "UNRELATED_PROBE_PAIRS", _TINY_CORPUS_LOW)
    monkeypatch.setattr(landscape, "RELATED_PROBE_PAIRS", _TINY_CORPUS_HIGH)


def test_reports_measured_baseline_range_and_cutoff(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_tiny_corpus(monkeypatch)
    embedder = _MappedEmbedder(_tiny_vectors())
    monkeypatch.setattr("theloom.operations.semantic.get_embedder", lambda: embedder)

    result = embedder_profile(EmbedderProfileInput(), multi)

    assert result["model"]
    assert result["dimensions"] == 3
    assert result["probeCorpus"]["unrelatedPairCount"] == 1
    assert result["probeCorpus"]["relatedPairCount"] == 1
    assert len(result["probeCorpus"]["pairs"]) == 2
    assert set(result["unrelatedPairBaseline"]) == {
        "meanScore",
        "minScore",
        "maxScore",
        "stdevScore",
        "sampleSize",
    }
    assert result["unrelatedPairBaseline"]["sampleSize"] == 1
    assert result["relatedPairRange"]["sampleSize"] == 1
    assert result["relatedPairRange"]["meanScore"] > result["unrelatedPairBaseline"]["meanScore"]
    # The cutoff sits strictly between the two observed bands -- computed,
    # not equal to either band's own number and not some other literal.
    assert (
        result["unrelatedPairBaseline"]["maxScore"]
        < result["meaningfullyRelatedCutoff"]
        < result["relatedPairRange"]["minScore"]
    )
    assert isinstance(result["cutoffMethod"], str) and result["cutoffMethod"]


def test_dimensions_are_measured_not_the_production_constant(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A swapped-in embedder with a different width must be reported
    honestly -- ``dimensions`` cannot be the hard-coded EMBEDDING_DIMENSIONS
    constant for the real model."""
    _patch_tiny_corpus(monkeypatch)
    vectors = _tiny_vectors()
    vectors["probe"] = [1.0, 0.0]
    vectors["dimension probe"] = [1.0, 0.0]
    # 2-wide vectors would break cosine_similarity against the 3-wide probe
    # pairs, so give this embedder entirely 2-wide vectors.
    two_wide = {
        "q": [1.0, 0.0],
        "far": [0.1, math.sqrt(1 - 0.01)],
        "close": [0.9, math.sqrt(1 - 0.81)],
        "probe": [1.0, 0.0],
        "dimension probe": [1.0, 0.0],
    }
    embedder = _MappedEmbedder(two_wide)
    monkeypatch.setattr("theloom.operations.semantic.get_embedder", lambda: embedder)

    result = embedder_profile(EmbedderProfileInput(), multi)

    assert result["dimensions"] == 2


def test_changing_the_probe_corpus_changes_the_reported_numbers(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pass bar: this is a MEASUREMENT, not a constant. The same
    embedder, asked with two different probe corpora (editing
    theloom/semantic/landscape.py's module-level lists is exactly this),
    reports two different landscapes."""
    embedder = _MappedEmbedder(_tiny_vectors())
    monkeypatch.setattr("theloom.operations.semantic.get_embedder", lambda: embedder)

    monkeypatch.setattr(landscape, "UNRELATED_PROBE_PAIRS", (("q", "far"),))
    monkeypatch.setattr(landscape, "RELATED_PROBE_PAIRS", (("q", "close"),))
    low_corpus_result = embedder_profile(EmbedderProfileInput(), multi)

    # Swap which pair is called "unrelated" vs "related" -- a different
    # corpus, same embedder.
    monkeypatch.setattr(landscape, "UNRELATED_PROBE_PAIRS", (("q", "close"),))
    monkeypatch.setattr(landscape, "RELATED_PROBE_PAIRS", (("q", "far"),))
    swapped_corpus_result = embedder_profile(EmbedderProfileInput(), multi)

    assert low_corpus_result["unrelatedPairBaseline"]["meanScore"] != pytest.approx(
        swapped_corpus_result["unrelatedPairBaseline"]["meanScore"]
    )
    assert low_corpus_result["meaningfullyRelatedCutoff"] != pytest.approx(
        swapped_corpus_result["meaningfullyRelatedCutoff"]
    )


def test_changing_the_embedder_changes_the_reported_numbers(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same probe corpus, a differently-shaped embedder -- the numbers must
    track the embedder actually configured, not a value fixed at write time."""
    _patch_tiny_corpus(monkeypatch)

    narrow = _MappedEmbedder(_tiny_vectors())
    monkeypatch.setattr("theloom.operations.semantic.get_embedder", lambda: narrow)
    narrow_result = embedder_profile(EmbedderProfileInput(), multi)

    wide_vectors = _tiny_vectors()
    wide_vectors["far"] = _vector_for_cosine(0.05)
    wide_vectors["close"] = _vector_for_cosine(0.99)
    wide = _MappedEmbedder(wide_vectors)
    monkeypatch.setattr("theloom.operations.semantic.get_embedder", lambda: wide)
    wide_result = embedder_profile(EmbedderProfileInput(), multi)

    assert narrow_result["unrelatedPairBaseline"]["meanScore"] != pytest.approx(
        wide_result["unrelatedPairBaseline"]["meanScore"]
    )


def test_registered_in_cli() -> None:
    from theloom.cli.registry import COMMANDS

    descriptor = next(c for c in COMMANDS if c.name == "embedder-profile")
    assert descriptor.handler is embedder_profile
    assert descriptor.allow_empty is True
    assert descriptor.category == "Embeddings"
