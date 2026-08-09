"""``belief-blast-radius`` (desire 4): "what would change if I stop
believing this?" — a first-class, non-mutating read over the same
propagation trajectory ``propagate-credit`` computes, built as exactly the
composite desire 12's design section specifies: **fork, propagate-credit
inside the fork with the hypothetical delta, diff-worlds, abandon.**

There is deliberately no second propagation implementation here — this
composite calls ``theloom.operations.epistemic.propagate_credit`` directly
(the very function the ``propagate-credit`` command registers), which is
also how a caller can prove it: monkeypatching that one function is
observable from here (see ``tests/test_belief_blast_radius.py``).

The world it forks is always torn down (``abandon-world``, even when
propagation or diffing raises) — the composite never leaves a stray world
behind, and nothing it computes ever reaches ``main``: ``applied`` is always
``false``, honestly, because nothing outside the discarded fork changed.
"""

from __future__ import annotations

from pydantic import Field

from theloom.operations import epistemic as epistemic_ops
from theloom.operations import worlds as worlds_ops
from theloom.operations.common import CommandInput, UuidStr
from theloom.operations.notices import Doc, with_notices
from theloom.store import worldctx
from theloom.store.multigraph import MultiGraph


class BeliefBlastRadiusInput(CommandInput):
    entity_ids: list[UuidStr] = Field(
        alias="entityIds",
        description="The evidence/claim entities to hypothetically revise -- same addressing as "
        "propagate-credit.",
    )
    delta: float = Field(description="The hypothetical confidence delta to propagate.")
    graph: str | None = None
    damping_factor: float | None = Field(default=None, alias="dampingFactor")
    max_depth: int | None = Field(default=None, ge=1, alias="maxDepth")
    min_delta: float | None = Field(default=None, ge=0, alias="minDelta")
    relation_types: list[str] | None = Field(default=None, alias="relationTypes")
    propagation_mode: str | None = Field(default=None, alias="propagationMode")


def belief_blast_radius(params: BeliefBlastRadiusInput, multi: MultiGraph) -> Doc:
    parent_label = params.world or "main"
    fork = multi.fork_world(
        name=None,
        graph=params.graph,
        from_world=params.world,
        as_of=None,
        ttl_seconds=None,
    )
    world_id = fork["worldId"]
    try:
        propagate_input = epistemic_ops.PropagateCreditInput.model_validate(
            {
                "entityIds": params.entity_ids,
                "delta": params.delta,
                "dampingFactor": params.damping_factor,
                "maxDepth": params.max_depth,
                "minDelta": params.min_delta,
                "relationTypes": params.relation_types,
                "propagationMode": params.propagation_mode,
                "graph": params.graph,
                "dryRun": False,
            }
        )
        with worldctx.active(world_id):
            propagation = epistemic_ops.propagate_credit(propagate_input, multi)
        diff = worlds_ops.diff_worlds(worlds_ops.DiffWorldsInput(a=parent_label, b=world_id), multi)
    finally:
        multi.abandon_world(world_id)
    return with_notices(
        {"worldId": world_id, "propagation": propagation, "diff": diff},
        applied=False,
    )
