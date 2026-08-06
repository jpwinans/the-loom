"""Live-server API tests via FastAPI's TestClient — no port is ever bound.

Skipped when the viz-serve extra is absent (bare `uv run pytest`); CI installs
`--extra viz-serve` so they run. The FalkorDB fixtures come from conftest."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")  # viz-serve extra; mirrors the UMAP importorskip

from fastapi.testclient import TestClient  # noqa: E402

from tests.fakes import FakeEmbedder  # noqa: E402
from theloom.model import EntityCreate, RelationCreate  # noqa: E402
from theloom.store.multigraph import MultiGraph  # noqa: E402
from theloom.viz.serve import create_app  # noqa: E402


@pytest.fixture()
def client(multi: MultiGraph) -> TestClient:
    return TestClient(create_app(multi))


def test_graphs_lists_the_default_graph(client: TestClient) -> None:
    response = client.get("/api/graphs")
    assert response.status_code == 200
    names = [g["name"] for g in response.json()]
    assert "default" in names


def test_status_map_pins_the_http_codes() -> None:
    from theloom.viz.serve import _STATUS

    assert _STATUS["NOT_FOUND"] == 404
    assert _STATUS["VALIDATION_ERROR"] == 422
    assert _STATUS["CONFIG_ERROR"] == 500


def test_bundle_returns_the_scoped_bundle(client: TestClient, multi: MultiGraph) -> None:
    multi.get_store().create_entity(
        EntityCreate.model_validate({"name": "a", "entityType": "concept", "observations": []})
    )
    response = client.get("/api/bundle")
    assert response.status_code == 200
    body = response.json()
    assert body["schemaVersion"] == 1
    assert body["meta"]["entityCount"] == 1


def test_bundle_missing_graph_is_404(client: TestClient) -> None:
    response = client.get("/api/bundle", params={"graph": "does-not-exist"})
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_bundle_bad_asof_is_422(client: TestClient, multi: MultiGraph) -> None:
    multi.get_store().create_entity(
        EntityCreate.model_validate({"name": "a", "entityType": "concept", "observations": []})
    )
    response = client.get("/api/bundle", params={"asOf": "not-a-timestamp"})
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_as_of_requires_the_param(client: TestClient) -> None:
    response = client.get("/api/as-of")
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_bundle_ego_scope_needs_a_center(client: TestClient, multi: MultiGraph) -> None:
    multi.get_store().create_entity(
        EntityCreate.model_validate({"name": "a", "entityType": "concept", "observations": []})
    )
    response = client.get("/api/bundle", params={"mode": "ego"})  # no center
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_neighbors_returns_the_ego_subgraph(client: TestClient, multi: MultiGraph) -> None:
    store = multi.get_store()
    a = store.create_entity(
        EntityCreate.model_validate({"name": "a", "entityType": "variable", "observations": []})
    )
    b = store.create_entity(
        EntityCreate.model_validate({"name": "b", "entityType": "variable", "observations": []})
    )
    store.create_relation(
        RelationCreate.model_validate(
            {"from": a.id, "to": b.id, "relationType": "causes", "polarity": "+"}
        )
    )
    response = client.get("/api/neighbors", params={"id": a.id, "depth": 1})
    assert response.status_code == 200
    body = response.json()
    assert {e["id"] for e in body["entities"]} == {a.id, b.id}
    assert [r["relationType"] for r in body["relations"]] == ["causes"]


def test_neighbors_unknown_id_is_404(client: TestClient) -> None:
    response = client.get("/api/neighbors", params={"id": "nope"})
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_search_returns_hits(client: TestClient, multi: MultiGraph, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    store = multi.get_store()
    hit = store.create_entity(
        EntityCreate.model_validate(
            {"name": "vector search", "entityType": "concept", "observations": []}
        )
    )
    miss = store.create_entity(
        EntityCreate.model_validate(
            {"name": "unrelated", "entityType": "concept", "observations": []}
        )
    )
    store.set_entity_vector(hit.id, [1.0, 0.0])
    store.set_entity_vector(miss.id, [0.0, 1.0])

    monkeypatch.setattr(
        "theloom.operations.semantic.get_embedder", lambda: FakeEmbedder([1.0, 0.0])
    )
    response = client.get("/api/search", params={"q": "vector", "limit": 5})
    assert response.status_code == 200
    names = [h["name"] for h in response.json()]
    assert names[0] == "vector search"


def test_entity_returns_the_wire_doc(client: TestClient, multi: MultiGraph) -> None:
    entity = multi.get_store().create_entity(
        EntityCreate.model_validate(
            {"name": "solo", "entityType": "concept", "observations": ["x"]}
        )
    )
    response = client.get(f"/api/entity/{entity.id}")
    assert response.status_code == 200
    assert response.json()["name"] == "solo"


def test_entity_unknown_is_404(client: TestClient) -> None:
    response = client.get("/api/entity/missing")
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_bundle_maps_each_query_param_to_its_own_scope_field(
    client: TestClient, multi: MultiGraph
) -> None:
    """Pins /api/bundle's query-param -> ExportBundleInput mapping: entityType
    must land on scope.entityType (not relationType) and vice versa, title and
    the include flags must land on their own fields — a swapped positional
    argument in the construction would move one of these onto the wrong
    field and this would catch it via meta.scope/meta.title/meta.sections,
    independently computed from the request rather than from the code path."""
    multi.get_store().create_entity(
        EntityCreate.model_validate({"name": "a", "entityType": "concept", "observations": []})
    )
    response = client.get(
        "/api/bundle",
        params={
            "mode": "typed",
            "entityType": "concept",
            "relationType": "causes",
            "title": "My Title",
            "analytics": "false",
            "temporal": "false",
            "semantic": "false",
        },
    )
    assert response.status_code == 200
    meta = response.json()["meta"]
    assert meta["scope"] == "typed:concept/causes"
    assert meta["title"] == "My Title"
    assert meta["sections"] == []


def test_as_of_maps_the_asof_param_onto_the_bundle(
    client: TestClient, multi: MultiGraph
) -> None:
    """Pins /api/as-of's construction: asOf lands on the bundle's asOf field
    (not swapped onto title or elsewhere) and the scope/include flags stay
    at their fixed full/true/true/true defaults for this endpoint."""
    multi.get_store().create_entity(
        EntityCreate.model_validate({"name": "a", "entityType": "concept", "observations": []})
    )
    as_of = "2026-01-01T00:00:00+00:00"
    response = client.get("/api/as-of", params={"asOf": as_of})
    assert response.status_code == 200
    meta = response.json()["meta"]
    assert meta["asOf"] == as_of
    assert meta["scope"] == "full"
    # semantic omits itself below _MIN_VECTORS regardless of the include flag,
    # so only analytics/temporal (unconditional here) pin the True/True mapping.
    assert {"analytics", "temporal"}.issubset(meta["sections"])
