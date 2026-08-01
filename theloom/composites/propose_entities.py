"""Propose-Entities composite.

A *thin wrapper*, not a composite envelope. Unlike the other composites it
returns the raw operation result (``proposals`` / ``strategyCounts`` /
``filteredCount`` / ``violations`` / ``durationMs``) directly rather than a
:func:`build_composite_result` envelope.

All the work lives in the already-built foundation
:func:`theloom.semantic.entity_proposer.propose_entities`; this module only
resolves the store, maps the CLI params to the op's option names, and forwards.
Template mode passes no ``simulateChange`` callable, so structural simulation is
a no-op even when ``simulate`` is set.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from theloom.operations.common import CommandInput
from theloom.semantic.entity_proposer import propose_entities as propose_entities_op
from theloom.store.multigraph import MultiGraph


class ProposeEntitiesInput(CommandInput):
    limit: int | None = 10
    simulate: bool | None = False
    # None defers to the op's default of both strategies (pattern_completion,
    # llm_reasoning); the field is optional with no default.
    strategies: list[Literal["pattern_completion", "llm_reasoning"]] | None = None
    graph: str | None = None
    min_pattern_occurrences: int | None = Field(default=2, alias="minPatternOccurrences")
    max_patterns: int | None = Field(default=20, alias="maxPatterns")


def propose_entities(params: ProposeEntitiesInput, multi: MultiGraph) -> dict[str, Any]:
    """Forward CLI params to the entity-proposer op and return its raw result."""
    store = multi.get_store(params.graph)
    options: dict[str, Any] = {
        "limit": params.limit,
        "simulate": params.simulate,
        "strategies": params.strategies,
        "minPatternOccurrences": params.min_pattern_occurrences,
        "maxPatterns": params.max_patterns,
        "graph": params.graph,
    }
    return propose_entities_op(store, options)
