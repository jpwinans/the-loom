"""Multi-Graph command handler tests.

Handlers produce the exact wire shapes the CLI emits: list-graphs is sorted
GraphInfo with loaded:false in one-shot mode; create/delete return their
success strings; find-related-graphs sorts names; graph-connections counts
pairs sorted by from_graph then to_graph. Error codes per the CLI contract.
"""

from __future__ import annotations

import pytest

from theloom.cli.registry import COMMANDS, run_handler
from theloom.errors import LoomError
from theloom.store.multigraph import MultiGraph


def bridge(from_id: str, to_id: str, from_graph: str, to_graph: str) -> dict[str, object]:
    return {
        "from": from_id,
        "to": to_id,
        "relationType": "supports",
        "polarity": None,
        "strength": "moderate",
        "evidence": None,
        "from_graph": from_graph,
        "to_graph": to_graph,
    }


def test_registry_has_the_six_multi_graph_commands() -> None:
    names = {c.name for c in COMMANDS}
    assert {
        "list-graphs",
        "create-graph",
        "delete-graph",
        "list-bridges",
        "find-related-graphs",
        "graph-connections",
    } <= names


def test_list_graphs_shape(multi: MultiGraph) -> None:
    multi.create_graph("research")
    result = run_handler("list-graphs", {}, multi)
    assert result == [
        {"name": "default", "loaded": False},
        {"name": "research", "loaded": False},
    ]


def test_create_graph_success_string(multi: MultiGraph) -> None:
    result = run_handler("create-graph", {"name": "research"}, multi)
    assert result == "Graph 'research' created successfully."
    assert multi.has_graph("research")


def test_create_graph_validation_errors(multi: MultiGraph) -> None:
    with pytest.raises(LoomError) as missing:
        run_handler("create-graph", {}, multi)
    assert missing.value.code == "VALIDATION_ERROR"
    with pytest.raises(LoomError) as invalid:
        run_handler("create-graph", {"name": "_bad"}, multi)
    assert invalid.value.code == "VALIDATION_ERROR"
    run_handler("create-graph", {"name": "dup"}, multi)
    with pytest.raises(LoomError) as duplicate:
        run_handler("create-graph", {"name": "dup"}, multi)
    assert duplicate.value.code == "OPERATION_ERROR"


def test_delete_graph(multi: MultiGraph) -> None:
    multi.create_graph("research")
    result = run_handler("delete-graph", {"name": "research"}, multi)
    assert result == "Graph 'research' deleted successfully."
    with pytest.raises(LoomError) as missing:
        run_handler("delete-graph", {"name": "research"}, multi)
    assert missing.value.code == "NOT_FOUND"
    with pytest.raises(LoomError) as default:
        run_handler("delete-graph", {"name": "default"}, multi)
    assert default.value.code == "OPERATION_ERROR"


def test_list_bridges_filters(multi: MultiGraph) -> None:
    multi.bridges.create_bridge(bridge("e1", "e2", "default", "research"))
    multi.bridges.create_bridge(bridge("e2", "e3", "research", "systems"))
    assert len(run_handler("list-bridges", {}, multi)) == 2
    assert len(run_handler("list-bridges", {"from_graph": "default"}, multi)) == 1
    assert len(run_handler("list-bridges", {"entity_id": "e2"}, multi)) == 2
    assert run_handler("list-bridges", {"to_graph": "nowhere"}, multi) == []


def test_find_related_graphs(multi: MultiGraph) -> None:
    multi.create_graph("research")
    multi.create_graph("systems")
    multi.bridges.create_bridge(bridge("e1", "e2", "default", "research"))
    multi.bridges.create_bridge(bridge("e2", "e3", "research", "systems"))
    assert run_handler("find-related-graphs", {"graph": "research"}, multi) == [
        "default",
        "systems",
    ]
    with pytest.raises(LoomError) as missing:
        run_handler("find-related-graphs", {"graph": "nowhere"}, multi)
    assert missing.value.code == "NOT_FOUND"


def test_graph_connections_counts_sorted(multi: MultiGraph) -> None:
    multi.bridges.create_bridge(bridge("e1", "e2", "zeta", "alpha"))
    multi.bridges.create_bridge(bridge("e3", "e4", "alpha", "beta"))
    multi.bridges.create_bridge(bridge("e5", "e6", "zeta", "alpha"))
    assert run_handler("graph-connections", {}, multi) == [
        {"from_graph": "alpha", "to_graph": "beta", "count": 1},
        {"from_graph": "zeta", "to_graph": "alpha", "count": 2},
    ]


def test_unknown_extra_input_keys_are_ignored(multi: MultiGraph) -> None:
    # Input schemas strip unknown keys; match that.
    result = run_handler("create-graph", {"name": "ok", "bogus": 1}, multi)
    assert result == "Graph 'ok' created successfully."
