"""Multi-Graph Landscape composite.

Ecosystem-level overview of every registered graph: the graph list, inter-graph
connection counts, bridge
relations, per-graph stats, and related-graph discovery. Sections 4 and 5 depend
on section 1 and degrade to :func:`failed_section` if the graph list failed.
"""

from __future__ import annotations

from typing import Any

from theloom.composites.framework import failed_section, run_composite, time_section
from theloom.operations.analysis import GraphOnlyInput, graph_stats
from theloom.operations.common import CommandInput
from theloom.operations.multigraph import GraphInput, find_related_graphs
from theloom.store.multigraph import MultiGraph


class MultiGraphLandscapeInput(CommandInput):
    graph: str | None = None


def multi_graph_landscape(params: MultiGraphLandscapeInput, multi: MultiGraph) -> dict[str, Any]:
    def _graphs() -> list[dict[str, Any]]:
        # list_graphs yields only {name, loaded}; entityCount/relationCount are
        # absent (omitted rather than emitted as null).
        return [{"name": g["name"], "loaded": g["loaded"]} for g in multi.list_graphs()]

    graphs_section = time_section(_graphs)

    def _connections() -> list[dict[str, Any]]:
        counts: dict[tuple[str, str], int] = {}
        for bridge in multi.bridges.list_bridges():
            key = (bridge["from_graph"], bridge["to_graph"])
            counts[key] = counts.get(key, 0) + 1
        return [
            {"fromGraph": from_graph, "toGraph": to_graph, "count": count}
            for (from_graph, to_graph), count in sorted(counts.items())
        ]

    def _bridges() -> list[dict[str, Any]]:
        return [
            {
                "from": b["from"],
                "to": b["to"],
                "fromGraph": b["from_graph"],
                "toGraph": b["to_graph"],
                "relationType": b["relationType"],
            }
            for b in multi.bridges.list_bridges()
        ]

    def _per_graph_stats() -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for g in graphs_section["data"]:
            stats = graph_stats(GraphOnlyInput(graph=g["name"]), multi)
            entries.append(
                {
                    "name": g["name"],
                    "entityCount": stats["entityCount"],
                    "relationCount": stats["relationCount"],
                    "entityTypeDistribution": stats["entityTypeDistribution"],
                    "relationTypeDistribution": stats["relationTypeDistribution"],
                }
            )
        return entries

    graphs_unavailable = graphs_section["error"] is not None or graphs_section["data"] is None
    if graphs_unavailable:
        per_graph_stats_section = failed_section(
            "Graph list unavailable -- cannot compute per-graph stats"
        )
    else:
        per_graph_stats_section = time_section(_per_graph_stats)

    if params.graph:
        focus_graph = params.graph

        def _related_specific() -> dict[str, list[str]]:
            return {focus_graph: find_related_graphs(GraphInput(graph=focus_graph), multi)}

        related_section = time_section(_related_specific)
    elif graphs_unavailable:
        related_section = failed_section("Graph list unavailable -- cannot discover related graphs")
    else:

        def _related_all() -> dict[str, list[str]]:
            return {
                g["name"]: find_related_graphs(GraphInput(graph=g["name"]), multi)
                for g in graphs_section["data"]
            }

        related_section = time_section(_related_all)

    return run_composite(
        [
            ("graphs", graphs_section),
            ("connections", _connections),
            ("bridges", _bridges),
            ("perGraphStats", per_graph_stats_section),
            ("relatedGraphs", related_section),
        ]
    )
