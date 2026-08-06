"""asOf bi-temporal bound — the as-of entity snapshot agrees with
read_entity_as_of, relations prune to survivors, temporal truncates."""

from __future__ import annotations

import time

import pytest

from tests.fakes import FakeEmbedder
from theloom.errors import LoomError
from theloom.model import EntityCreate, RelationCreate
from theloom.store.multigraph import MultiGraph
from theloom.timeutil import iso_now
from theloom.viz.bundle import ExportBundleInput, assemble_bundle


def test_as_of_shows_prior_incarnation(multi: MultiGraph) -> None:
    store = multi.get_store()
    entity = store.create_entity(
        EntityCreate.model_validate({"name": "a", "entityType": "concept", "observations": []})
    )
    time.sleep(0.01)
    pivot = iso_now()  # strictly after create, strictly before the update
    time.sleep(0.01)
    store.update_entity(entity.id, {"name": "a2"})

    now_doc = assemble_bundle(ExportBundleInput(), multi)
    assert now_doc["entities"][0]["name"] == "a2"
    assert "asOf" not in now_doc["meta"]

    as_of_doc = assemble_bundle(ExportBundleInput.model_validate({"asOf": pivot}), multi)
    assert as_of_doc["entities"][0]["name"] == "a"
    assert as_of_doc["meta"]["asOf"] == pivot


def test_as_of_prunes_relations_and_entities_created_later(multi: MultiGraph) -> None:
    store = multi.get_store()
    a = store.create_entity(
        EntityCreate.model_validate({"name": "a", "entityType": "variable", "observations": []})
    )
    time.sleep(0.01)
    pivot = iso_now()
    time.sleep(0.01)
    b = store.create_entity(
        EntityCreate.model_validate({"name": "b", "entityType": "variable", "observations": []})
    )
    store.create_relation(
        RelationCreate.model_validate({"from": a.id, "to": b.id, "relationType": "causes"})
    )

    as_of_doc = assemble_bundle(ExportBundleInput.model_validate({"asOf": pivot}), multi)
    assert {e["name"] for e in as_of_doc["entities"]} == {"a"}  # b not yet born
    assert as_of_doc["relations"] == []  # its only edge references the unborn b
    assert as_of_doc["meta"]["relationCount"] == 0


def test_as_of_restores_a_relation_retired_since_the_bound(multi: MultiGraph) -> None:
    """Retracting an entity closes out its edges. A bundle bounded before that
    must still show the neighbourhood as it stood — the edge was live then."""
    store = multi.get_store()
    a = store.create_entity(
        EntityCreate.model_validate({"name": "a", "entityType": "variable", "observations": []})
    )
    b = store.create_entity(
        EntityCreate.model_validate({"name": "b", "entityType": "variable", "observations": []})
    )
    store.create_relation(
        RelationCreate.model_validate({"from": a.id, "to": b.id, "relationType": "causes"})
    )
    time.sleep(0.01)
    pivot = iso_now()
    time.sleep(0.01)
    store.delete_entity(b.id)

    now_doc = assemble_bundle(ExportBundleInput(), multi)
    assert now_doc["relations"] == []  # the retraction took the edge with it

    as_of_doc = assemble_bundle(ExportBundleInput.model_validate({"asOf": pivot}), multi)
    assert [(r["from"], r["to"]) for r in as_of_doc["relations"]] == [(a.id, b.id)]
    assert as_of_doc["meta"]["relationCount"] == 1
    assert [e["name"] for e in as_of_doc["entities"]] == ["a", "b"]


def test_as_of_truncates_temporal_events(multi: MultiGraph) -> None:
    store = multi.get_store()
    store.create_entity(
        EntityCreate.model_validate({"name": "a", "entityType": "concept", "observations": []})
    )
    time.sleep(0.01)
    pivot = iso_now()
    time.sleep(0.01)
    store.create_entity(
        EntityCreate.model_validate({"name": "b", "entityType": "concept", "observations": []})
    )
    as_of_doc = assemble_bundle(ExportBundleInput.model_validate({"asOf": pivot}), multi)
    types = [e["type"] for e in as_of_doc["temporal"]["events"]]
    assert types == ["entity_created"]  # only the first create is at/before pivot


def test_malformed_as_of_is_validation_error(multi: MultiGraph) -> None:
    with pytest.raises(LoomError) as err:
        assemble_bundle(ExportBundleInput.model_validate({"asOf": "not-a-timestamp"}), multi)
    assert err.value.code == "VALIDATION_ERROR"


def test_as_of_stamps_analytics_and_semantic_as_current(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Analytics and semantic are never recomputed as-of a historical bound —
    they must say so, rather than silently mixing two times in one bundle."""
    store = multi.get_store()
    vectors = {"a": [1.0, 0.0, 0.0], "b": [0.0, 1.0, 0.0], "c": [0.0, 0.0, 1.0]}
    for name, vector in vectors.items():
        entity = store.create_entity(
            EntityCreate.model_validate({"name": name, "entityType": "concept", "observations": []})
        )
        store.set_entity_vector(entity.id, vector)
    monkeypatch.setattr("theloom.operations.semantic.get_embedder", lambda: FakeEmbedder(vectors))
    time.sleep(0.01)
    pivot = iso_now()

    now_doc = assemble_bundle(ExportBundleInput(), multi)
    assert "temporalScope" not in now_doc["analytics"]
    assert "temporalScope" not in now_doc["semantic"]

    as_of_doc = assemble_bundle(ExportBundleInput.model_validate({"asOf": pivot}), multi)
    assert as_of_doc["analytics"]["temporalScope"] == "current"
    assert as_of_doc["semantic"]["temporalScope"] == "current"
