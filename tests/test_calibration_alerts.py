"""Standalone test for ``theloom.operations.calibration_alerts.provide_alerts``
-- the fixed-contract seam ``since-last-session``/``theloom.composites.alerts``
try-imports.

Deliberately imports nothing from any composite or alerts-aggregation
machinery: only ``theloom.operations.calibration`` (this module's own
building blocks) and the CLI registry (to drive resolve-claim/create-entity
the same way a real caller would). This proves the provider works correctly
in complete isolation from whatever calls it.
"""

from __future__ import annotations

from typing import Any

import pytest

from theloom.cli.registry import run_handler
from theloom.operations.calibration_alerts import provide_alerts
from theloom.store.multigraph import MultiGraph
from theloom.timeutil import iso_now


def claim(multi: MultiGraph, name: str, **overrides: object) -> dict[str, Any]:
    base: dict[str, object] = {
        "name": name,
        "entityType": "claim",
        "observations": [f"claim about {name}"],
        "confidence": {"score": 0.8, "basis": "direct_observation"},
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


def test_provide_alerts_empty_graph_returns_nothing(multi: MultiGraph) -> None:
    assert provide_alerts("default", multi, None) == []


def test_provide_alerts_reports_a_newly_resolved_claim(multi: MultiGraph) -> None:
    c = claim(multi, "Freshly resolved")
    resolve(multi, c["id"], "confirmed", evidence="checked out")

    alerts = provide_alerts(multi.default_graph, multi, None)
    resolved = [a for a in alerts if a["code"] == "CLAIM_RESOLVED"]
    assert len(resolved) == 1
    assert resolved[0]["severity"] == "info"
    assert resolved[0]["entityIds"] == [c["id"]]
    assert resolved[0]["entityNames"] == [c["name"]]
    assert resolved[0]["data"]["resolution"] == "confirmed"


def test_provide_alerts_since_excludes_earlier_resolutions(multi: MultiGraph) -> None:
    early = claim(multi, "Early")
    resolve(multi, early["id"], "confirmed")

    cutoff = iso_now()

    later = claim(multi, "Later")
    resolve(multi, later["id"], "refuted")

    alerts = provide_alerts(multi.default_graph, multi, cutoff)
    resolved_ids = {a["entityIds"][0] for a in alerts if a["code"] == "CLAIM_RESOLVED"}
    assert resolved_ids == {later["id"]}


def test_provide_alerts_flags_a_calibration_gap_freshly_evidenced(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOOM_CALIBRATION_GAP_THRESHOLD", "0.1")
    monkeypatch.setenv("LOOM_CALIBRATION_MIN_BUCKET_N", "2")

    author = "gap-author"
    for i in range(2):
        c = claim(
            multi,
            f"gap-{i}",
            confidence={"score": 0.9, "basis": "direct_observation"},
            session=author,
        )
        resolve(multi, c["id"], "refuted")  # asserted 0.9, always wrong: huge gap

    alerts = provide_alerts(multi.default_graph, multi, None)
    gap_alerts = [a for a in alerts if a["code"] == "CALIBRATION_GAP"]
    assert len(gap_alerts) == 1
    assert gap_alerts[0]["severity"] == "warning"
    assert gap_alerts[0]["data"]["author"] == author
    assert gap_alerts[0]["data"]["gap"] > 0


def test_provide_alerts_gap_silent_when_not_freshly_evidenced(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bucket that has always been over threshold, with nothing resolved
    since the cutoff, must not be reported as newly crossed."""
    monkeypatch.setenv("LOOM_CALIBRATION_GAP_THRESHOLD", "0.1")
    monkeypatch.setenv("LOOM_CALIBRATION_MIN_BUCKET_N", "2")

    author = "stale-gap-author"
    for i in range(2):
        c = claim(
            multi,
            f"stale-gap-{i}",
            confidence={"score": 0.9, "basis": "direct_observation"},
            session=author,
        )
        resolve(multi, c["id"], "refuted")

    cutoff = iso_now()
    alerts = provide_alerts(multi.default_graph, multi, cutoff)
    gap_alerts = [a for a in alerts if a["code"] == "CALIBRATION_GAP"]
    assert gap_alerts == []


def test_provide_alerts_gap_silent_below_the_floor(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOOM_CALIBRATION_GAP_THRESHOLD", "0.01")
    monkeypatch.setenv("LOOM_CALIBRATION_MIN_BUCKET_N", "5")
    author = "sparse-gap-author"
    c = claim(
        multi, "sparse", confidence={"score": 0.9, "basis": "direct_observation"}, session=author
    )
    resolve(multi, c["id"], "refuted")

    alerts = provide_alerts(multi.default_graph, multi, None)
    assert [a for a in alerts if a["code"] == "CALIBRATION_GAP"] == []


def test_provide_alerts_never_mutates(multi: MultiGraph) -> None:
    c = claim(multi, "untouched")
    resolve(multi, c["id"], "confirmed")
    before = run_handler("read-entity", {"id": c["id"]}, multi)
    provide_alerts(multi.default_graph, multi, None)
    provide_alerts(multi.default_graph, multi, None)
    after = run_handler("read-entity", {"id": c["id"]}, multi)
    assert before == after
