"""Creativity Loop composite.

A six-step cycle (explore → retrieve → transfer → score → accept/reject →
learn) that discovers novel entities via far-analogy transfer.

The CLI handler wires empty dependency stubs — so from the CLI the
real explore/retrieve/transfer machinery is never exercised: every cycle produces
zero proposals, so the loop runs ``min(maxCycles, maxEmptyCycles)`` empty cycles
and stops on ``consecutive_failure`` (or ``max_cycles`` when maxCycles is the
smaller bound). Fully deterministic; the output is the bespoke
``{summary, cycles, state}`` object (not the standard composite envelope).
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import Field

from theloom.composites.framework import time_section
from theloom.operations.common import CommandInput

DEFAULT_MAX_CYCLES = 10
DEFAULT_THRESHOLD = 0.3
DEFAULT_FAILURE_LIMIT = 3
DEFAULT_EXPLORATION_BUDGET = 5
DEFAULT_TRANSFER_BUDGET = 10
DEFAULT_SLIPPAGE_TEMPERATURE = 0.5
MIN_STRUCTURAL_SIMILARITY = 0.3


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


def creativity_loop(params: CreativityLoopInput, _multi: Any) -> dict[str, Any]:
    start = time.perf_counter()
    config = _map_to_config(params)
    max_cycles = config["maxCycles"]
    failure_limit = config["consecutiveFailureLimit"]
    detect_plateau = config["detectPlateau"]

    state: dict[str, Any] = {
        "config": config,
        "cycleCount": 0,
        "interestingnessHistory": [],
        "totals": {"proposed": 0, "accepted": 0, "rejected": 0},
        "componentPairArchive": [],
        "explorationState": None,
    }
    cycles: list[dict[str, Any]] = []
    stopping_reason = "max_cycles"
    consecutive_failures = 0
    previous_region_count = -1

    for cycle in range(max_cycles):
        cycle_start = time.perf_counter()

        # Steps 1-3: empty stubs.
        explore = time_section(
            lambda: {
                "regions": {"data": [], "durationMs": 0, "error": None},
                "mvtAdvice": {"data": [], "durationMs": 0, "error": None},
                "antiPatterns": {"data": [], "durationMs": 0, "error": None},
            }
        )
        region_count = 0
        if previous_region_count > 0 and region_count == 0:
            stopping_reason = "frontier_exhausted"
            break

        retrieve = time_section(lambda: {"candidateCount": 0, "candidates": [], "proposals": []})
        transfer = time_section(
            lambda: {"mappingsAttempted": 0, "proposalsGenerated": 0, "proposals": []}
        )
        # Steps 4-6: all operate on empty inputs.
        score = time_section(lambda: {"scored": [], "aboveThreshold": 0, "belowThreshold": 0})
        accept_reject = time_section(
            lambda: {"accepted": [], "rejected": [], "housekeepingAutoAccepted": 0}
        )
        accepted_count = 0
        archive_size_before = len(state["componentPairArchive"])
        learn = time_section(
            lambda: {"creditChanges": [], "explorationUpdates": 0, "archiveUpdates": 0}
        )
        cycle_interestingness = 0

        cycles.append(
            {
                "explore": explore,
                "retrieve": retrieve,
                "transfer": transfer,
                "score": score,
                "acceptReject": accept_reject,
                "learn": learn,
                "cycleInterestingness": cycle_interestingness,
                "durationMs": round((time.perf_counter() - cycle_start) * 1000),
            }
        )

        state["cycleCount"] = cycle + 1
        state["interestingnessHistory"].append(cycle_interestingness)

        if accepted_count == 0:
            consecutive_failures += 1
            if consecutive_failures >= failure_limit:
                stopping_reason = "consecutive_failure"
                break
        else:
            consecutive_failures = 0

        archive_size_after = len(state["componentPairArchive"])
        no_new_pairs = archive_size_after == archive_size_before and archive_size_before > 0
        if no_new_pairs and all(p["status"] != "unexplored" for p in state["componentPairArchive"]):
            stopping_reason = "all_pairs_explored"
            break

        if detect_plateau and len(state["interestingnessHistory"]) >= 3:
            history = state["interestingnessHistory"]
            if history[-1] < history[-2] < history[-3]:
                stopping_reason = "quality_plateau"
                break

        previous_region_count = region_count

    total_pairs = len(state["componentPairArchive"])
    explored = sum(1 for p in state["componentPairArchive"] if p["status"] != "unexplored")
    coverage = explored / total_pairs if total_pairs > 0 else 0

    summary = {
        "cycles": state["cycleCount"],
        "totals": dict(state["totals"]),
        "trajectory": list(state["interestingnessHistory"]),
        "stoppingReason": stopping_reason,
        "explorationCoverage": coverage,
        "durationMs": round((time.perf_counter() - start) * 1000),
    }
    return {"summary": summary, "cycles": cycles, "state": state}
