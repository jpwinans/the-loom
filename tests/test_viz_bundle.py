"""Bundle assembler tests — the one entry point both commands share."""

from __future__ import annotations

import pytest

from theloom.errors import LoomError
from theloom.model import EntityCreate, RelationCreate
from theloom.store.multigraph import MultiGraph
from theloom.viz.bundle import ExportBundleInput, assemble_bundle
from theloom.viz.schema import SCHEMA_VERSION, TapestryBundle


@pytest.fixture()
def seeded(multi: MultiGraph) -> None:
    store = multi.get_store()
    a = store.create_entity(
        EntityCreate.model_validate({"name": "a", "entityType": "concept", "observations": []})
    )
    b = store.create_entity(
        EntityCreate.model_validate({"name": "b", "entityType": "claim", "observations": []})
    )
    store.create_relation(
        RelationCreate.model_validate({"from": a.id, "to": b.id, "relationType": "supports"})
    )


def test_default_bundle(multi: MultiGraph, seeded: None) -> None:
    doc = assemble_bundle(ExportBundleInput(), multi)
    TapestryBundle.model_validate(doc)  # schema-valid
    assert doc["schemaVersion"] == SCHEMA_VERSION
    assert doc["meta"]["graph"] == "default"
    assert doc["meta"]["scope"] == "full"
    assert doc["meta"]["entityCount"] == 2
    assert doc["meta"]["relationCount"] == 1
    assert set(doc["meta"]["sections"]) == {"analytics", "temporal"}  # no vectors seeded
    assert "semantic" not in doc


def test_includes_are_flags(multi: MultiGraph, seeded: None) -> None:
    doc = assemble_bundle(
        ExportBundleInput.model_validate(
            {"include": {"analytics": False, "temporal": False, "semantic": False}}
        ),
        multi,
    )
    assert doc["meta"]["sections"] == []
    assert "analytics" not in doc and "temporal" not in doc


def test_unknown_graph_is_not_found(multi: MultiGraph) -> None:
    with pytest.raises(LoomError) as err:
        assemble_bundle(ExportBundleInput.model_validate({"graph": "nope"}), multi)
    assert err.value.code == "NOT_FOUND"


def test_max_entities_truncates_and_records_metadata(multi: MultiGraph) -> None:
    store = multi.get_store()
    for name in ("a", "b", "c"):
        store.create_entity(
            EntityCreate.model_validate({"name": name, "entityType": "concept", "observations": []})
        )
    bundle = assemble_bundle(ExportBundleInput.model_validate({"maxEntities": 2}), multi)
    assert bundle["meta"]["entityCount"] == 2
    assert bundle["meta"]["truncated"]["total"] == 3
    assert bundle["meta"]["truncated"]["kept"] == 2
    assert bundle["meta"]["truncated"]["by"] == "degree"
    assert len(bundle["entities"]) == 2


def test_no_truncated_key_when_under_cap(multi: MultiGraph) -> None:
    multi.get_store().create_entity(
        EntityCreate.model_validate({"name": "a", "entityType": "concept", "observations": []})
    )
    bundle = assemble_bundle(ExportBundleInput.model_validate({}), multi)
    assert "truncated" not in bundle["meta"]  # exclude_none drops it


def test_max_entities_keeps_higher_degree_core_and_induced_relations(multi: MultiGraph) -> None:
    store = multi.get_store()
    # A hub (2 relations) plus two leaves (1 each) plus one fully isolated node
    # (0). A cap of 2 must keep the hub — its degree is unambiguously highest —
    # and drop the isolated node before either leaf.
    hub = store.create_entity(
        EntityCreate.model_validate({"name": "hub", "entityType": "concept", "observations": []})
    )
    leaf_a = store.create_entity(
        EntityCreate.model_validate({"name": "leaf_a", "entityType": "concept", "observations": []})
    )
    store.create_entity(
        EntityCreate.model_validate({"name": "leaf_b", "entityType": "concept", "observations": []})
    )
    store.create_entity(
        EntityCreate.model_validate(
            {"name": "isolated", "entityType": "concept", "observations": []}
        )
    )
    store.create_relation(
        RelationCreate.model_validate(
            {"from": hub.id, "to": leaf_a.id, "relationType": "related_to"}
        )
    )
    store.create_relation(
        RelationCreate.model_validate({"from": hub.id, "to": leaf_a.id, "relationType": "supports"})
    )

    bundle = assemble_bundle(ExportBundleInput.model_validate({"maxEntities": 2}), multi)
    kept_ids = {e["id"] for e in bundle["entities"]}
    assert kept_ids == {hub.id, leaf_a.id}
    assert bundle["meta"]["relationCount"] == 2  # both hub<->leaf_a relations induced


def test_max_entities_truncation_is_reproducible(multi: MultiGraph) -> None:
    store = multi.get_store()
    for name in ("a", "b", "c", "d"):
        store.create_entity(
            EntityCreate.model_validate({"name": name, "entityType": "concept", "observations": []})
        )
    first = assemble_bundle(ExportBundleInput.model_validate({"maxEntities": 2}), multi)
    second = assemble_bundle(ExportBundleInput.model_validate({"maxEntities": 2}), multi)
    assert [e["id"] for e in first["entities"]] == [e["id"] for e in second["entities"]]
