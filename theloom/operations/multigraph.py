"""Ordinary graph-level queries over the MultiGraph facade's bridge index.

Moved out of the CLI registry (which should hold only command wiring): these
are plain reads over ``multi.bridges``, with no CLI-specific concerns.
"""

from __future__ import annotations

from typing import Any

from theloom.errors import NotFoundError
from theloom.operations.common import CommandInput
from theloom.operations.notices import list_envelope
from theloom.store.multigraph import MultiGraph


class GraphInput(CommandInput):
    graph: str


class EmptyInput(CommandInput):
    pass


def find_related_graphs(params: GraphInput, multi: MultiGraph) -> dict[str, Any]:
    """Graphs bridged to ``params.graph``, either direction, sorted by name."""
    if not multi.has_graph(params.graph):
        raise NotFoundError(
            f"Graph '{params.graph}' not found. Use list_graphs to see available graphs."
        )
    related: set[str] = set()
    for bridge in multi.bridges.list_bridges():
        if bridge["from_graph"] == params.graph:
            related.add(bridge["to_graph"])
        if bridge["to_graph"] == params.graph:
            related.add(bridge["from_graph"])
    return list_envelope(sorted(related))


def graph_connections(_: EmptyInput, multi: MultiGraph) -> dict[str, Any]:
    """Bridge counts between every connected graph pair, sorted by (from, to)."""
    counts: dict[tuple[str, str], int] = {}
    for bridge in multi.bridges.list_bridges():
        key = (bridge["from_graph"], bridge["to_graph"])
        counts[key] = counts.get(key, 0) + 1
    return list_envelope(
        [
            {"from_graph": from_graph, "to_graph": to_graph, "count": count}
            for (from_graph, to_graph), count in sorted(counts.items())
        ]
    )
