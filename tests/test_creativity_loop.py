"""Creativity-Loop composite: the documented six-step cycle must actually run.

The command used to build its config, throw it away and raise a typed
OPERATION_ERROR — honest about the missing orchestration, but a registered
command that can never succeed. It now composes the primitives that already
exist (``explore-frontier`` for the frontier ranking, ``far-analogy-retrieval``
for retrieve/transfer/score, the analogy trigger queue for its status) into a
real multi-cycle loop with cross-cycle bookkeeping, and every assertion below
pins observable, non-stub output: real proposals, real accept/reject counts,
real termination reasons.
"""

from __future__ import annotations

import pytest
from falkordb import FalkorDB
from redis import Redis

from theloom.composites.creativity_loop import (
    CreativityLoopInput,
    _map_to_config,
    creativity_loop,
)
from theloom.model import EntityCreate, RelationCreate
from theloom.store.multigraph import MultiGraph


@pytest.fixture()
def multi(db: FalkorDB, redis_client: Redis, namespace: str) -> MultiGraph:
    return MultiGraph(db, redis_client, default_graph="default", key_prefix=namespace)


def _seed_two_domains(multi: MultiGraph) -> None:
    """Two disconnected, structurally similar, semantically unrelated regions:
    a four-link hydraulic chain and a three-link electrical chain. That is the
    shape far-analogy retrieval is built to find (structural similarity 0.58,
    semantic dissimilarity 1.0) and CWSG transfer turns into proposals."""
    store = multi.get_store()

    def entity(name: str) -> str:
        created = store.create_entity(
            EntityCreate.model_validate(
                {"name": name, "entityType": "concept", "observations": [f"{name} observation"]}
            )
        )
        return created.id

    def relation(from_id: str, to_id: str) -> None:
        store.create_relation(
            RelationCreate.model_validate(
                {
                    "from": from_id,
                    "to": to_id,
                    "relationType": "causes",
                    "polarity": "+",
                    "strength": "moderate",
                    "evidence": "seed",
                }
            )
        )

    pump, pipe, reservoir, valve = (entity(n) for n in ("Pump", "Pipe", "Reservoir", "Valve"))
    relation(pump, pipe)
    relation(pipe, reservoir)
    relation(reservoir, valve)

    battery, wire, resistor = (entity(n) for n in ("Battery", "Wire", "Resistor"))
    relation(battery, wire)
    relation(wire, resistor)


def test_loop_runs_real_cycles_with_non_stub_results(multi: MultiGraph) -> None:
    _seed_two_domains(multi)

    result = creativity_loop(CreativityLoopInput.model_validate({"maxCycles": 2}), multi)

    cycles = result["cycles"]
    assert len(cycles) >= 1
    first = cycles[0]
    assert first["cycle"] == 1
    # explore: the frontier ranking really ran over both regions.
    assert first["explore"]["regionsRanked"] == 2
    assert first["explore"]["error"] is None
    assert first["explore"]["topRegion"]["entityNames"]
    # retrieve/transfer/score: real far-analogy work, not zeros.
    assert first["retrieve"]["candidatePairs"] >= 1
    assert first["retrieve"]["newPairs"] >= 1
    assert first["transfer"]["proposalsGenerated"] >= 1
    assert first["score"]["proposalsScored"] >= 1
    # accept/reject: proposals were actually judged against the threshold.
    assert first["accept"]["accepted"] + first["accept"]["rejected"] >= 1
    # learn: the component-pair archive gained credit.
    assert first["learn"]["archiveSize"] >= 1

    accepted = result["acceptedProposals"]
    assert accepted, "a seeded far-analogy graph must yield at least one accepted proposal"
    for proposal in accepted:
        assert proposal["entity"]["name"]
        assert proposal["cycle"] >= 1
        assert 0 <= proposal["effectiveScore"] <= 1

    archive = result["componentPairArchive"]
    assert archive and archive[0]["visits"] >= 1
    assert archive[0]["proposalsCredited"] >= 1

    # Envelope: every section succeeded, and the summary is a real report.
    envelope = result["composite"]
    assert envelope["metadata"]["sectionsFailed"] == 0
    assert "Creativity Loop" in result["summary"]
    assert str(len(cycles)) in result["summary"]


def test_repeat_cycles_are_duplicates_and_the_loop_stops_early(multi: MultiGraph) -> None:
    """The loop is deterministic and does not mutate the graph, so a second
    cycle re-derives the same proposals. They must be recognised as already
    seen (duplicates, not new accepts) and the consecutive-empty-cycle limit
    must stop the loop before ``maxCycles``."""
    _seed_two_domains(multi)

    result = creativity_loop(
        CreativityLoopInput.model_validate({"maxCycles": 6, "maxEmptyCycles": 1}), multi
    )

    cycles = result["cycles"]
    assert len(cycles) == 2, "cycle 2 accepts nothing new, so the limit of 1 stops the loop"
    second = cycles[1]
    assert second["accept"]["duplicates"] >= 1
    assert second["accept"]["accepted"] == 0
    assert second["retrieve"]["newPairs"] == 0
    assert result["stopReason"] == "consecutive-empty-cycles"


def test_plateau_detection_stops_when_no_new_component_pairs_appear(multi: MultiGraph) -> None:
    _seed_two_domains(multi)

    result = creativity_loop(
        CreativityLoopInput.model_validate({"maxCycles": 6, "detectPlateau": True}), multi
    )

    assert result["stopReason"] == "plateau-no-new-pairs"
    assert len(result["cycles"]) == 2
    assert result["composite"]["result"]["learn"]["data"]["plateauDetected"] is True


def test_exploration_ranking_gates_acceptance(multi: MultiGraph) -> None:
    """explore-frontier is load-bearing, not decoration: with the frontier
    narrowed to a single region, proposals whose transferred relations land
    outside it are rejected off-frontier instead of accepted."""
    _seed_two_domains(multi)

    wide = creativity_loop(CreativityLoopInput.model_validate({"maxCycles": 1}), multi)
    narrow = creativity_loop(
        CreativityLoopInput.model_validate({"maxCycles": 1, "exploreTopK": 1}), multi
    )

    assert wide["cycles"][0]["accept"]["offFrontier"] == 0
    assert narrow["cycles"][0]["accept"]["offFrontier"] >= 1
    assert len(narrow["acceptedProposals"]) < len(wide["acceptedProposals"])


def test_empty_graph_reports_an_honest_empty_loop(multi: MultiGraph) -> None:
    result = creativity_loop(CreativityLoopInput.model_validate({"maxCycles": 3}), multi)

    assert result["acceptedProposals"] == []
    assert result["componentPairArchive"] == []
    assert result["stopReason"] == "consecutive-empty-cycles"
    # Honest, not fabricated: the retrieve step reports why it produced nothing.
    assert result["cycles"][0]["retrieve"]["error"] is not None


def test_boundaries_are_stated_in_the_output(multi: MultiGraph) -> None:
    _seed_two_domains(multi)
    result = creativity_loop(CreativityLoopInput.model_validate({"maxCycles": 1}), multi)

    boundaries = " ".join(result["boundaries"]).lower()
    assert "trigger queue" in boundaries
    assert "llm" in boundaries


def test_config_mapping_still_applies_documented_defaults() -> None:
    config = _map_to_config(CreativityLoopInput())
    assert config["maxCycles"] == 10
    assert config["interestingnessThreshold"] == 0.3
    assert config["consecutiveFailureLimit"] == 3
    assert config["explorationBudget"] == 5
    assert config["transferBudget"] == 10
    assert config["dryRunCredit"] is False
    assert config["useTriggerQueue"] is True
    # Absent optionals are omitted, not defaulted to null.
    assert "graph" not in config
    assert "exploreTopK" not in config
    assert "purpose" not in config
    assert "generalizationBias" not in config


def test_config_mapping_overrides_and_keeps_optionals() -> None:
    config = _map_to_config(
        CreativityLoopInput.model_validate(
            {"graph": "research", "maxCycles": 5, "exploreTopK": 3, "purpose": "find analogies"}
        )
    )
    assert config["graph"] == "research"
    assert config["maxCycles"] == 5
    assert config["exploreTopK"] == 3
    assert config["purpose"] == "find analogies"


def test_registered_summary_no_longer_marks_the_command_unavailable() -> None:
    """The registry summary (and the COMMANDS.md catalog generated from it)
    must stop telling callers the command can never succeed."""
    from theloom.cli.registry import COMMANDS

    descriptor = next(c for c in COMMANDS if c.name == "creativity-loop")
    assert "unavailable" not in descriptor.summary.lower()
