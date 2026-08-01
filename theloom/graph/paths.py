"""Path finding: bidirectional shortest path + bounded all-simple-paths.

Both are implemented directly (rather than via rustworkx dijkstra /
all_simple_paths): equal-length shortest paths are not unique and the chosen
path — and the LIFO enumeration order of all-paths — are observable output, so
the tie-breaking must be deterministic and bit-stable.
"""

from __future__ import annotations

import time

from theloom.graph.hydrate import LoomGraph


def bidirectional(graph: LoomGraph, source: str, target: str) -> list[str] | None:
    """Bidirectional BFS shortest path."""
    if source == target:
        return [source]

    predecessor: dict[str, str | None] = {source: None}
    successor: dict[str, str | None] = {target: None}
    forward_fringe = [source]
    reverse_fringe = [target]
    found = False
    neighbor = ""

    while forward_fringe and reverse_fringe and not found:
        if len(forward_fringe) <= len(reverse_fringe):
            current, forward_fringe = forward_fringe, []
            for node in current:
                for neighbor in graph.out_neighbors(node):
                    if neighbor not in predecessor:
                        forward_fringe.append(neighbor)
                        predecessor[neighbor] = node
                    if neighbor in successor:
                        found = True
                        break
                if found:
                    break
        else:
            current, reverse_fringe = reverse_fringe, []
            for node in current:
                for neighbor in graph.in_neighbors(node):
                    if neighbor not in successor:
                        reverse_fringe.append(neighbor)
                        successor[neighbor] = node
                    if neighbor in predecessor:
                        found = True
                        break
                if found:
                    break

    if not found:
        return None

    path: list[str] = []
    walk: str | None = neighbor
    while walk:
        path.insert(0, walk)
        walk = predecessor.get(walk)
    walk = successor.get(path[-1])
    while walk:
        path.append(walk)
        walk = successor.get(walk)
    return path if path else None


def bounded_all_simple_paths(
    graph: LoomGraph,
    source: str,
    target: str,
    max_depth: int,
    max_paths: int,
    timeout_ms: int,
) -> dict[str, object]:
    """Iterative LIFO DFS with maxPaths/timeout truncation."""
    paths: list[list[str]] = []
    start = time.monotonic()
    stack: list[tuple[str, list[str], set[str]]] = [(source, [source], {source})]

    while stack:
        if (time.monotonic() - start) * 1000 > timeout_ms:
            return {"paths": paths, "truncated": True, "truncationReason": "timeout"}
        node, path, visited = stack.pop()
        if node == target and len(path) > 1:
            paths.append(path)
            if len(paths) >= max_paths:
                return {"paths": paths, "truncated": True, "truncationReason": "maxPaths"}
            continue
        if len(path) - 1 >= max_depth:
            continue
        for neighbor in graph.out_neighbors(node):
            if neighbor not in visited:
                stack.append((neighbor, [*path, neighbor], {*visited, neighbor}))

    return {"paths": paths, "truncated": False}
