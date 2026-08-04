"""Simulate-Change composite tests.

The before-snapshot must read the same graph the mutation clone reads — a
simulation on a non-default graph that snapshots "before" from the default
graph diffs two unrelated states.
"""

from __future__ import annotations

import pytest
from falkordb import FalkorDB
from redis import Redis

from theloom.composites.simulate_change import SimulateChangeInput, simulate_change
from theloom.model import EntityCreate, RelationCreate
from theloom.store.multigraph import MultiGraph


@pytest.fixture()
def multi(db: FalkorDB, redis_client: Redis, namespace: str) -> MultiGraph:
    return MultiGraph(db, redis_client, default_graph="default", key_prefix=namespace)


def _concept(store: object, name: str) -> object:
    return store.create_entity(  # type: ignore[attr-defined]
        EntityCreate.model_validate({"name": name, "entityType": "concept", "observations": []})
    )


def test_before_and_after_snapshot_the_same_non_default_graph(multi: MultiGraph) -> None:
    # The default graph carries its own, unrelated entities and a relation —
    # a connected pair that must never leak into a simulation on "other".
    default_store = multi.get_store()
    x = _concept(default_store, "x")
    y = _concept(default_store, "y")
    default_store.create_relation(
        RelationCreate.model_validate(
            {
                "from": x.id,
                "to": y.id,
                "relationType": "related_to",
                "polarity": None,
                "strength": "moderate",
                "evidence": None,
            }
        )
    )

    # The simulated graph is disjoint: two isolated entities, no relation yet.
    multi.create_graph("other")
    other_store = multi.get_store("other")
    a = _concept(other_store, "a")
    b = _concept(other_store, "b")

    result = simulate_change(
        SimulateChangeInput.model_validate(
            {
                "graph": "other",
                "mutations": [
                    {
                        "type": "createRelation",
                        "payload": {
                            "from": a.id,
                            "to": b.id,
                            "relationType": "related_to",
                            "polarity": None,
                            "strength": "moderate",
                            "evidence": None,
                        },
                    }
                ],
            }
        ),
        multi,
    )

    delta_ids = {d["entityId"] for d in result["result"]["centralityDelta"]["data"]}
    # Only entities from "other" may appear in the diff.
    assert delta_ids == {a.id, b.id}
    assert x.id not in delta_ids
    assert y.id not in delta_ids

    # "other" starts as two isolated nodes (2 components) and ends connected
    # by the mutated relation (1 component) — the default graph's own
    # single-component x/y pair must not leak into these counts.
    assert result["result"]["componentChanges"]["data"] == {"before": 2, "after": 1}
    assert result["result"]["blastRadius"]["data"]["total"] == 2
