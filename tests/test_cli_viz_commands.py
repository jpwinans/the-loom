"""Visualization command tests through run_handler, per the CLI test convention."""

from __future__ import annotations

from pathlib import Path

import pytest

from theloom.cli.registry import COMMANDS, run_handler
from theloom.errors import LoomError
from theloom.model import EntityCreate
from theloom.store.multigraph import MultiGraph


def test_registry_has_visualization_commands() -> None:
    by_name = {c.name: c for c in COMMANDS}
    assert by_name["visualize"].category == "Visualization"
    assert by_name["export-bundle"].category == "Visualization"


def test_export_bundle_returns_bundle(multi: MultiGraph) -> None:
    multi.get_store().create_entity(
        EntityCreate.model_validate({"name": "a", "entityType": "concept", "observations": []})
    )
    result = run_handler("export-bundle", {}, multi)
    assert result["schemaVersion"] == 1
    assert result["meta"]["entityCount"] == 1


def test_visualize_writes_file(multi: MultiGraph, tmp_path: Path) -> None:
    multi.get_store().create_entity(
        EntityCreate.model_validate({"name": "a", "entityType": "concept", "observations": []})
    )
    output = tmp_path / "out.html"
    result = run_handler("visualize", {"output": str(output)}, multi)
    assert result["path"] == str(output)
    assert result["entityCount"] == 1
    assert result["bytes"] == len(output.read_bytes())
    assert "tapestry-data" in output.read_text()


def test_visualize_bad_theme_is_validation_error(multi: MultiGraph) -> None:
    with pytest.raises(LoomError) as err:
        run_handler("visualize", {"theme": "sepia"}, multi)
    assert err.value.code == "VALIDATION_ERROR"


def test_export_bundle_search_scope(multi: MultiGraph, monkeypatch) -> None:  # type: ignore[no-untyped-def]
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

    class _Stub:
        def embed_query(self, text: str) -> list[float]:
            return [1.0, 0.0]

    monkeypatch.setattr("theloom.operations.semantic.get_embedder", lambda: _Stub())
    result = run_handler("export-bundle", {"scope": {"mode": "search", "query": "vector"}}, multi)
    assert {e["name"] for e in result["entities"]} == {"vector search"}
    assert result["meta"]["scope"] == "search:vector"


def test_serve_check_returns_the_handshake_without_binding(multi: MultiGraph) -> None:
    result = run_handler("serve", {"check": True, "host": "127.0.0.1", "port": 8123}, multi)
    assert result["host"] == "127.0.0.1"
    assert result["port"] == 8123
    assert result["url"] == "http://127.0.0.1:8123"
    assert result["graph"] == "default"


def test_serve_registered_under_visualization() -> None:
    by_name = {c.name: c for c in COMMANDS}
    assert by_name["serve"].category == "Visualization"


def test_serve_blocking_prints_handshake_then_runs(multi: MultiGraph, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    pytest.importorskip("fastapi")  # create_app needs the extra
    calls: list[tuple[str, int]] = []
    # Stub the blocking runner so the test never binds a port.
    monkeypatch.setattr(
        "theloom.viz.serve.run_uvicorn",
        lambda app, host, port: calls.append((host, port)),
    )
    run_handler("serve", {"host": "127.0.0.1", "port": 8124}, multi)
    assert calls == [("127.0.0.1", 8124)]
    printed = capsys.readouterr().out
    assert '"url": "http://127.0.0.1:8124"' in printed
