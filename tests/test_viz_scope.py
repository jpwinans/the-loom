"""Scope resolution tests over a live namespaced store."""

from __future__ import annotations

import pytest

from tests.fakes import FakeEmbedder
from theloom import config as config_module
from theloom.errors import LoomError
from theloom.model import EntityCreate, RelationCreate
from theloom.store.multigraph import MultiGraph
from theloom.viz.scope import ScopeInput, resolve_scope


@pytest.fixture()
def seeded(multi: MultiGraph) -> dict[str, str]:
    """a --causes--> b --supports--> c ; returns name->id."""
    store = multi.get_store()
    ids: dict[str, str] = {}
    for name, etype in (("a", "variable"), ("b", "variable"), ("c", "claim")):
        entity = store.create_entity(
            EntityCreate.model_validate({"name": name, "entityType": etype, "observations": []})
        )
        ids[name] = entity.id
    store.create_relation(
        RelationCreate.model_validate({"from": ids["a"], "to": ids["b"], "relationType": "causes"})
    )
    store.create_relation(
        RelationCreate.model_validate(
            {"from": ids["b"], "to": ids["c"], "relationType": "supports"}
        )
    )
    return ids


def test_full_scope(multi: MultiGraph, seeded: dict[str, str]) -> None:
    entities, relations, label = resolve_scope(ScopeInput(), multi.get_store())
    assert {e["name"] for e in entities} == {"a", "b", "c"}
    assert len(relations) == 2
    assert label == "full"


def test_causal_scope_keeps_only_causal_relations(
    multi: MultiGraph, seeded: dict[str, str]
) -> None:
    entities, relations, label = resolve_scope(ScopeInput(mode="causal"), multi.get_store())
    assert {e["name"] for e in entities} == {"a", "b"}
    assert [r["relationType"] for r in relations] == ["causes"]
    assert label == "causal"


def test_ego_scope(multi: MultiGraph, seeded: dict[str, str]) -> None:
    entities, _, label = resolve_scope(
        ScopeInput(mode="ego", center=seeded["a"], depth=1), multi.get_store()
    )
    assert {e["name"] for e in entities} == {"a", "b"}
    assert label == f"ego:{seeded['a']}:d1"


def test_typed_scope(multi: MultiGraph, seeded: dict[str, str]) -> None:
    entities, relations, _ = resolve_scope(
        ScopeInput.model_validate({"mode": "typed", "entityType": "variable"}),
        multi.get_store(),
    )
    assert {e["name"] for e in entities} == {"a", "b"}
    assert [r["relationType"] for r in relations] == ["causes"]


def test_ego_without_center_is_validation_error(multi: MultiGraph) -> None:
    with pytest.raises(LoomError) as err:
        resolve_scope(ScopeInput(mode="ego"), multi.get_store())
    assert err.value.code == "VALIDATION_ERROR"


def test_ego_with_missing_center_is_not_found(multi: MultiGraph, seeded: dict[str, str]) -> None:
    with pytest.raises(LoomError) as err:
        resolve_scope(
            ScopeInput(mode="ego", center="00000000-0000-0000-0000-000000000000"),
            multi.get_store(),
        )
    assert err.value.code == "NOT_FOUND"


def test_unknown_mode_is_validation_error(multi: MultiGraph) -> None:
    with pytest.raises(LoomError) as err:
        resolve_scope(ScopeInput(mode="banana"), multi.get_store())
    assert err.value.code == "VALIDATION_ERROR"


def test_search_scope_keeps_matches_and_induced_relations(
    multi: MultiGraph, seeded: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    store = multi.get_store()
    # Seed vectors: a,b close to the query direction; c orthogonal (excluded).
    store.set_entity_vector(seeded["a"], [1.0, 0.0])
    store.set_entity_vector(seeded["b"], [0.98, 0.20])
    store.set_entity_vector(seeded["c"], [0.0, 1.0])
    monkeypatch.setattr(config_module, "_embedder_override", FakeEmbedder([1.0, 0.0]))
    entities, relations, label = resolve_scope(
        ScopeInput.model_validate({"mode": "search", "query": "a and b"}),
        store,
    )
    names = {e["name"] for e in entities}
    assert names == {"a", "b"}  # c is orthogonal to the query
    assert [r["relationType"] for r in relations] == ["causes"]  # a->b, both matched
    assert label == "search:a and b"


def test_search_scope_requires_a_query(multi: MultiGraph, seeded: dict[str, str]) -> None:
    with pytest.raises(LoomError) as err:
        resolve_scope(ScopeInput(mode="search"), multi.get_store())
    assert err.value.code == "VALIDATION_ERROR"


def test_search_scope_accepts_paraphrase_level_matches(
    multi: MultiGraph, seeded: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The search floor must admit a genuine-but-inexact match, not just near
    duplicates: b sits at cosine 0.8 from the query (score ~0.61) — a real
    topical match well under find_clusters' near-duplicate 0.7 bar, which a
    real short query against a real entity's full text rarely clears. c stays
    orthogonal noise and must still be excluded."""
    store = multi.get_store()
    store.set_entity_vector(seeded["a"], [1.0, 0.0])
    store.set_entity_vector(seeded["b"], [0.8, 0.6])
    store.set_entity_vector(seeded["c"], [0.0, 1.0])
    monkeypatch.setattr(config_module, "_embedder_override", FakeEmbedder([1.0, 0.0]))
    entities, _relations, _label = resolve_scope(
        ScopeInput.model_validate({"mode": "search", "query": "topic"}), store
    )
    assert {e["name"] for e in entities} == {"a", "b"}
