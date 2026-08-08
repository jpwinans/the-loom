"""Desire 8 (claude-desires.md): the embedder's own empirical similarity
landscape, measured live against a small fixed probe corpus rather than
asserted as a constant.

These tests pin: the cutoff is *derived* from whatever the corpus actually
measured (never a literal), a clean separation between the bands uses the
midpoint of the observed gap, an overlapping probe corpus falls back to a
disclosed statistical rule instead of pretending there was a gap, editing the
corpus changes the reported numbers, and in-process caching does not silently
serve a stale measurement across a corpus change.
"""

from __future__ import annotations

import math

import pytest

from tests.fakes import FakeEmbedder
from theloom.semantic.embed import cosine_similarity
from theloom.semantic.landscape import (
    RELATED_PROBE_PAIRS,
    UNRELATED_PROBE_PAIRS,
    band_stats_doc,
    measure_landscape,
    pair_doc,
)
from theloom.semantic.search import l2_similarity


class _MappedEmbedder:
    """A fake embedder with full control over the vector for each exact
    text — the shared ``tests.fakes.FakeEmbedder`` only keys off the first
    token, which isn't enough control to engineer specific cosine values for
    this module's calibration tests."""

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
    """A unit vector at exactly ``cosine`` similarity to [1, 0, 0]."""
    return [cosine, math.sqrt(max(0.0, 1 - cosine**2)), 0.0]


def _score_for_cosine(cosine: float) -> float:
    return l2_similarity(cosine)


class TestCleanSeparation:
    def test_cutoff_is_the_midpoint_of_the_observed_gap(self) -> None:
        vectors = {
            "uq1": [1.0, 0.0, 0.0],
            "ud1": _vector_for_cosine(0.0),
            "uq2": [1.0, 0.0, 0.0],
            "ud2": _vector_for_cosine(0.2),
            "rq1": [1.0, 0.0, 0.0],
            "rd1": _vector_for_cosine(0.9),
            "rq2": [1.0, 0.0, 0.0],
            "rd2": _vector_for_cosine(0.95),
        }
        embedder = _MappedEmbedder(vectors)

        profile = measure_landscape(
            embedder,
            unrelated_pairs=(("uq1", "ud1"), ("uq2", "ud2")),
            related_pairs=(("rq1", "rd1"), ("rq2", "rd2")),
        )

        unrelated_max = max(_score_for_cosine(0.0), _score_for_cosine(0.2))
        related_min = min(_score_for_cosine(0.9), _score_for_cosine(0.95))
        assert profile.unrelated_baseline.max == pytest.approx(unrelated_max)
        assert profile.related_range.min == pytest.approx(related_min)
        assert profile.meaningfully_related_cutoff == pytest.approx(
            (unrelated_max + related_min) / 2
        )
        assert "separated cleanly" in profile.cutoff_method
        assert len(profile.pairs) == 4
        assert {p.relation for p in profile.pairs} == {"unrelated", "related"}


class TestOverlappingBands:
    def test_falls_back_to_stdev_above_the_unrelated_mean(self) -> None:
        # cosine 0.5 -> score 0.5 exactly (1/(1+sqrt(1))); cosine 0.3 -> a
        # lower score, so the "related" pair scores BELOW the "unrelated"
        # one -- a deliberately pathological probe corpus with no gap.
        vectors = {
            "uq1": [1.0, 0.0, 0.0],
            "ud1": _vector_for_cosine(0.5),
            "rq1": [1.0, 0.0, 0.0],
            "rd1": _vector_for_cosine(0.3),
        }
        embedder = _MappedEmbedder(vectors)

        profile = measure_landscape(
            embedder,
            unrelated_pairs=(("uq1", "ud1"),),
            related_pairs=(("rq1", "rd1"),),
        )

        assert profile.unrelated_baseline.max == pytest.approx(0.5)
        assert profile.related_range.min < profile.unrelated_baseline.max
        # n=1 on each side -> stdev 0 -> cutoff collapses to the unrelated mean.
        assert profile.meaningfully_related_cutoff == pytest.approx(0.5)
        assert "overlapped" in profile.cutoff_method

    def test_stdev_above_mean_with_a_real_spread(self) -> None:
        vectors = {
            "uq1": [1.0, 0.0, 0.0],
            "ud1": _vector_for_cosine(0.4),
            "uq2": [1.0, 0.0, 0.0],
            "ud2": _vector_for_cosine(0.6),
            "rq1": [1.0, 0.0, 0.0],
            "rd1": _vector_for_cosine(0.3),
        }
        embedder = _MappedEmbedder(vectors)

        profile = measure_landscape(
            embedder,
            unrelated_pairs=(("uq1", "ud1"), ("uq2", "ud2")),
            related_pairs=(("rq1", "rd1"),),
        )

        scores = [_score_for_cosine(0.4), _score_for_cosine(0.6)]
        mean = sum(scores) / len(scores)
        stdev = math.sqrt(sum((s - mean) ** 2 for s in scores) / len(scores))
        assert profile.meaningfully_related_cutoff == pytest.approx(mean + 2 * stdev)


class TestCorpusIsLiveNotHardcoded:
    def test_default_corpus_matches_the_module_constants(self) -> None:
        embedder = FakeEmbedder([1.0, 0.0])
        profile = measure_landscape(embedder, use_cache=False)

        assert len(profile.pairs) == len(UNRELATED_PROBE_PAIRS) + len(RELATED_PROBE_PAIRS)
        expected = [(q, d) for q, d in UNRELATED_PROBE_PAIRS] + [
            (q, d) for q, d in RELATED_PROBE_PAIRS
        ]
        actual = [(p.query, p.document) for p in profile.pairs]
        assert actual == expected

    def test_editing_the_corpus_changes_the_reported_numbers(self) -> None:
        """The pass bar this module exists to meet: the same embedder, asked
        about two different probe corpora, reports two different landscapes
        -- there is no constant standing in for a measurement."""
        vectors = {
            "q": [1.0, 0.0, 0.0],
            "close": _vector_for_cosine(0.9),
            "far": _vector_for_cosine(0.1),
        }
        embedder = _MappedEmbedder(vectors)

        near_profile = measure_landscape(
            embedder,
            unrelated_pairs=(("q", "far"),),
            related_pairs=(("q", "close"),),
            use_cache=False,
        )
        # Swap which pair plays which role -- the "unrelated" baseline must
        # move to reflect the new corpus, not stay pinned to the old number.
        swapped_profile = measure_landscape(
            embedder,
            unrelated_pairs=(("q", "close"),),
            related_pairs=(("q", "far"),),
            use_cache=False,
        )

        assert near_profile.unrelated_baseline.mean != pytest.approx(
            swapped_profile.unrelated_baseline.mean
        )
        assert near_profile.unrelated_baseline.mean == pytest.approx(
            swapped_profile.related_range.mean
        )


class TestCaching:
    def test_repeated_calls_reuse_the_measurement_by_default(self) -> None:
        vectors = {"q": [1.0, 0.0, 0.0], "d": _vector_for_cosine(0.5)}
        embedder = _MappedEmbedder(vectors)
        pairs = (("q", "d"),)

        first = measure_landscape(embedder, unrelated_pairs=pairs, related_pairs=pairs)
        calls_after_first = embedder.query_calls
        second = measure_landscape(embedder, unrelated_pairs=pairs, related_pairs=pairs)

        assert first is second
        assert embedder.query_calls == calls_after_first  # no re-embedding

    def test_use_cache_false_forces_a_fresh_measurement(self) -> None:
        vectors = {"q": [1.0, 0.0, 0.0], "d": _vector_for_cosine(0.5)}
        embedder = _MappedEmbedder(vectors)
        pairs = (("q", "d"),)

        measure_landscape(embedder, unrelated_pairs=pairs, related_pairs=pairs)
        calls_after_first = embedder.query_calls
        measure_landscape(embedder, unrelated_pairs=pairs, related_pairs=pairs, use_cache=False)

        assert embedder.query_calls > calls_after_first


class TestWireShapeHelpers:
    def test_band_stats_doc_shape(self) -> None:
        vectors = {"q": [1.0, 0.0, 0.0], "d": _vector_for_cosine(0.5)}
        embedder = _MappedEmbedder(vectors)
        pairs = (("q", "d"),)
        profile = measure_landscape(
            embedder, unrelated_pairs=pairs, related_pairs=pairs, use_cache=False
        )

        doc = band_stats_doc(profile.unrelated_baseline)
        assert set(doc) == {"meanScore", "minScore", "maxScore", "stdevScore", "sampleSize"}
        assert doc["sampleSize"] == 1

    def test_pair_doc_shape_and_score_scale(self) -> None:
        vectors = {"q": [1.0, 0.0, 0.0], "d": [1.0, 0.0, 0.0]}
        embedder = _MappedEmbedder(vectors)
        pairs = (("q", "d"),)
        profile = measure_landscape(
            embedder, unrelated_pairs=pairs, related_pairs=pairs, use_cache=False
        )

        doc = pair_doc(profile.pairs[0])
        assert set(doc) == {"query", "document", "relation", "cosine", "score"}
        assert doc["cosine"] == pytest.approx(cosine_similarity(vectors["q"], vectors["d"]))
        assert doc["score"] == pytest.approx(1.0)
