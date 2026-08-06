"""The shared cosine helper's degenerate cases.

Callers score a freshly embedded proposal against whatever vectors the graph
already holds, and stored widths are not enforced — a graph carrying vectors
from an older or other model can hand a scorer two vectors of different
length. That is "no comparable signal" (0.0), not a crash: an untyped numpy
ValueError out of a scoring loop is not one of the Loom's error codes.
"""

from __future__ import annotations

import pytest

from theloom.analysis.interestingness import compute_subjective_information_density
from theloom.semantic.embed import cosine_similarity


def test_cosine_similarity_of_equal_length_vectors() -> None:
    # (1,0)·(1,1) / (1 * sqrt(2)) = 1/sqrt(2) = 0.70710678...
    assert cosine_similarity([1.0, 0.0], [1.0, 1.0]) == pytest.approx(0.7071067811865475)


def test_cosine_similarity_of_mismatched_lengths_is_zero() -> None:
    assert cosine_similarity([1.0, 0.0, 0.0], [1.0, 0.0]) == 0.0


def test_cosine_similarity_of_an_empty_vector_is_zero() -> None:
    assert cosine_similarity([], []) == 0.0


def test_information_density_scores_against_a_foreign_width_vector() -> None:
    """A stored vector of the wrong width contributes similarity 0, so a
    proposal that matches nothing comparable scores the maximum novelty 1.0
    instead of raising out of the scoring loop."""
    assert compute_subjective_information_density([1.0, 0.0, 0.0], [[1.0, 0.0]]) == 1.0
