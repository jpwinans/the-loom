"""Entity Deep Dive composite tests.

Pins the compact-by-default relations/neighbors shape (one line per item:
name, entityType, relationType, direction, anchor) and the `full: true`
escape hatch back to full relation/entity envelopes.
"""

from __future__ import annotations

import pytest
from falkordb import FalkorDB
from redis import Redis

from theloom.composites.entity_deep_dive import EntityDeepDiveInput, entity_deep_dive
from theloom.operations.entity import CreateEntityInput, create_entity
from theloom.operations.relations import CreateRelationInput, create_relation
from theloom.store.multigraph import MultiGraph


@pytest.fixture()
def multi(db: FalkorDB, redis_client: Redis, namespace: str) -> MultiGraph:
    return MultiGraph(db, redis_client, default_graph="default", key_prefix=namespace)


def ent(multi: MultiGraph, name: str, observations: list[str] | None = None) -> str:
    result = create_entity(
        CreateEntityInput.model_validate(
            {
                "name": name,
                "entityType": "concept",
                "observations": observations if observations is not None else [name],
            }
        ),
        multi,
    )
    return str(result["id"])


def rel(multi: MultiGraph, from_id: str, to_id: str, relation_type: str = "supports") -> None:
    create_relation(
        CreateRelationInput.model_validate(
            {
                "from": from_id,
                "to": to_id,
                "relationType": relation_type,
                "polarity": None,
                "strength": "moderate",
                "evidence": None,
            }
        ),
        multi,
    )


def test_deep_dive_default_relations_and_neighbors_are_compact_lines(multi: MultiGraph) -> None:
    center = ent(multi, "Center", observations=["center obs"])
    other = ent(multi, "Other", observations=["other obs one", "other obs two"])
    rel(multi, center, other, "supports")

    result = entity_deep_dive(EntityDeepDiveInput.model_validate({"entityId": center}), multi)
    relations = result["result"]["relations"]["data"]
    assert relations["outgoing"] == [
        {
            "name": "Other",
            "entityType": "concept",
            "relationType": "supports",
            "direction": "out",
            "anchor": "other obs one",
        }
    ]
    assert relations["incoming"] == []

    neighbors = result["result"]["neighbors"]["data"]
    assert neighbors == [
        {
            "name": "Other",
            "entityType": "concept",
            "relationType": "supports",
            "direction": "out",
            "anchor": "other obs one",
        }
    ]


def test_deep_dive_full_true_restores_envelopes(multi: MultiGraph) -> None:
    center = ent(multi, "Center")
    other = ent(multi, "Other")
    rel(multi, center, other, "supports")

    result = entity_deep_dive(
        EntityDeepDiveInput.model_validate({"entityId": center, "full": True}), multi
    )
    relations = result["result"]["relations"]["data"]
    assert relations["outgoing"][0]["from"] == center
    assert relations["outgoing"][0]["to"] == other
    assert "anchor" not in relations["outgoing"][0]
    assert "direction" not in relations["outgoing"][0]

    neighbors = result["result"]["neighbors"]["data"]
    assert neighbors == [{"id": other, "name": "Other", "entityType": "concept"}]


def test_deep_dive_default_fetches_neighbors_once(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The relations and neighbors sections share one compact neighbor fetch."""
    import theloom.composites.entity_deep_dive as module

    center = ent(multi, "Center")
    other = ent(multi, "Other")
    rel(multi, center, other, "supports")

    calls: list[object] = []
    original = module.get_neighbors

    def counting(params: object, m: MultiGraph) -> object:
        calls.append(params)
        return original(params, m)  # type: ignore[arg-type]

    monkeypatch.setattr(module, "get_neighbors", counting)
    result = entity_deep_dive(EntityDeepDiveInput.model_validate({"entityId": center}), multi)

    assert len(calls) == 1
    assert result["result"]["neighbors"]["data"] == [
        {
            "name": "Other",
            "entityType": "concept",
            "relationType": "supports",
            "direction": "out",
            "anchor": "Other",
        }
    ]


def test_anchor_is_none_without_observations() -> None:
    from theloom.composites.entity_deep_dive import _anchor

    assert _anchor([]) is None
    assert _anchor(None) is None


def test_anchor_truncates_long_observations() -> None:
    from theloom.composites.entity_deep_dive import ANCHOR_MAX_CHARS, _anchor

    long_text = "x" * (ANCHOR_MAX_CHARS + 50)
    anchor = _anchor([long_text])
    assert anchor is not None
    assert len(anchor) <= ANCHOR_MAX_CHARS + 1  # +1 for the ellipsis char
    assert anchor.endswith("…")


def test_deep_dive_incoming_relation_has_direction_in(multi: MultiGraph) -> None:
    center = ent(multi, "Center")
    other = ent(multi, "Other")
    rel(multi, other, center, "causes")

    result = entity_deep_dive(EntityDeepDiveInput.model_validate({"entityId": center}), multi)
    relations = result["result"]["relations"]["data"]
    assert relations["incoming"] == [
        {
            "name": "Other",
            "entityType": "concept",
            "relationType": "causes",
            "direction": "in",
            "anchor": "Other",
        }
    ]
