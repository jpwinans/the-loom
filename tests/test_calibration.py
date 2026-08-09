"""The closed calibration loop (desire 14): resolve-claim, calibration-profile,
assertion-time reads, and create-entity's CONFIDENCE_OUT_OF_LINE feedback.

Acceptance (a)-(c) from claude-desires.md #14 live here; (d) (calibrated
damping) lives in ``tests/test_calibration_damping.py`` alongside
propagate-credit, and the standalone alerts-provider test lives in
``tests/test_calibration_alerts.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from theloom.cli.registry import run_handler
from theloom.errors import LoomError
from theloom.store.multigraph import MultiGraph
from theloom.timeutil import iso_now

CONF = {"score": 0.8, "basis": "direct_observation"}


def claim(multi: MultiGraph, name: str, **overrides: object) -> dict[str, Any]:
    base: dict[str, object] = {
        "name": name,
        "entityType": "claim",
        "observations": [f"claim about {name}"],
    }
    base.update(overrides)
    result: dict[str, Any] = run_handler("create-entity", base, multi)
    return result


def resolve(
    multi: MultiGraph, claim_id: str, resolution: str, evidence: str = "e"
) -> dict[str, Any]:
    result: dict[str, Any] = run_handler(
        "resolve-claim",
        {"claimId": claim_id, "resolution": resolution, "evidence": evidence},
        multi,
    )
    return result


def profile(multi: MultiGraph, **params: object) -> dict[str, Any]:
    result: dict[str, Any] = run_handler("calibration-profile", params, multi)
    return result


def bucket(result: dict[str, Any], key: str) -> dict[str, Any]:
    buckets: list[dict[str, Any]] = result["buckets"]
    for row in buckets:
        if row["key"] == key:
            return row
    raise AssertionError(f"no bucket {key!r} in {buckets}")


# =============================================================================
# resolve-claim: resolution linkage
# =============================================================================


def test_resolve_claim_creates_outcome_link_and_transitions_status(multi: MultiGraph) -> None:
    c = claim(multi, "Confirmed claim", confidence=CONF)
    result = resolve(multi, c["id"], "confirmed", evidence="it held up")

    assert result["applied"] is True
    assert result["outcome"]["entityType"] == "evidence"
    assert result["relation"]["relationType"] == "resolves"
    assert result["relation"]["from"] == result["outcome"]["id"]
    assert result["relation"]["to"] == c["id"]
    assert result["claim"]["status"] == "active"

    reread = run_handler("read-entity", {"id": c["id"]}, multi)
    assert reread["status"] == "active"


def test_resolve_claim_refuted_retracts(multi: MultiGraph) -> None:
    c = claim(multi, "Refuted claim", confidence=CONF)
    result = resolve(multi, c["id"], "refuted", evidence="proven false")
    assert result["claim"]["status"] == "retracted"


def test_resolve_claim_expired_deprecates(multi: MultiGraph) -> None:
    c = claim(multi, "Expired claim", confidence=CONF)
    result = resolve(multi, c["id"], "expired", evidence="went moot")
    assert result["claim"]["status"] == "deprecated"


def test_resolve_claim_rejects_non_claim_entities(multi: MultiGraph) -> None:
    concept = run_handler(
        "create-entity", {"name": "Not a claim", "entityType": "concept", "observations": []}, multi
    )
    with pytest.raises(LoomError) as excinfo:
        resolve(multi, concept["id"], "confirmed")
    assert excinfo.value.code == "OPERATION_ERROR"


def test_resolve_claim_rejects_unknown_id(multi: MultiGraph) -> None:
    with pytest.raises(LoomError) as excinfo:
        resolve(multi, "00000000-0000-4000-8000-000000000000", "confirmed")
    assert excinfo.value.code == "NOT_FOUND"


def test_resolve_claim_refuses_invalid_transition(multi: MultiGraph) -> None:
    """A retracted (refuted) claim cannot then be confirmed -- update-entity
    itself refuses active<-retracted, and resolve-claim routes through the
    same transition table."""
    c = claim(multi, "Once refuted", confidence=CONF)
    resolve(multi, c["id"], "refuted")
    with pytest.raises(LoomError) as excinfo:
        resolve(multi, c["id"], "confirmed")
    assert excinfo.value.code == "VALIDATION_ERROR"


def test_resolve_claim_hypothesis_also_eligible(multi: MultiGraph) -> None:
    h = run_handler(
        "create-entity",
        {
            "name": "A hypothesis",
            "entityType": "hypothesis",
            "observations": [],
            "confidence": CONF,
        },
        multi,
    )
    result = resolve(multi, h["id"], "confirmed")
    assert result["applied"] is True


# =============================================================================
# calibration-profile: acceptance (a) -- exact Brier/gap over a planted set
# =============================================================================


def test_acceptance_a_exact_brier_and_gap_over_a_planted_set(multi: MultiGraph) -> None:
    """Four claims asserted at 0.8 by 'author-a': three confirmed, one
    refuted, plus one expired claim that must NOT enter the score.

    Hand-computed:
      judged n = 4, meanAsserted = 0.8
      empirical hit rate = 3/4 = 0.75
      Brier = mean((0.8-1)^2 * 3 + (0.8-0)^2 * 1) / 4
            = (3*0.04 + 0.64) / 4 = 0.76 / 4 = 0.19
      gap = meanAsserted - empirical = 0.8 - 0.75 = 0.05
    """
    author = "author-a"
    ids = []
    for i in range(3):
        c = claim(multi, f"Confirmed {i}", confidence=CONF, session=author)
        resolve(multi, c["id"], "confirmed")
        ids.append(c["id"])
    refuted = claim(multi, "Refuted", confidence=CONF, session=author)
    resolve(multi, refuted["id"], "refuted")
    expired = claim(
        multi, "Expired", confidence={"score": 0.9, "basis": "direct_observation"}, session=author
    )
    resolve(multi, expired["id"], "expired")

    result = profile(multi, by="author", minBucketN=1)
    row = bucket(result, author)

    assert row["n"] == 4
    assert row["expiredCount"] == 1
    assert row["meanAssertedConfidence"] == pytest.approx(0.8)
    assert row["empiricalHitRate"] == pytest.approx(0.75)
    assert row["brierScore"] == pytest.approx(0.19)
    assert row["gap"] == pytest.approx(0.05)
    assert row["insufficientData"] is False
    assert result["totalResolvedClaims"] == 5
    assert "notices" not in result


def test_calibration_profile_insufficient_data_below_floor(multi: MultiGraph) -> None:
    author = "sparse-author"
    c = claim(multi, "Only one", confidence=CONF, session=author)
    resolve(multi, c["id"], "confirmed")

    result = profile(multi, by="author", minBucketN=2)
    row = bucket(result, author)
    assert row["insufficientData"] is True
    assert row["n"] == 1
    assert row["meanAssertedConfidence"] is None
    assert row["empiricalHitRate"] is None
    assert row["brierScore"] is None
    assert row["gap"] is None
    codes = {n["code"] for n in result["notices"]}
    assert "INSUFFICIENT_DATA" in codes
    assert any(author in n["message"] for n in result["notices"])


def test_calibration_profile_skips_claims_resolved_without_confidence(multi: MultiGraph) -> None:
    author = "no-confidence-author"
    c = claim(multi, "No confidence", confidence=None, session=author)
    resolve(multi, c["id"], "confirmed")

    result = profile(multi, by="author", minBucketN=1)
    assert result["skippedNoConfidence"] == 1
    # The author still gets a bucket row (nothing is silently dropped from
    # the response), but with zero judged claims -- insufficient, not zero
    # fabricated as a real number.
    row = bucket(result, author)
    assert row["n"] == 0
    assert row["insufficientData"] is True
    assert row["brierScore"] is None


def test_calibration_profile_buckets_by_basis(multi: MultiGraph) -> None:
    a = claim(multi, "Inference claim", confidence={"score": 0.6, "basis": "inference"})
    resolve(multi, a["id"], "confirmed")
    b = claim(multi, "Observed claim", confidence={"score": 0.6, "basis": "direct_observation"})
    resolve(multi, b["id"], "confirmed")

    result = profile(multi, by="basis", minBucketN=1)
    assert bucket(result, "inference")["n"] == 1
    assert bucket(result, "direct_observation")["n"] == 1


def test_calibration_profile_buckets_by_domain(multi: MultiGraph) -> None:
    a = claim(multi, "Eng claim", confidence=CONF, domain="engineering")
    resolve(multi, a["id"], "confirmed")
    b = claim(multi, "Research claim", confidence=CONF, domain="research")
    resolve(multi, b["id"], "confirmed")

    result = profile(multi, by="domain", minBucketN=1)
    assert bucket(result, "engineering")["n"] == 1
    assert bucket(result, "research")["n"] == 1


def test_calibration_profile_window_filters_by_resolution_time(multi: MultiGraph) -> None:
    author = "windowed-author"
    c1 = claim(multi, "Early", confidence=CONF, session=author)
    resolve(multi, c1["id"], "confirmed")

    cutoff = iso_now()

    c2 = claim(multi, "Later", confidence=CONF, session=author)
    resolve(multi, c2["id"], "confirmed")

    windowed = profile(multi, by="author", minBucketN=1, window={"since": cutoff})
    # Only the resolution at/after cutoff (c2) is in the window.
    row = bucket(windowed, author)
    assert row["n"] == 1
    all_time = profile(multi, by="author", minBucketN=1)
    assert bucket(all_time, author)["n"] == 2


def test_calibration_profile_default_by_is_author(multi: MultiGraph) -> None:
    c = claim(multi, "Default bucket", confidence=CONF, session="default-by-author")
    resolve(multi, c["id"], "confirmed")
    result = profile(multi, minBucketN=1)
    assert result["by"] == "author"


def test_calibration_profile_is_read_only(multi: MultiGraph) -> None:
    """Calibration reads never mutate -- re-running the profile does not
    change resolved claims or their status."""
    c = claim(multi, "Stable", confidence=CONF)
    resolve(multi, c["id"], "confirmed")
    before = run_handler("read-entity", {"id": c["id"]}, multi)
    profile(multi, minBucketN=1)
    profile(multi, minBucketN=1)
    after = run_handler("read-entity", {"id": c["id"]}, multi)
    assert before == after


# =============================================================================
# Acceptance (b): assertion-time confidence is read from history, even after
# later updates -- proven by mutating confidence AFTER resolution.
# =============================================================================


def test_acceptance_b_assertion_time_confidence_survives_later_updates(multi: MultiGraph) -> None:
    author = "revised-author"
    c = claim(
        multi,
        "Will be revised",
        confidence={"score": 0.9, "basis": "direct_observation"},
        session=author,
    )
    resolve(multi, c["id"], "confirmed")

    # Mutate the LIVE confidence well after resolution -- calibration must
    # keep reading the ASSERTED (0.9) value, never this new one.
    run_handler(
        "update-entity",
        {"id": c["id"], "confidence": {"score": 0.1, "basis": "calculated"}},
        multi,
    )
    live = run_handler("read-entity", {"id": c["id"]}, multi)
    assert live["confidence"]["score"] == pytest.approx(0.1)

    result = profile(multi, by="author", minBucketN=1)
    row = bucket(result, author)
    assert row["meanAssertedConfidence"] == pytest.approx(0.9)
    assert row["empiricalHitRate"] == pytest.approx(1.0)
    assert row["brierScore"] == pytest.approx(0.01)


# =============================================================================
# Acceptance (c): CONFIDENCE_OUT_OF_LINE fires at the threshold, not below it.
# =============================================================================


def _plant_history(
    multi: MultiGraph,
    *,
    session: str,
    basis: str,
    domain: str,
    n_confirmed: int,
    n_refuted: int,
    score: float,
) -> None:
    for i in range(n_confirmed):
        c = claim(
            multi,
            f"hist-confirmed-{i}",
            confidence={"score": score, "basis": basis},
            session=session,
            domain=domain,
        )
        resolve(multi, c["id"], "confirmed")
    for i in range(n_refuted):
        c = claim(
            multi,
            f"hist-refuted-{i}",
            confidence={"score": score, "basis": basis},
            session=session,
            domain=domain,
        )
        resolve(multi, c["id"], "refuted")


def test_acceptance_c_out_of_line_notice_fires_at_threshold_not_below(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOOM_CALIBRATION_GAP_THRESHOLD", "0.2")
    monkeypatch.setenv("LOOM_CALIBRATION_MIN_BUCKET_N", "5")

    author = "threshold-author"
    # 5 claims at asserted 0.6, all confirmed: empirical hit rate = 1.0.
    # Use a hit rate of 0.6 instead (3 confirmed / 2 refuted) so the gap is
    # controllable against a fresh assertion.
    _plant_history(
        multi,
        session=author,
        basis="inference",
        domain="engineering",
        n_confirmed=3,
        n_refuted=2,
        score=0.6,
    )
    # empirical hit rate for this bucket = 3/5 = 0.6

    # AT the threshold: asserted 0.6 + 0.2 = 0.8 -> gap exactly 0.2 -> fires.
    at_threshold = claim(
        multi,
        "at threshold",
        confidence={"score": 0.8, "basis": "inference"},
        session=author,
        domain="engineering",
    )
    codes_at = {n["code"] for n in at_threshold.get("notices", [])}
    assert "CONFIDENCE_OUT_OF_LINE" in codes_at

    # BELOW the threshold: asserted 0.6 + 0.19 = 0.79 -> gap 0.19 -> silent.
    below_threshold = claim(
        multi,
        "below threshold",
        confidence={"score": 0.79, "basis": "inference"},
        session=author,
        domain="engineering",
    )
    codes_below = {n["code"] for n in below_threshold.get("notices", [])}
    assert "CONFIDENCE_OUT_OF_LINE" not in codes_below


def test_out_of_line_notice_silent_below_the_floor(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Too little history (below the floor) must never fabricate a gap --
    no notice, even for a wildly over-confident assertion."""
    monkeypatch.setenv("LOOM_CALIBRATION_GAP_THRESHOLD", "0.01")
    monkeypatch.setenv("LOOM_CALIBRATION_MIN_BUCKET_N", "5")
    author = "sparse-out-of-line-author"
    _plant_history(
        multi,
        session=author,
        basis="inference",
        domain="engineering",
        n_confirmed=1,
        n_refuted=1,
        score=0.1,
    )
    wild = claim(
        multi,
        "wildly overconfident",
        confidence={"score": 0.99, "basis": "inference"},
        session=author,
        domain="engineering",
    )
    assert "notices" not in wild or all(
        n["code"] != "CONFIDENCE_OUT_OF_LINE" for n in wild["notices"]
    )


def test_out_of_line_notice_never_fires_without_new_confidence(multi: MultiGraph) -> None:
    """create-entity with no confidence at all can't be out of line -- there
    is no asserted score to compare."""
    result = run_handler(
        "create-entity",
        {"name": "no confidence given", "entityType": "claim", "observations": []},
        multi,
    )
    assert "notices" not in result
