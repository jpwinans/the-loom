"""Sliced Wasserstein distance for semantic component signatures.

Covers the sliced-Wasserstein distance and the semantic half of signature
comparison (semantic far-analogy candidate detection).

The distance is computed with POT's ``ot.sliced_wasserstein_distance``
(n_projections=20, seed=42) rather than a hand-rolled projector. POT uses
uniform-weight optimal transport between the empirical distributions (no
interpolation) with its own seeded projection generator, so the distances are
ranking-meaningful but not reproducible to the byte — this path is rank-only.

Edge cases:
- both matrices empty -> 0.0
- exactly one empty -> mean L2 norm of the non-empty matrix's rows
- feature dimension 0 -> 0.0
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import ot  # type: ignore[import-untyped]

DEFAULT_NUM_PROJECTIONS = 20
DEFAULT_WASSERSTEIN_SEED = 42
DEFAULT_TOP_N = 10


def sliced_wasserstein_distance(
    matrix_a: list[list[float]],
    matrix_b: list[list[float]],
    n_projections: int = 20,
    seed: int = 42,
) -> float:
    """Sliced Wasserstein distance between two point clouds (rows = samples)."""
    if not matrix_a and not matrix_b:
        return 0.0

    if not matrix_a or not matrix_b:
        # One empty, one not: distance from the origin (mean L2 norm of the rows).
        non_empty = matrix_a if matrix_a else matrix_b
        total = 0.0
        for vector in non_empty:
            total += math.sqrt(sum(x * x for x in vector))
        return total / len(non_empty)

    dimensions = len(matrix_a[0])
    if dimensions == 0:
        return 0.0

    xs = np.asarray(matrix_a, dtype=np.float64)
    xt = np.asarray(matrix_b, dtype=np.float64)
    distance = ot.sliced_wasserstein_distance(xs, xt, n_projections=n_projections, seed=seed)
    return float(distance)


def compare_semantic_component_signatures(
    sig1: dict[str, Any], sig2: dict[str, Any], options: dict[str, Any] | None = None
) -> float:
    """Sliced Wasserstein distance between two semantic component signature
    matrices (0.0 when both are empty)."""
    options = options or {}
    matrix1 = sig1["signatureMatrix"]
    matrix2 = sig2["signatureMatrix"]

    if not matrix1 and not matrix2:
        return 0.0

    num_projections = _opt_int(options, "numProjections", DEFAULT_NUM_PROJECTIONS)
    seed = _opt_int(options, "seed", DEFAULT_WASSERSTEIN_SEED)
    return sliced_wasserstein_distance(matrix1, matrix2, num_projections, seed)


def find_semantic_far_analogy_candidates(
    semantic_sigs: list[dict[str, Any]], options: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """All pairs of semantic component signatures ranked by Wasserstein distance
    (higher distance = better far-analogy). Candidate shape:
    {"sourceComponent", "targetComponent", "semanticDistance", "farAnalogyScore"}.

    Rank-only: ordering is meaningful, but absolute scores are not exact
    (POT's projector produces its own numeric scale).
    """
    options = options or {}
    if len(semantic_sigs) < 2:
        return []

    top_n = _opt_int(options, "topN", DEFAULT_TOP_N)
    num_projections = _opt_int(options, "numProjections", DEFAULT_NUM_PROJECTIONS)
    seed = _opt_int(options, "seed", DEFAULT_WASSERSTEIN_SEED)

    candidates: list[dict[str, Any]] = []

    for i in range(len(semantic_sigs) - 1):
        for j in range(i + 1, len(semantic_sigs)):
            sig1 = semantic_sigs[i]
            sig2 = semantic_sigs[j]

            semantic_distance = compare_semantic_component_signatures(
                sig1, sig2, {"numProjections": num_projections, "seed": seed}
            )

            candidates.append(
                {
                    "sourceComponent": sig1,
                    "targetComponent": sig2,
                    "semanticDistance": semantic_distance,
                    "farAnalogyScore": semantic_distance,
                }
            )

    candidates.sort(key=lambda c: -c["farAnalogyScore"])
    return candidates[:top_n]


def _opt_int(options: dict[str, Any], key: str, default: int) -> int:
    value = options.get(key)
    return default if value is None else value
