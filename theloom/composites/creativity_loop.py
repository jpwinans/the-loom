"""Creativity Loop composite.

The documented six-step cycle — explore → retrieve → transfer → score →
accept/reject → learn — run autonomously for up to ``maxCycles`` iterations
with cross-cycle bookkeeping. Every step composes a primitive that already
exists; nothing here re-implements an algorithm:

1. **explore** — ``explore-frontier`` ranks the graph's regions by exploration
   priority. The top ``exploreTopK`` regions are the cycle's *frontier*, and
   that frontier is load-bearing: a proposal whose transferred relations land
   entirely outside it is rejected off-frontier (step 5). The analogy trigger
   queue's status is read and reported alongside (see the boundary below).
2. **retrieve** / 3. **transfer** / 4. **score** — one ``far-analogy-retrieval``
   call per cycle supplies all three (fingerprint → match → slip → transfer →
   score), bounded by ``explorationBudget`` candidates and ``transferBudget``
   proposals at ``slippageTemperature``.
3. **accept/reject** — a proposal is accepted when its effective score clears
   ``acceptanceThreshold``. The effective score blends the proposal's own
   confidence with its *generality* (its share of the cycle's widest relation
   fan-out) in the ratio ``generalizationBias``; at the default bias of 0 the
   effective score is the confidence unchanged.
4. **learn** — every proposal seen (accepted or rejected) is remembered by
   name, so a later cycle re-deriving it counts it as a duplicate rather than
   a fresh discovery, and every component pair the cycle visited is credited
   in the ``componentPairArchive``.

**Termination is real, not a fixed cycle count.** The loop stops when
``maxEmptyCycles`` consecutive cycles accept nothing new
(``consecutive-empty-cycles``), when ``detectPlateau`` is on and a cycle
surfaces no component pair the archive has not already seen
(``plateau-no-new-pairs``), or at ``maxCycles``. Because retrieval is
deterministic and the loop never mutates the graph, a second cycle over an
unchanged graph re-derives the first cycle's proposals — which is exactly what
the duplicate bookkeeping is for, and why an unproductive loop halts early
instead of burning its whole budget.

**Boundaries** (also reported in the result's ``boundaries``):

- *No LLM is involved.* Proposals come from CWSG analogy transfer and concept
  slippage, which are deterministic.
- *The trigger queue is report-only.* ``useTriggerQueue`` is fixed true by the
  config mapping, and each cycle reads ``trigger-status``, but far-analogy
  retrieval accepts no candidate-pair filter, so pending trigger candidates
  cannot steer which pairs are retrieved. The queue is reported, never
  drained — this command is read-only.
- *Exploration credit is always recorded.* ``dryRunCredit`` is fixed false by
  the config mapping and the input schema exposes no switch for it, so every
  cycle's visits land in the archive.
"""

from __future__ import annotations

import time
from functools import partial
from typing import Any

from pydantic import Field

from theloom.composites.explore_frontier import ExploreFrontierInput, explore_frontier
from theloom.composites.far_analogy_retrieval import (
    FarAnalogyRetrievalInput,
    far_analogy_retrieval,
)
from theloom.composites.framework import SectionResult, run_composite, time_section
from theloom.operations.common import CommandInput
from theloom.operations.reification import TriggerStatusInput, trigger_status
from theloom.store.multigraph import MultiGraph

Doc = dict[str, Any]

DEFAULT_MAX_CYCLES = 10
DEFAULT_THRESHOLD = 0.3
DEFAULT_FAILURE_LIMIT = 3
DEFAULT_EXPLORATION_BUDGET = 5
DEFAULT_TRANSFER_BUDGET = 10
DEFAULT_SLIPPAGE_TEMPERATURE = 0.5
MIN_STRUCTURAL_SIMILARITY = 0.3

STOP_MAX_CYCLES = "max-cycles"
STOP_EMPTY = "consecutive-empty-cycles"
STOP_PLATEAU = "plateau-no-new-pairs"

BOUNDARIES = [
    "No LLM is used: proposals come from CWSG analogy transfer and concept slippage, "
    "which are deterministic.",
    "The analogy trigger queue is report-only: each cycle reads trigger-status, but "
    "far-analogy retrieval takes no candidate-pair filter, so pending triggers cannot "
    "steer retrieval and the queue is never drained.",
    "dryRunCredit is fixed false (the input schema exposes no switch), so every cycle's "
    "component-pair visits are recorded in the archive.",
]


class CreativityLoopInput(CommandInput):
    graph: str | None = Field(default=None, max_length=200)
    max_cycles: int | None = Field(default=None, ge=1, le=100, alias="maxCycles")
    max_empty_cycles: int | None = Field(default=None, ge=1, le=50, alias="maxEmptyCycles")
    acceptance_threshold: float | None = Field(
        default=None, ge=0, le=1, alias="acceptanceThreshold"
    )
    slippage_temperature: float | None = Field(
        default=None, ge=0, le=1, alias="slippageTemperature"
    )
    retrieve_max_candidates: int | None = Field(
        default=None, ge=1, le=50, alias="retrieveMaxCandidates"
    )
    max_proposals_per_cycle: int | None = Field(
        default=None, ge=1, le=100, alias="maxProposalsPerCycle"
    )
    explore_top_k: int | None = Field(default=None, ge=1, le=20, alias="exploreTopK")
    detect_plateau: bool | None = Field(default=None, alias="detectPlateau")
    purpose: str | None = Field(default=None, max_length=10000)
    generalization_bias: float | None = Field(default=None, ge=0, le=1, alias="generalizationBias")


def _map_to_config(params: CreativityLoopInput) -> dict[str, Any]:
    """Apply defaults and omit absent optionals
    (graph/exploreTopK/purpose/generalizationBias)."""
    config: dict[str, Any] = {
        "maxCycles": params.max_cycles if params.max_cycles is not None else DEFAULT_MAX_CYCLES,
        "interestingnessThreshold": params.acceptance_threshold
        if params.acceptance_threshold is not None
        else DEFAULT_THRESHOLD,
        "consecutiveFailureLimit": params.max_empty_cycles
        if params.max_empty_cycles is not None
        else DEFAULT_FAILURE_LIMIT,
        "explorationBudget": params.retrieve_max_candidates
        if params.retrieve_max_candidates is not None
        else DEFAULT_EXPLORATION_BUDGET,
        "transferBudget": params.max_proposals_per_cycle
        if params.max_proposals_per_cycle is not None
        else DEFAULT_TRANSFER_BUDGET,
        "slippageTemperature": params.slippage_temperature
        if params.slippage_temperature is not None
        else DEFAULT_SLIPPAGE_TEMPERATURE,
        "minStructuralSimilarity": MIN_STRUCTURAL_SIMILARITY,
        "dryRunCredit": False,
        "detectPlateau": params.detect_plateau if params.detect_plateau is not None else False,
        "useTriggerQueue": True,
    }
    if params.graph is not None:
        config["graph"] = params.graph
    if params.explore_top_k is not None:
        config["exploreTopK"] = params.explore_top_k
    if params.purpose is not None:
        config["purpose"] = params.purpose
    if params.generalization_bias is not None:
        config["generalizationBias"] = params.generalization_bias
    return config


def _section_data(envelope: Doc, name: str) -> tuple[Doc | None, str | None]:
    """A named section's ``(data, error)`` out of a composite envelope."""
    section = envelope.get("result", {}).get(name)
    if section is None:
        return None, f"section '{name}' missing from the envelope"
    return section.get("data"), section.get("error")


def _explore(config: dict[str, Any], multi: MultiGraph) -> Doc:
    """Step 1: rank the frontier and read the trigger queue's status."""
    payload: dict[str, Any] = {}
    if "graph" in config:
        payload["graph"] = config["graph"]
    if "exploreTopK" in config:
        payload["topK"] = config["exploreTopK"]
    if "purpose" in config:
        payload["purpose"] = config["purpose"]
    envelope = explore_frontier(ExploreFrontierInput.model_validate(payload), multi)
    regions, error = _section_data(envelope, "regions")
    ranked: list[Doc] = list(regions) if isinstance(regions, list) else []
    frontier_ids: set[str] = {
        entity_id for region in ranked for entity_id in region.get("entityIds", [])
    }
    queue = trigger_status(
        TriggerStatusInput.model_validate({"graph": config["graph"]} if "graph" in config else {}),
        multi,
    )
    return {
        "regionsRanked": len(ranked),
        "frontierEntityCount": len(frontier_ids),
        "topRegion": (
            {
                "entityNames": ranked[0]["entityNames"],
                "compositeScore": ranked[0]["compositeScore"],
            }
            if ranked
            else None
        ),
        "triggerQueue": {
            "pendingCount": queue["pendingCount"],
            "processedCount": queue["processedCount"],
            "consumed": False,
        },
        "error": error,
        "frontierIds": frontier_ids,
    }


def _retrieve(config: dict[str, Any], multi: MultiGraph) -> Doc:
    """Steps 2-4: one far-analogy retrieval supplies retrieve/transfer/score."""
    payload: dict[str, Any] = {
        "maxCandidates": config["explorationBudget"],
        "minStructuralSimilarity": config["minStructuralSimilarity"],
        "slippageTemperature": config["slippageTemperature"],
        "maxProposals": config["transferBudget"],
    }
    if "graph" in config:
        payload["graph"] = config["graph"]
    if "purpose" in config:
        payload["purpose"] = config["purpose"]
    return far_analogy_retrieval(FarAnalogyRetrievalInput.model_validate(payload), multi)


def _generality(proposal: Doc, widest: int) -> float:
    """A proposal's relation fan-out relative to the cycle's widest — the
    "generalizes further" signal ``generalizationBias`` weights."""
    if widest <= 0:
        return 0.0
    return min(1.0, len(proposal.get("relations") or []) / widest)


def _judge(
    proposals: list[Doc],
    frontier_ids: set[str],
    seen_names: set[str],
    threshold: float,
    bias: float,
    cycle: int,
) -> tuple[list[Doc], Doc]:
    """Step 5: accept/reject each proposal; returns (accepted, counts)."""
    widest = max((len(p.get("relations") or []) for p in proposals), default=0)
    accepted: list[Doc] = []
    duplicates = 0
    off_frontier = 0
    rejected = 0
    for proposal in proposals:
        name_key = str(proposal["entity"]["name"]).strip().lower()
        if name_key in seen_names:
            duplicates += 1
            continue
        seen_names.add(name_key)

        relations = proposal.get("relations") or []
        target_ids = {str(r.get("targetId")) for r in relations}
        # A proposal with no anchor cannot be off-frontier; one that anchors
        # only outside the ranked regions is out of this cycle's scope.
        if frontier_ids and target_ids and not (target_ids & frontier_ids):
            off_frontier += 1
            continue

        effective = (1 - bias) * float(proposal["confidence"]) + bias * _generality(
            proposal, widest
        )
        if effective >= threshold:
            accepted.append({**proposal, "cycle": cycle, "effectiveScore": effective})
        else:
            rejected += 1
    return accepted, {
        "accepted": len(accepted),
        "rejected": rejected,
        "duplicates": duplicates,
        "offFrontier": off_frontier,
        "threshold": threshold,
    }


def _credit(archive: dict[str, Doc], feedback: list[Doc], cycle: int) -> int:
    """Step 6: record this cycle's component-pair visits; returns new pairs."""
    new_pairs = 0
    for pair in feedback:
        key = f"{pair['sourceComponentId']}|{pair['targetComponentId']}"
        entry = archive.get(key)
        if entry is None:
            new_pairs += 1
            archive[key] = {
                "pairKey": key,
                "sourceComponentId": pair["sourceComponentId"],
                "targetComponentId": pair["targetComponentId"],
                "visits": 1,
                "proposalsCredited": pair.get("proposalCount", 0),
                "firstCycle": cycle,
                "lastCycle": cycle,
            }
            continue
        entry["visits"] += 1
        entry["proposalsCredited"] += pair.get("proposalCount", 0)
        entry["lastCycle"] = cycle
    return new_pairs


def creativity_loop(params: CreativityLoopInput, multi: MultiGraph) -> dict[str, Any]:
    start = time.perf_counter()
    config = _map_to_config(params)
    max_cycles = int(config["maxCycles"])
    threshold = float(config["interestingnessThreshold"])
    failure_limit = int(config["consecutiveFailureLimit"])
    detect_plateau = bool(config["detectPlateau"])
    bias = float(config.get("generalizationBias", 0.0))

    archive: dict[str, Doc] = {}
    seen_names: set[str] = set()
    accepted_all: list[Doc] = []
    cycles: list[Doc] = []
    consecutive_empty = 0
    plateau_detected = False
    stop_reason = STOP_MAX_CYCLES
    loop_start = time.perf_counter()

    for cycle in range(1, max_cycles + 1):
        cycle_start = time.perf_counter()

        explore_section = time_section(partial(_explore, config, multi))
        explore_data: Doc = explore_section["data"] or {
            "regionsRanked": 0,
            "frontierEntityCount": 0,
            "topRegion": None,
            "triggerQueue": {"pendingCount": 0, "processedCount": 0, "consumed": False},
            "error": explore_section["error"],
            "frontierIds": set(),
        }
        frontier_ids: set[str] = set(explore_data.pop("frontierIds", set()))
        if explore_section["error"] is not None:
            explore_data["error"] = explore_section["error"]

        retrieve_section = time_section(partial(_retrieve, config, multi))
        retrieval: Doc = retrieve_section["data"] or {}
        envelope: Doc = retrieval.get("composite", {})
        match_data, match_error = _section_data(envelope, "match")
        transfer_data, _ = _section_data(envelope, "transfer")
        score_data, _ = _section_data(envelope, "score")
        proposals: list[Doc] = list(retrieval.get("proposals") or [])
        feedback: list[Doc] = list(
            (retrieval.get("explorationFeedback") or {}).get("candidateFeedback") or []
        )

        accepted, counts = _judge(proposals, frontier_ids, seen_names, threshold, bias, cycle)
        accepted_all.extend(accepted)
        new_pairs = _credit(archive, feedback, cycle)

        if accepted:
            consecutive_empty = 0
        else:
            consecutive_empty += 1

        cycles.append(
            {
                "cycle": cycle,
                "explore": explore_data,
                "retrieve": {
                    "candidatePairs": (match_data or {}).get("candidateCount", 0),
                    "newPairs": new_pairs,
                    "error": retrieve_section["error"] or match_error,
                },
                "transfer": {
                    "proposalsGenerated": (transfer_data or {}).get("proposalsGenerated", 0)
                },
                "score": {
                    "proposalsScored": (score_data or {}).get("proposalsScored", 0),
                    "topScore": (
                        max(float(p["confidence"]) for p in proposals) if proposals else None
                    ),
                },
                "accept": counts,
                "learn": {
                    "pairsCredited": len(feedback),
                    "archiveSize": len(archive),
                    "namesSeen": len(seen_names),
                    "consecutiveEmptyCycles": consecutive_empty,
                },
                "durationMs": round((time.perf_counter() - cycle_start) * 1000),
            }
        )

        if consecutive_empty >= failure_limit:
            stop_reason = STOP_EMPTY
            break
        if detect_plateau and cycle > 1 and new_pairs == 0:
            plateau_detected = True
            stop_reason = STOP_PLATEAU
            break

    loop_ms = round((time.perf_counter() - loop_start) * 1000)
    archive_list = sorted(archive.values(), key=lambda entry: str(entry["pairKey"]))

    cycles_section: SectionResult = {
        "data": {
            "cyclesRun": len(cycles),
            "maxCycles": max_cycles,
            "stopReason": stop_reason,
            "cycles": cycles,
        },
        "durationMs": loop_ms,
        "error": None,
    }
    accept_section: SectionResult = {
        "data": {
            "acceptedCount": len(accepted_all),
            "rejectedCount": sum(int(c["accept"]["rejected"]) for c in cycles),
            "duplicateCount": sum(int(c["accept"]["duplicates"]) for c in cycles),
            "offFrontierCount": sum(int(c["accept"]["offFrontier"]) for c in cycles),
            "threshold": threshold,
            "generalizationBias": bias,
        },
        "durationMs": 0,
        "error": None,
    }
    learn_section: SectionResult = {
        "data": {
            "archiveSize": len(archive_list),
            "namesSeen": len(seen_names),
            "plateauDetected": plateau_detected,
            "componentPairArchive": archive_list,
        },
        "durationMs": 0,
        "error": None,
    }

    composite = run_composite(
        [("cycles", cycles_section), ("accept", accept_section), ("learn", learn_section)],
        start=start,
    )
    total_ms = composite["metadata"]["totalDurationMs"]
    return {
        "composite": composite,
        "cycles": cycles,
        "acceptedProposals": accepted_all,
        "componentPairArchive": archive_list,
        "stopReason": stop_reason,
        "summary": _build_summary(cycles, accepted_all, stop_reason, total_ms),
        "boundaries": list(BOUNDARIES),
    }


def _build_summary(
    cycles: list[Doc], accepted: list[Doc], stop_reason: str, duration_ms: int
) -> str:
    lines = [
        f"Creativity Loop Complete ({duration_ms}ms)",
        "",
        f"Cycles run: {len(cycles)} (stopped: {stop_reason})",
        f"Accepted proposals: {len(accepted)}",
        "",
    ]
    for cycle in cycles:
        accept = cycle["accept"]
        lines.append(
            f"  Cycle {cycle['cycle']}: {cycle['retrieve']['candidatePairs']} pair(s), "
            f"{cycle['transfer']['proposalsGenerated']} proposal(s) -> "
            f"{accept['accepted']} accepted, {accept['rejected']} rejected, "
            f"{accept['duplicates']} duplicate(s), {accept['offFrontier']} off-frontier"
        )
    lines.append("")
    if not accepted:
        lines.append("No proposals accepted.")
        return "\n".join(lines)
    lines.append(f"Top {min(len(accepted), 5)} accepted:")
    for proposal in sorted(accepted, key=lambda p: -float(p["effectiveScore"]))[:5]:
        lines.append(
            f"  {proposal['entity']['name']} "
            f"(score: {proposal['effectiveScore']:.3f}, cycle: {proposal['cycle']})"
        )
    if len(accepted) > 5:
        lines.append(f"  ... and {len(accepted) - 5} more")
    return "\n".join(lines)
