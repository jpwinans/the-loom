"""Work memory: record-outcome and reflect.

The experiential layer is native — an outcome is an evidence entity in the
graph plus supports/questions edges to what it cited, and reflection is a
deterministic aggregation over those edges, not an LLM call.

What is pinned here:

- ``record-outcome`` writes the usage evidence entity in a fixed observation
  shape and the correct edge type per outcome, and refuses unknown citations
  before it writes anything;
- ``reflect`` decays each citation by age with an exact half-life (fed a fixed
  ``asOf`` so the arithmetic never depends on the wall clock), requires
  corroboration before it will call anything preferred, and separates a merely
  contested entity from a corroborated dead end;
- staleness flips when the cited file's content changes under a stored
  fingerprint;
- bad input is refused with typed errors.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from falkordb import FalkorDB
from redis import Redis

from theloom.cli.registry import run_handler
from theloom.errors import NotFoundError, ValidationError
from theloom.store.falkor import FalkorGraphStore
from theloom.store.multigraph import MultiGraph

AS_OF = "2026-08-04T00:00:00.000Z"


@pytest.fixture()
def multi(db: FalkorDB, redis_client: Redis, namespace: str) -> MultiGraph:
    return MultiGraph(db, redis_client, default_graph="default", key_prefix=namespace)


def ent(multi: MultiGraph, name: str, observations: list[str] | None = None) -> str:
    result = run_handler(
        "create-entity",
        {
            "name": name,
            "entityType": "procedure",
            "observations": observations if observations is not None else [f"purpose: {name}"],
        },
        multi,
    )
    return str(result["id"])


def read(multi: MultiGraph, entity_id: str) -> dict[str, Any]:
    doc: dict[str, Any] = run_handler("read-entity", {"id": entity_id}, multi)
    return doc


def observations(multi: MultiGraph, entity_id: str) -> list[str]:
    return [str(text) for text in read(multi, entity_id)["observations"]]


def usage_status(multi: MultiGraph, entity_id: str) -> str | None:
    for text in observations(multi, entity_id):
        if text.startswith("usage_status:"):
            return text
    return None


def usage_record(
    multi: MultiGraph,
    *,
    question: str,
    outcome: str,
    targets: list[str],
    recorded: str,
) -> str:
    """A usage-evidence entity with an injected recording date.

    Written by hand rather than through record-outcome so the decay tests can
    place a citation at an exact age; the shape is exactly what record-outcome
    emits, which is what makes this a contract pin.
    """
    evidence_id = run_handler(
        "create-entity",
        {
            "name": f"usage: {question}",
            "entityType": "evidence",
            "observations": [
                "map_layer: usage",
                f"question: {question}",
                f"outcome: {outcome}",
                f"recorded: {recorded}",
            ],
        },
        multi,
    )["id"]
    relation_type = "supports" if outcome == "useful" else "questions"
    for target in targets:
        run_handler(
            "create-relation",
            {
                "from": evidence_id,
                "to": target,
                "relationType": relation_type,
                "polarity": None,
                "strength": "moderate",
                "evidence": f"Recorded outcome '{outcome}' for question: {question}",
            },
            multi,
        )
    return str(evidence_id)


def reflect(multi: MultiGraph, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"asOf": AS_OF}
    payload.update(overrides)
    result: dict[str, Any] = run_handler("reflect", payload, multi)
    return result


def names(rows: list[dict[str, Any]]) -> set[str]:
    return {str(row["name"]) for row in rows}


# =============================================================================
# record-outcome
# =============================================================================


def test_record_outcome_writes_usage_evidence_and_supports_edges(multi: MultiGraph) -> None:
    target = ent(multi, "resolve_entity_ref")
    other = ent(multi, "EntityFilter")

    result = run_handler(
        "record-outcome",
        {
            "question": "how does name addressing resolve",
            "answer": "through resolve_entity_ref, which pushes down to EntityFilter",
            "entityIds": [target, other],
            "outcome": "useful",
        },
        multi,
    )

    evidence = result["evidence"]
    assert evidence["entityType"] == "evidence"
    texts = [str(text) for text in evidence["observations"]]
    assert texts[0] == "map_layer: usage"
    assert "question: how does name addressing resolve" in texts
    assert "outcome: useful" in texts
    assert any(text.startswith("recorded: ") for text in texts)
    assert any(text.startswith("answer: ") for text in texts)
    assert evidence["provenance"]["sourceType"] == "observation"
    assert evidence["provenance"]["extractor"] == "record-outcome"

    assert {relation["to"] for relation in result["relations"]} == {target, other}
    assert {relation["relationType"] for relation in result["relations"]} == {"supports"}
    assert all(
        "how does name addressing resolve" in relation["evidence"]
        for relation in result["relations"]
    )

    # No embedding is triggered — embedding stays a deliberate, separate call.
    assert evidence.get("embeddingStatus") is None


@pytest.mark.parametrize(
    ("outcome", "relation_type"),
    [("useful", "supports"), ("dead_end", "questions"), ("corrected", "questions")],
)
def test_record_outcome_edge_type_follows_the_outcome(
    multi: MultiGraph, outcome: str, relation_type: str
) -> None:
    target = ent(multi, f"target_{outcome}")
    result = run_handler(
        "record-outcome",
        {
            "question": "q",
            "entityIds": [target],
            "outcome": outcome,
            "correction": "it is actually the other one",
        },
        multi,
    )
    assert [relation["relationType"] for relation in result["relations"]] == [relation_type]


def test_record_outcome_refuses_unknown_citations_without_writing(multi: MultiGraph) -> None:
    missing = "11111111-2222-3333-4444-555555555555"
    with pytest.raises(NotFoundError):
        run_handler(
            "record-outcome",
            {"question": "q", "entityIds": [missing], "outcome": "useful"},
            multi,
        )
    remaining = run_handler("list-entities", {"entityType": "evidence"}, multi)
    assert remaining == []


def test_record_outcome_collapses_a_repeated_citation_to_one_edge(multi: MultiGraph) -> None:
    """One outcome is one experience: citing the same id twice must not vote
    twice, or a single call could satisfy corroboration on its own."""
    target = ent(multi, "cited_twice_in_one_call")

    result = run_handler(
        "record-outcome",
        {"question": "q", "entityIds": [target, target], "outcome": "useful"},
        multi,
    )
    assert [relation["to"] for relation in result["relations"]] == [target]

    reflected = reflect(multi, minCorroboration=2)
    assert names(reflected["preferred"]) == set()
    assert usage_status(multi, target) is None


def test_record_outcome_leaves_no_evidence_behind_when_the_citations_cannot_land(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A citation retracted between the existence check and the write must not
    leave a usage record citing nothing."""
    target = ent(multi, "vanishing")

    def boom(*args: Any, **kwargs: Any) -> list[Any]:
        raise NotFoundError("Entity not found: relation endpoints must exist")

    monkeypatch.setattr(FalkorGraphStore, "create_relations", boom)
    with pytest.raises(NotFoundError):
        run_handler(
            "record-outcome",
            {"question": "q", "entityIds": [target], "outcome": "useful"},
            multi,
        )

    assert run_handler("list-entities", {"entityType": "evidence"}, multi) == []


@pytest.mark.parametrize(
    "payload",
    [
        {"question": "q", "entityIds": [], "outcome": "useful"},
        {"question": "q", "outcome": "useful"},
        {"question": "q", "entityIds": ["not-a-uuid"], "outcome": "useful"},
    ],
)
def test_record_outcome_rejects_bad_input(multi: MultiGraph, payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        run_handler("record-outcome", payload, multi)


def test_record_outcome_rejects_unknown_outcome(multi: MultiGraph) -> None:
    target = ent(multi, "thing")
    with pytest.raises(ValidationError):
        run_handler(
            "record-outcome",
            {"question": "q", "entityIds": [target], "outcome": "brilliant"},
            multi,
        )


# =============================================================================
# reflect — decay and corroboration
# =============================================================================


def test_reflect_decays_citations_by_half_life(multi: MultiGraph) -> None:
    fresh = ent(multi, "fresh_symbol")
    stale_use = ent(multi, "old_symbol")

    usage_record(
        multi, question="q1", outcome="useful", targets=[fresh], recorded="2026-08-04T00:00:00.000Z"
    )
    usage_record(
        multi, question="q2", outcome="useful", targets=[fresh], recorded="2026-08-04T00:00:00.000Z"
    )
    # Exactly two half-lives old at asOf → each citation weighs 0.25.
    usage_record(
        multi,
        question="q3",
        outcome="useful",
        targets=[stale_use],
        recorded="2026-06-05T00:00:00.000Z",
    )
    usage_record(
        multi,
        question="q4",
        outcome="useful",
        targets=[stale_use],
        recorded="2026-06-05T00:00:00.000Z",
    )

    result = reflect(multi, halfLifeDays=30, minCorroboration=2)

    scores = {str(row["name"]): row["score"] for row in result["preferred"]}
    assert scores["fresh_symbol"] == pytest.approx(2.0)
    assert scores["old_symbol"] == pytest.approx(0.5)
    assert result["preferred"][0]["name"] == "fresh_symbol"  # ranked by score, desc


def test_reflect_requires_corroboration_before_calling_something_preferred(
    multi: MultiGraph,
) -> None:
    lonely = ent(multi, "cited_once")
    usage_record(multi, question="q1", outcome="useful", targets=[lonely], recorded=AS_OF)

    result = reflect(multi, minCorroboration=2)
    assert names(result["preferred"]) == set()
    assert usage_status(multi, lonely) is None

    usage_record(multi, question="q2", outcome="useful", targets=[lonely], recorded=AS_OF)
    result = reflect(multi, minCorroboration=2)
    assert names(result["preferred"]) == {"cited_once"}
    status = usage_status(multi, lonely)
    assert status is not None
    assert status.startswith("usage_status: preferred (score 2.00, 2 uses)")


def test_reflect_separates_contested_from_dead_end(multi: MultiGraph) -> None:
    contested = ent(multi, "contested_symbol")
    dead = ent(multi, "dead_symbol")

    # One correction against one use → net negative, but not corroborated.
    usage_record(multi, question="a", outcome="useful", targets=[contested], recorded=AS_OF)
    usage_record(multi, question="b", outcome="corrected", targets=[contested], recorded=AS_OF)
    usage_record(multi, question="c", outcome="corrected", targets=[contested], recorded=AS_OF)
    # Two dead ends and nothing positive → a corroborated dead end.
    usage_record(multi, question="d", outcome="dead_end", targets=[dead], recorded=AS_OF)
    usage_record(multi, question="e", outcome="dead_end", targets=[dead], recorded=AS_OF)

    result = reflect(multi, minCorroboration=2)

    assert names(result["preferred"]) == set()
    assert names(result["contested"]) == {"contested_symbol"}
    assert names(result["deadEnds"]) == {"dead_symbol"}
    contested_status = usage_status(multi, contested)
    dead_status = usage_status(multi, dead)
    assert contested_status is not None and contested_status.startswith("usage_status: contested")
    assert dead_status is not None and dead_status.startswith("usage_status: dead_end")


def test_reflect_replaces_the_previous_usage_status_and_versions_the_entity(
    multi: MultiGraph,
) -> None:
    entity_id = ent(multi, "flipper")
    usage_record(multi, question="a", outcome="useful", targets=[entity_id], recorded=AS_OF)
    usage_record(multi, question="b", outcome="useful", targets=[entity_id], recorded=AS_OF)
    reflect(multi, minCorroboration=2)
    first_version = read(multi, entity_id)["version"]

    usage_record(multi, question="c", outcome="dead_end", targets=[entity_id], recorded=AS_OF)
    usage_record(multi, question="d", outcome="dead_end", targets=[entity_id], recorded=AS_OF)
    usage_record(multi, question="e", outcome="dead_end", targets=[entity_id], recorded=AS_OF)
    reflect(multi, minCorroboration=2)

    texts = observations(multi, entity_id)
    statuses = [text for text in texts if text.startswith("usage_status:")]
    assert len(statuses) == 1
    assert statuses[0].startswith("usage_status: dead_end")
    assert read(multi, entity_id)["version"] > first_version


def test_reflect_counts_one_use_per_record_even_with_duplicate_citation_edges(
    multi: MultiGraph,
) -> None:
    """Corroboration counts distinct usage records, not citation edges."""
    entity_id = ent(multi, "double_cited")
    usage_record(
        multi, question="a", outcome="useful", targets=[entity_id, entity_id], recorded=AS_OF
    )

    result = reflect(multi, minCorroboration=2)
    assert names(result["preferred"]) == set()
    assert usage_status(multi, entity_id) is None

    usage_record(multi, question="b", outcome="useful", targets=[entity_id], recorded=AS_OF)
    result = reflect(multi, minCorroboration=2)
    preferred = {str(row["name"]): row for row in result["preferred"]}
    assert set(preferred) == {"double_cited"}
    assert preferred["double_cited"]["useful"] == 2
    assert preferred["double_cited"]["score"] == pytest.approx(2.0)


def test_reflect_retracts_a_status_it_can_no_longer_justify(multi: MultiGraph) -> None:
    """A reflection that no longer reaches a verdict must clear the previous
    one instead of leaving a contradictory record behind."""
    entity_id = ent(multi, "demoted")
    usage_record(multi, question="a", outcome="useful", targets=[entity_id], recorded=AS_OF)
    usage_record(multi, question="b", outcome="useful", targets=[entity_id], recorded=AS_OF)
    reflect(multi, minCorroboration=2)
    assert usage_status(multi, entity_id) is not None

    result = reflect(multi, minCorroboration=3)
    assert names(result["preferred"]) == set()
    assert names(result["contested"]) == set()
    assert names(result["deadEnds"]) == set()
    assert usage_status(multi, entity_id) is None
    assert result["summary"]["updated"] == 1


def test_reflect_is_a_no_op_when_nothing_was_recorded(multi: MultiGraph) -> None:
    ent(multi, "untouched")
    result = reflect(multi)
    assert result["preferred"] == []
    assert result["contested"] == []
    assert result["deadEnds"] == []
    assert result["stale"] == []
    assert result["summary"]["usageRecords"] == 0


def test_reflect_dry_run_reports_without_writing(multi: MultiGraph) -> None:
    entity_id = ent(multi, "previewed")
    usage_record(multi, question="a", outcome="useful", targets=[entity_id], recorded=AS_OF)
    usage_record(multi, question="b", outcome="useful", targets=[entity_id], recorded=AS_OF)

    result = reflect(multi, minCorroboration=2, dryRun=True)
    assert names(result["preferred"]) == {"previewed"}
    assert result["summary"]["updated"] == 0
    assert usage_status(multi, entity_id) is None


# =============================================================================
# reflect — staleness
# =============================================================================


def test_reflect_flags_an_entity_whose_file_changed_since_verification(
    multi: MultiGraph, tmp_path: Path
) -> None:
    source = tmp_path / "pkg" / "mod.py"
    source.parent.mkdir(parents=True)
    source.write_text("def go():\n    return 1\n")

    entity_id = ent(multi, "go", ["File path: pkg/mod.py", "purpose: does the thing"])
    usage_record(multi, question="a", outcome="useful", targets=[entity_id], recorded=AS_OF)
    usage_record(multi, question="b", outcome="useful", targets=[entity_id], recorded=AS_OF)

    first = reflect(multi, minCorroboration=2, projectPath=str(tmp_path))
    assert first["stale"] == []
    fingerprints = [
        text for text in observations(multi, entity_id) if text.startswith("file_fingerprint:")
    ]
    assert len(fingerprints) == 1

    source.write_text("def go():\n    return 2\n")
    second = reflect(multi, minCorroboration=2, projectPath=str(tmp_path))

    assert names(second["stale"]) == {"go"}
    assert second["stale"][0]["filePath"] == "pkg/mod.py"
    assert "usage_stale: file changed since last verification" in observations(multi, entity_id)


def test_reflect_skips_staleness_without_a_project_path(multi: MultiGraph, tmp_path: Path) -> None:
    entity_id = ent(multi, "unanchored", ["File path: pkg/missing.py"])
    usage_record(multi, question="a", outcome="useful", targets=[entity_id], recorded=AS_OF)
    usage_record(multi, question="b", outcome="useful", targets=[entity_id], recorded=AS_OF)

    result = reflect(multi, minCorroboration=2)
    assert result["stale"] == []
    assert not [
        text for text in observations(multi, entity_id) if text.startswith("file_fingerprint:")
    ]


def test_reflect_rejects_bad_input(multi: MultiGraph) -> None:
    for payload in (
        {"halfLifeDays": 0},
        {"minCorroboration": 0},
        {"asOf": "yesterday"},
    ):
        with pytest.raises(ValidationError):
            run_handler("reflect", payload, multi)


# =============================================================================
# record-outcome → reflect, end to end
# =============================================================================


def test_recorded_outcomes_feed_reflection(multi: MultiGraph) -> None:
    entity_id = ent(multi, "round_trip")
    for question in ("q1", "q2"):
        run_handler(
            "record-outcome",
            {"question": question, "entityIds": [entity_id], "outcome": "useful"},
            multi,
        )
    result = run_handler("reflect", {"minCorroboration": 2}, multi)
    assert names(result["preferred"]) == {"round_trip"}
    assert result["summary"]["usageRecords"] == 2
