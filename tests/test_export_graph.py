"""export-graph — a compact, zero-infrastructure JSON artifact.

CLI-level tests through run_handler, per the CLI test convention
(tests/test_cli_viz_commands.py).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from theloom.cli.registry import COMMANDS, run_handler
from theloom.errors import LoomError
from theloom.model import EntityCreate, RelationCreate
from theloom.store.multigraph import MultiGraph


def test_registry_has_export_graph_command() -> None:
    by_name = {c.name: c for c in COMMANDS}
    assert "export-graph" in by_name


def test_export_graph_round_trips_a_small_graph(multi: MultiGraph, tmp_path: Path) -> None:
    store = multi.get_store()
    a = store.create_entity(
        EntityCreate.model_validate(
            {"name": "a", "entityType": "concept", "observations": ["obs one"]}
        )
    )
    b = store.create_entity(
        EntityCreate.model_validate(
            {"name": "b", "entityType": "concept", "observations": ["obs two"]}
        )
    )
    store.create_relation(
        RelationCreate.model_validate(
            {
                "from": a.id,
                "to": b.id,
                "relationType": "related_to",
                "evidence": "seen together",
            }
        )
    )

    output = tmp_path / "graph.json"
    result = run_handler("export-graph", {"output": str(output)}, multi)

    assert result["path"] == str(output)
    assert result["entityCount"] == 2
    assert result["relationCount"] == 1
    assert result["bytes"] == len(output.read_bytes())

    payload = json.loads(output.read_text())
    assert payload["meta"]["graph"] == "default"
    assert payload["meta"]["counts"] == {"entities": 2, "relations": 1}
    assert "generated" in payload["meta"]

    assert {e["name"] for e in payload["entities"]} == {"a", "b"}
    entity_a = next(e for e in payload["entities"] if e["name"] == "a")
    assert set(entity_a.keys()) == {"id", "name", "entityType", "observations"}
    assert entity_a["observations"] == ["obs one"]

    assert len(payload["relations"]) == 1
    relation = payload["relations"][0]
    assert set(relation.keys()) == {"from", "to", "relationType", "evidence"}
    assert relation["from"] == a.id
    assert relation["to"] == b.id
    assert relation["relationType"] == "related_to"
    assert relation["evidence"] == "seen together"


def test_export_graph_defaults_to_active_only(multi: MultiGraph, tmp_path: Path) -> None:
    store = multi.get_store()
    store.create_entity(
        EntityCreate.model_validate({"name": "live", "entityType": "concept", "observations": []})
    )
    superseded_by = store.create_entity(
        EntityCreate.model_validate(
            {"name": "superseded-by", "entityType": "concept", "observations": []}
        )
    )
    old = store.create_entity(
        EntityCreate.model_validate({"name": "old", "entityType": "concept", "observations": []})
    )
    run_handler(
        "update-entity",
        {"id": old.id, "status": "superseded", "replacedById": superseded_by.id},
        multi,
    )

    output = tmp_path / "graph.json"
    result = run_handler("export-graph", {"output": str(output)}, multi)
    payload = json.loads(output.read_text())
    names = {e["name"] for e in payload["entities"]}
    assert names == {"live", "superseded-by"}
    assert result["entityCount"] == 2

    output2 = tmp_path / "graph-with-superseded.json"
    run_handler("export-graph", {"output": str(output2), "includeSuperseded": True}, multi)
    payload2 = json.loads(output2.read_text())
    names2 = {e["name"] for e in payload2["entities"]}
    assert names2 == {"live", "superseded-by", "old"}


def test_export_graph_scope_by_entity_type(multi: MultiGraph, tmp_path: Path) -> None:
    store = multi.get_store()
    store.create_entity(
        EntityCreate.model_validate(
            {"name": "concept-a", "entityType": "concept", "observations": []}
        )
    )
    store.create_entity(
        EntityCreate.model_validate(
            {"name": "pattern-a", "entityType": "pattern", "observations": []}
        )
    )

    output = tmp_path / "graph.json"
    result = run_handler("export-graph", {"output": str(output), "entityTypes": ["pattern"]}, multi)
    payload = json.loads(output.read_text())
    assert {e["name"] for e in payload["entities"]} == {"pattern-a"}
    assert result["entityCount"] == 1


def test_export_graph_size_guard_trips_without_force(  # type: ignore[no-untyped-def]
    multi: MultiGraph, tmp_path: Path, monkeypatch
) -> None:
    import theloom.operations.portability as portability

    monkeypatch.setattr(portability, "MAX_EXPORT_BYTES", 1)
    store = multi.get_store()
    store.create_entity(
        EntityCreate.model_validate({"name": "a", "entityType": "concept", "observations": []})
    )
    output = tmp_path / "graph.json"

    with pytest.raises(LoomError) as err:
        run_handler("export-graph", {"output": str(output)}, multi)
    assert err.value.code == "OPERATION_ERROR"
    assert not output.exists()

    result = run_handler("export-graph", {"output": str(output), "force": True}, multi)
    assert result["entityCount"] == 1
    assert output.exists()


def test_export_graph_unknown_graph_is_not_found(multi: MultiGraph, tmp_path: Path) -> None:
    output = tmp_path / "graph.json"
    with pytest.raises(LoomError) as err:
        run_handler("export-graph", {"output": str(output), "graph": "does-not-exist"}, multi)
    assert err.value.code == "NOT_FOUND"
