"""AnKGE-derived confidence scoring for analogy transfer.

Two-signal mode (default): w1=0.6, w2=0.4 over structural + slippage.
Three-signal (interestingness OR purpose): w1=0.4, w2=0.3, w3/w4=0.3.
Four-signal (both): 0.3 structural / 0.2 slippage / 0.25 interestingness /
0.25 purpose. All inputs are clamped to [0, 1]; the weighted sum is divided by
the total weight and clamped; a total weight of 0 yields 0.
"""

from __future__ import annotations

DEFAULT_TWO_SIGNAL_WEIGHTS = {"w1": 0.6, "w2": 0.4}
DEFAULT_THREE_SIGNAL_WEIGHTS = {"w1": 0.4, "w2": 0.3, "w3": 0.3, "w4": 0.0}
DEFAULT_PURPOSE_THREE_SIGNAL_WEIGHTS = {"w1": 0.4, "w2": 0.3, "w4": 0.3}
DEFAULT_FOUR_SIGNAL_WEIGHTS = {"w1": 0.3, "w2": 0.2, "w3": 0.25, "w4": 0.25}


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def compute_analogy_confidence(
    structural_preservation: float,
    slippage_score: float = 0.5,
    interestingness_score: float | None = None,
    weights: dict[str, float] | None = None,
    purpose_relevance: float | None = None,
) -> float:
    """Confidence in [0, 1] blending structural preservation with up to three
    creative/pragmatic signals, with weight redistribution across active signals."""
    sp = _clamp01(structural_preservation)
    ss = _clamp01(slippage_score)
    w = weights or {}

    has_purpose = purpose_relevance is not None
    has_interestingness = interestingness_score is not None

    if has_purpose and has_interestingness:
        assert interestingness_score is not None
        assert purpose_relevance is not None
        is_ = _clamp01(interestingness_score)
        pr = _clamp01(purpose_relevance)
        w1 = w.get("w1", DEFAULT_FOUR_SIGNAL_WEIGHTS["w1"])
        w2 = w.get("w2", DEFAULT_FOUR_SIGNAL_WEIGHTS["w2"])
        w3 = w.get("w3", DEFAULT_FOUR_SIGNAL_WEIGHTS["w3"])
        w4 = w.get("w4", DEFAULT_FOUR_SIGNAL_WEIGHTS["w4"])
        total_weight = w1 + w2 + w3 + w4
        if total_weight == 0:
            return 0.0
        return _clamp01((w1 * sp + w2 * ss + w3 * is_ + w4 * pr) / total_weight)

    if has_purpose:
        assert purpose_relevance is not None
        pr = _clamp01(purpose_relevance)
        w1 = w.get("w1", DEFAULT_PURPOSE_THREE_SIGNAL_WEIGHTS["w1"])
        w2 = w.get("w2", DEFAULT_PURPOSE_THREE_SIGNAL_WEIGHTS["w2"])
        w4 = w.get("w4", DEFAULT_PURPOSE_THREE_SIGNAL_WEIGHTS["w4"])
        total_weight = w1 + w2 + w4
        if total_weight == 0:
            return 0.0
        return _clamp01((w1 * sp + w2 * ss + w4 * pr) / total_weight)

    if has_interestingness:
        assert interestingness_score is not None
        is_ = _clamp01(interestingness_score)
        w1 = w.get("w1", DEFAULT_THREE_SIGNAL_WEIGHTS["w1"])
        w2 = w.get("w2", DEFAULT_THREE_SIGNAL_WEIGHTS["w2"])
        w3 = w.get("w3", DEFAULT_THREE_SIGNAL_WEIGHTS["w3"])
        total_weight = w1 + w2 + w3
        if total_weight == 0:
            return 0.0
        return _clamp01((w1 * sp + w2 * ss + w3 * is_) / total_weight)

    w1 = w.get("w1", DEFAULT_TWO_SIGNAL_WEIGHTS["w1"])
    w2 = w.get("w2", DEFAULT_TWO_SIGNAL_WEIGHTS["w2"])
    total_weight = w1 + w2
    if total_weight == 0:
        return 0.0
    return _clamp01((w1 * sp + w2 * ss) / total_weight)
