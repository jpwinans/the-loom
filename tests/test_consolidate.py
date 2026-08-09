"""``consolidate`` / ``since-last-session`` (desire 13, Part 6) and the
alerts seam (``theloom.composites.alerts.collect_alerts``): acceptance tests
(a)-(d) plus the pass-level and seam-level unit tests, all dispatched
through ``theloom.cli.registry.run_handler`` the way a real CLI invocation
does, matching ``tests/test_worlds.py``'s own style for Part 5.
"""

from __future__ import annotations

from typing import Any

from theloom.cli.registry import run_handler
from theloom.composites import alerts as alerts_composite
from theloom.composites.consolidate import _pass_analogy, _resolve_passes
from theloom.composites.since_last_session import MAX_ENVELOPE_CHARS, _shrink_to_budget
from theloom.store.multigraph import MultiGraph

# =============================================================================
# Helpers (mirrors tests/test_worlds.py's own create/relate helpers)
# =============================================================================


def create(multi: MultiGraph, graph: str, name: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "graph": graph,
        "name": name,
        "entityType": "concept",
        "observations": [f"observation about {name}"],
    }
    payload.update(overrides)
    result: dict[str, Any] = run_handler("create-entity", payload, multi)
    return result


def relate(
    multi: MultiGraph,
    graph: str,
    from_id: str,
    to_id: str,
    relation_type: str = "related_to",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "graph": graph,
        "from": from_id,
        "to": to_id,
        "relationType": relation_type,
        "strength": "moderate",
        "polarity": None,
        "evidence": None,
    }
    result: dict[str, Any] = run_handler("create-relation", payload, multi)
    return result


_TRANSITIVE_RULE = {
    "name": "contradiction-propagates-through-support",
    "description": "If A contradicts B and B supports C, A contradicts C too.",
    "conditions": [
        {"from": "?a", "to": "?b", "relationType": "contradicts"},
        {"from": "?b", "to": "?c", "relationType": "supports"},
    ],
    "conclusion": {
        "from": "?a",
        "to": "?c",
        "relationType": "contradicts",
        "strength": "moderate",
        "evidence": "derived-transitive",
        "polarity": None,
    },
    "enabled": True,
}


def _plant_scenario(multi: MultiGraph, graph: str) -> dict[str, str]:
    """A transitive contradiction (only visible after run-inference) plus a
    structural gap (a claim with zero supporting evidence) -- acceptance
    (a)'s fixture, shared by several tests below."""
    target = create(multi, graph, "TargetClaim", entityType="claim")
    a = create(multi, graph, "A")
    b = create(multi, graph, "B")
    e1 = create(multi, graph, "E1", entityType="evidence")
    gap_claim = create(multi, graph, "GapClaim", entityType="claim")

    relate(multi, graph, e1["id"], target["id"], "supports")
    relate(multi, graph, b["id"], target["id"], "supports")
    relate(multi, graph, a["id"], b["id"], "contradicts")

    run_handler("inference-rule-create", {"graph": graph, "rule": _TRANSITIVE_RULE}, multi)

    return {
        "targetId": target["id"],
        "aId": a["id"],
        "bId": b["id"],
        "e1Id": e1["id"],
        "gapClaimId": gap_claim["id"],
    }


def _events(multi: MultiGraph, graph: str) -> list[tuple[str, dict[str, Any]]]:
    return [(e.type, e.payload) for e in multi.event_log(graph).read_all()]


# =============================================================================
# Acceptance (a): planted transitive contradiction + structural gap
# =============================================================================


def test_acceptance_a_dream_world_contains_tension_and_hypothesis_main_untouched(
    multi: MultiGraph,
) -> None:
    graph = "g"
    ids = _plant_scenario(multi, graph)
    events_before = _events(multi, graph)

    result = run_handler("consolidate", {"graph": graph}, multi)

    assert result["applied"] is True
    assert result["graph"] == graph
    assert result["parentWorld"] == "main"
    world_id = result["worldId"]
    assert world_id.startswith("world-")
    assert result["dreamWorld"].startswith("dream-")

    report = result["report"]
    contradiction = report["passes"]["contradiction"]
    assert contradiction["transitiveCount"] >= 1

    hypothesis = report["passes"]["hypothesis"]
    assert hypothesis["underEvidencedCount"] >= 1

    # The dream world's own store carries the tension + hypothesis entities.
    world_store = multi.get_store(None, world=world_id)
    tensions = world_store.list_entities()
    tension_rows = [e for e in tensions if e.entity_type.value == "tension"]
    hypothesis_rows = [e for e in tensions if e.entity_type.value == "hypothesis"]
    assert any(row.name == "Tension: TargetClaim" for row in tension_rows), [
        row.name for row in tension_rows
    ]
    assert any(
        ids["gapClaimId"] in obs
        for row in hypothesis_rows
        for obs in row.observations
        if obs.startswith("__consolidation_finding_json:")
    )

    # main is untouched: same replayed event log, byte for byte.
    events_after = _events(multi, graph)
    assert events_before == events_after

    # credit pass has nothing to diff against on a graph's first consolidation.
    assert report["passes"]["credit"]["skipped"] is True
    assert any(n["code"] == "CONSOLIDATION_PASS_SKIPPED" for n in result.get("notices", []))


def test_consolidate_defaults_graph_and_respects_passes_filter(multi: MultiGraph) -> None:
    graph = multi.default_graph
    create(multi, graph, "Solo")
    result = run_handler("consolidate", {"passes": ["staleness"]}, multi)
    assert set(result["report"]["passes"]) == {"staleness"}
    assert result["report"]["counts"].get("contradiction") is None


# =============================================================================
# Acceptance (b): since-last-session returns the report and diff handle
# =============================================================================


def test_acceptance_b_since_last_session_returns_report_and_diff_handle(
    multi: MultiGraph,
) -> None:
    graph = "g"
    _plant_scenario(multi, graph)
    consolidated = run_handler("consolidate", {"graph": graph}, multi)
    world_id = consolidated["worldId"]

    surface = run_handler("since-last-session", {"graph": graph}, multi)

    assert surface["graph"] == graph
    assert len(surface["unreviewedDreams"]) == 1
    dream = surface["unreviewedDreams"][0]
    assert dream["worldId"] == world_id
    assert dream["report"]["worldId"] == world_id
    assert dream["diffWorldsHandle"] == {"a": "main", "b": world_id}
    assert dream["diffSummary"]["changeCount"] > 0
    assert surface["sinceLastConsolidation"] == dream["forkedAt"]
    assert "calibrationAlerts" in surface


def test_since_last_session_with_no_history_notices_and_stays_usable(multi: MultiGraph) -> None:
    graph = "g"
    create(multi, graph, "Solo")
    surface = run_handler("since-last-session", {"graph": graph}, multi)
    assert surface["unreviewedDreams"] == []
    assert surface["sinceLastConsolidation"] is None
    assert any(n["code"] == "NO_CONSOLIDATION_HISTORY" for n in surface.get("notices", []))


# =============================================================================
# Acceptance (c): selective merge grafts exactly the endorsed entities
# =============================================================================


def test_acceptance_c_selective_merge_grafts_exactly_the_endorsed_entity(
    multi: MultiGraph,
) -> None:
    graph = "g"
    _plant_scenario(multi, graph)
    result = run_handler("consolidate", {"graph": graph}, multi)
    world_id = result["worldId"]

    tension_finding = next(
        f for f in result["report"]["topFindings"] if f["pass"] == "contradiction"
    )
    hypothesis_finding = next(
        f for f in result["report"]["topFindings"] if f["pass"] == "hypothesis"
    )
    tension_id = tension_finding["entityId"]
    hypothesis_id = hypothesis_finding["entityId"]

    merge = run_handler(
        "merge-world",
        {"from": world_id, "strategy": "select", "entityIds": [tension_id]},
        multi,
    )
    assert merge["applied"] is True
    applied_ids = {row["entityId"] for row in merge["appliedEntities"]}
    assert applied_ids == {tension_id}

    main_store = multi.get_store(graph)
    assert main_store.read_entity(tension_id) is not None
    assert main_store.read_entity(hypothesis_id) is None


# =============================================================================
# Acceptance (d): two consolidations without an intervening merge are
# independent, not compounding
# =============================================================================


def test_acceptance_d_two_consolidations_are_independent_not_compounding(
    multi: MultiGraph,
) -> None:
    graph = "g"
    _plant_scenario(multi, graph)

    first = run_handler("consolidate", {"graph": graph}, multi)
    second = run_handler("consolidate", {"graph": graph}, multi)

    assert first["worldId"] != second["worldId"]
    assert first["reportEntityId"] != second["reportEntityId"]

    first_tension = next(f for f in first["report"]["topFindings"] if f["pass"] == "contradiction")
    second_store = multi.get_store(None, world=second["worldId"])
    # The second dream forked fresh from `main` -- main never saw the first
    # dream's writes, so the first dream's tension entity is invisible here.
    assert second_store.read_entity(first_tension["entityId"]) is None

    surface = run_handler("since-last-session", {"graph": graph}, multi)
    assert {d["worldId"] for d in surface["unreviewedDreams"]} == {
        first["worldId"],
        second["worldId"],
    }


# =============================================================================
# Alerts seam: calibration alerts flow through collect_alerts now that
# theloom.operations.calibration_alerts is merged (the seam's joint behavior)
# =============================================================================


def test_collect_alerts_includes_calibration_alerts(multi: MultiGraph) -> None:
    graph = "g"
    claim = create(
        multi,
        graph,
        "Seam Claim",
        entityType="claim",
        confidence={"score": 0.8, "basis": "inference"},
    )
    run_handler(
        "resolve-claim",
        {
            "graph": graph,
            "claimId": claim["id"],
            "resolution": "confirmed",
            "evidence": "seam joint-behavior test",
        },
        multi,
    )

    alerts = alerts_composite.collect_alerts(graph, multi, since=None)
    codes = [a["code"] for a in alerts]
    assert "CLAIM_RESOLVED" in codes
    resolved = next(a for a in alerts if a["code"] == "CLAIM_RESOLVED")
    assert claim["id"] in resolved["entityIds"]


def test_dream_expiry_alert_fires_within_the_warning_window(multi: MultiGraph) -> None:
    graph = "g"
    create(multi, graph, "Solo")
    fork = run_handler("fork-world", {"graph": graph, "ttlSeconds": 60}, multi)

    alerts = alerts_composite.collect_alerts(graph, multi, since=None)
    codes = [a["code"] for a in alerts]
    assert "DREAM_EXPIRING_SOON" in codes
    matching = next(a for a in alerts if a["code"] == "DREAM_EXPIRING_SOON")
    assert matching["data"]["worldId"] == fork["worldId"]
    assert matching["severity"] == "warning"


def test_dream_expiry_alert_silent_for_a_fresh_week_long_ttl(multi: MultiGraph) -> None:
    graph = "g"
    _plant_scenario(multi, graph)
    result = run_handler("consolidate", {"graph": graph}, multi)

    alerts = alerts_composite.collect_alerts(graph, multi, since=None)
    ids = [a["data"]["worldId"] for a in alerts if a["code"] == "DREAM_EXPIRING_SOON"]
    assert result["worldId"] not in ids


# =============================================================================
# Pass-level unit tests not covered by the acceptance flow
# =============================================================================


def test_analogy_pass_skips_with_notice_when_too_few_domains(multi: MultiGraph) -> None:
    graph = "g"
    create(multi, graph, "OnlyOne")
    outcome = _pass_analogy(graph, multi, 20)
    assert outcome["ran"] is False
    assert outcome["skipped"] is True
    assert "domain" in outcome["skipReason"]


def test_isolated_entity_survives_a_default_all_passes_run(multi: MultiGraph) -> None:
    """Regression (round 2 critic finding): staleness runs before hypothesis
    in canonical pass order and links its own insight to nearly every stale
    entity via a `related_to` edge -- that dream-authored edge must not make
    an otherwise-isolated entity look structurally connected by the time the
    hypothesis pass looks. Planting many stale-but-connected entities plus
    one genuinely isolated one and running every pass (the exact shape the
    critic's transcript used, not just the single-pass call) is what would
    have let the bug hide: passes=["hypothesis"] alone never exercised the
    staleness pass's side effect at all."""
    graph = "g"
    hub = create(multi, graph, "Hub")
    for i in range(3):
        satellite = create(multi, graph, f"Satellite{i}")
        relate(multi, graph, hub["id"], satellite["id"])
    isolated = create(multi, graph, "TrulyIsolated")

    result = run_handler("consolidate", {"graph": graph}, multi)

    hypothesis = result["report"]["passes"]["hypothesis"]
    assert hypothesis["isolatedCount"] >= 1, hypothesis
    isolated_findings = [
        f
        for f in result["report"]["topFindings"]
        if f["pass"] == "hypothesis" and f.get("gapType") == "isolated"
    ]
    assert any(f["targetEntityId"] == isolated["id"] for f in isolated_findings), isolated_findings

    # Same fixture, hypothesis run alone -- must find the same isolated
    # entity, proving the all-passes run isn't merely lucky.
    only_hypothesis = run_handler("consolidate", {"graph": graph, "passes": ["hypothesis"]}, multi)
    assert only_hypothesis["report"]["passes"]["hypothesis"]["isolatedCount"] >= 1


def test_resolve_passes_default_and_canonical_order() -> None:
    assert _resolve_passes(None) == [
        "contradiction",
        "staleness",
        "motifs",
        "hypothesis",
        "analogy",
        "credit",
    ]
    assert _resolve_passes(["credit", "contradiction"]) == ["contradiction", "credit"]
    assert _resolve_passes([]) == []


def test_shrink_to_budget_marks_truncated_and_fits_the_cap() -> None:
    envelope: dict[str, Any] = {
        "graph": "g",
        "generatedAt": "2026-01-01T00:00:00.000Z",
        "sinceLastConsolidation": None,
        "unreviewedDreams": [
            {
                "worldId": f"world-{i}",
                "name": f"dream-{i}",
                "forkedAt": "2026-01-01T00:00:00.000Z",
                "expiresAt": None,
                "report": {
                    "worldId": f"world-{i}",
                    "generatedAt": "2026-01-01T00:00:00.000Z",
                    "counts": {"contradiction": 1},
                    "totalFindings": 1,
                    "topFindings": [{"pass": "contradiction", "entityId": "x" * 40}] * 20,
                },
                "diffSummary": {"changeCount": 1},
                "diffWorldsHandle": {"a": "main", "b": f"world-{i}"},
            }
            for i in range(10)
        ],
        "recentContradictions": [{"entityId": f"c-{i}", "note": "x" * 200} for i in range(50)],
        "calibrationAlerts": [{"code": "X", "message": "y" * 200} for i in range(50)],
    }
    shrunk, truncated = _shrink_to_budget(envelope)
    assert truncated is True
    import json

    assert len(json.dumps(shrunk, default=str)) <= MAX_ENVELOPE_CHARS + 2000  # last-resort slack
