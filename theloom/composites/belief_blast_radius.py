"""``belief-blast-radius`` (desire 4): "what would change if I stop
believing this?" — a first-class, non-mutating read over the same
propagation trajectory ``propagate-credit`` computes, built as exactly the
composite desire 12's design section specifies: **fork, propagate-credit
inside the fork with the hypothetical delta, diff-worlds, abandon.**

There is deliberately no second propagation implementation here — this
composite calls ``theloom.operations.epistemic.propagate_credit`` directly
(the very function the ``propagate-credit`` command registers), which is
also how a caller can prove it: monkeypatching that one function is
observable from here (see ``tests/test_worlds.py``).

The world it forks is always torn down, even when propagation or diffing
raises — but torn down by *purge*, not ``abandon-world``: this composite
owns the fork's entire lifecycle within one call, so reap-and-keep-as-
history (what a caller-initiated ``abandon-world`` gives an ordinary fork)
would just grow ``list-worlds`` forever with entries nobody will ever look
up again — a real, observed leak of one ref plus its lifecycle events per
call. ``multi.purge_world`` erases the ref's record outright instead, and —
unlike ``abandon_world`` — commits no ref-lifecycle events of its own
(nothing left to replay for a ref that, from every other caller's
perspective, never existed).

That still leaves the fork's *own* segment: propagate-credit's writes land
in ``_world_<id>``'s own event stream, which purge deletes along with the
graph data — a real dangling pointer if returned. Those, and only those,
are isolated in a *nested* write-receipts scope
(``theloom.store.receipts.collecting``) that this composite's own response
never sees. ``fork-world``'s own ``ref_registered`` event is different: it
lives in the shared, never-deleted ``_refs`` stream, so it stays genuinely
replayable via ``what-changed`` even after the ref itself is purged — and
is reported normally, not swept into the same nested scope. Nothing this
composite computes ever reaches ``main``: ``applied`` is always ``false``,
honestly.
"""

from __future__ import annotations

from pydantic import Field

from theloom.operations import epistemic as epistemic_ops
from theloom.operations import worlds as worlds_ops
from theloom.operations.common import CommandInput, UuidStr
from theloom.operations.notices import Doc, with_notices
from theloom.store import receipts, worldctx
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
    # fork-world's own ref_registered event lives in the shared _refs
    # stream, which purge_world never touches -- it stays genuinely
    # replayable, so it is NOT isolated and reaches this response's own
    # eventIds normally.
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
        # Propagate-credit's writes land in the fork's OWN segment, which
        # purge_world deletes below -- a nested collecting() scope isolates
        # those event ids from the outer (this command's own) scope, the
        # same mechanism receipts.py documents for a future "composite-of-
        # composites", so a dangling pointer into a stream that no longer
        # exists is never reported as a receipt.
        with worldctx.active(world_id), receipts.collecting("belief-blast-radius:ephemeral-fork"):
            propagation = epistemic_ops.propagate_credit(propagate_input, multi)
        diff = worlds_ops.diff_worlds(worlds_ops.DiffWorldsInput(a=parent_label, b=world_id), multi)
    finally:
        multi.purge_world(world_id)
    return with_notices(
        {"worldId": world_id, "propagation": propagation, "diff": diff},
        applied=False,
    )
