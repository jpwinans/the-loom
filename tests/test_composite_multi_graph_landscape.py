"""Multi-Graph Landscape composite: section shape, and the one thing that could
drift now that its related-graph discovery is no longer a private copy.

The composite used to carry its own `_find_related` that duplicated the
`find-related-graphs` command line for line. Both now call
`theloom.operations.multigraph.find_related_graphs`, so these tests pin the
agreement rather than trusting two copies to stay in step.
"""

from __future__ import annotations

from typing import Any

from theloom.composites.multi_graph_landscape import (
    MultiGraphLandscapeInput,
    multi_graph_landscape,
)
from theloom.operations.multigraph import GraphInput, find_related_graphs
from theloom.store.multigraph import MultiGraph

_SECTIONS = ("graphs", "connections", "bridges", "perGraphStats", "relatedGraphs")


def _bridge(from_graph: str, to_graph: str, from_id: str, to_id: str) -> dict[str, Any]:
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


def test_landscape_reports_every_section_and_the_envelope(multi: MultiGraph) -> None:
    result = multi_graph_landscape(MultiGraphLandscapeInput(), multi)
    sections = result["result"]
    assert list(sections) == list(_SECTIONS)
    for name in _SECTIONS:
        assert set(sections[name]) == {"data", "durationMs", "error"}
        assert sections[name]["error"] is None
    assert result["metadata"]["sectionsSucceeded"] == len(_SECTIONS)
    assert result["metadata"]["sectionsFailed"] == 0
    assert [g["name"] for g in sections["graphs"]["data"]] == ["default"]


def test_related_graphs_agrees_with_the_find_related_graphs_command(multi: MultiGraph) -> None:
    """default <-> research bridged, isolated bridged to nothing."""
    multi.create_graph("research")
    multi.create_graph("isolated")
    multi.bridges.create_bridge(_bridge("default", "research", "e1", "e2"))

    landscape = multi_graph_landscape(MultiGraphLandscapeInput(), multi)
    related = landscape["result"]["relatedGraphs"]["data"]
    assert related == {"default": ["research"], "isolated": [], "research": ["default"]}
    for graph, expected in related.items():
        assert find_related_graphs(GraphInput(graph=graph), multi) == expected


def test_focus_graph_narrows_related_to_that_graph_alone(multi: MultiGraph) -> None:
    multi.create_graph("research")
    multi.bridges.create_bridge(_bridge("research", "default", "e1", "e2"))

    result = multi_graph_landscape(MultiGraphLandscapeInput(graph="research"), multi)
    assert result["result"]["relatedGraphs"]["data"] == {"research": ["default"]}


def test_connections_count_bridges_per_ordered_pair(multi: MultiGraph) -> None:
    multi.create_graph("research")
    multi.bridges.create_bridge(_bridge("default", "research", "e1", "e2"))
    multi.bridges.create_bridge(_bridge("default", "research", "e3", "e4"))

    result = multi_graph_landscape(MultiGraphLandscapeInput(), multi)
    sections = result["result"]
    assert sections["connections"]["data"] == [
        {"fromGraph": "default", "toGraph": "research", "count": 2}
    ]
    assert [b["from"] for b in sections["bridges"]["data"]] == ["e1", "e3"]
