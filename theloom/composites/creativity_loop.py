"""Creativity Loop composite.

A six-step cycle (explore → retrieve → transfer → score → accept/reject →
learn) meant to discover novel entities via far-analogy transfer, running
autonomously for multiple cycles with cross-cycle exploration-credit
bookkeeping (``componentPairArchive``, exploration-state UCB bonuses, plateau
detection).

That stateful, multi-cycle orchestration is not built. Individual pieces
exist as their own composites — ``explore-frontier`` ranks regions,
``far-analogy-retrieval`` runs fingerprint/match/slip/transfer/score once —
but nothing threads them through ``maxCycles`` iterations with the credit
archive and accept/reject bookkeeping this composite's input schema promises
(``explorationBudget``, ``transferBudget``, ``dryRunCredit``,
``useTriggerQueue``, ``generalizationBias``). Rather than silently faking
that with empty stub cycles (data no caller can trust as a real crawl) or
raising a bare ``NotImplementedError``, this command raises a typed
``OperationError`` that names exactly what's missing.

``_map_to_config`` — the config mapping from this composite's input schema —
is kept and still tested; it's the part of the documented contract that
already exists.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from theloom.errors import OperationError
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


# The multi-cycle explore/retrieve/transfer/score/accept-reject/learn
# orchestration has no implementation to run yet (see module docstring).
_LOOP_UNAVAILABLE_MESSAGE = (
    "creativity-loop's multi-cycle orchestration (explore/retrieve/transfer/"
    "score/accept-reject/learn with cross-cycle exploration credit) is not "
    "implemented — only its input-config mapping exists. This command is "
    "currently unavailable."
)


def creativity_loop(params: CreativityLoopInput, _multi: Any) -> dict[str, Any]:
    # Validate/map the input first (a bad config should fail as itself, not
    # be masked by the unavailability error below).
    _map_to_config(params)
    raise OperationError(_LOOP_UNAVAILABLE_MESSAGE)
