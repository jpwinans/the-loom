"""Centrality + components.

Algorithms and library use, per algorithm:

- degree: degree/(order-1), parallel edges counted — trivial, computed directly.
- pagerank: power iteration (alpha 0.85, 100 iterations, per-edge weight 1 with
  parallel edges, dangling redistribution, L1 convergence < N*tolerance),
  computed directly for stable scores at 1e-6.
- betweenness: rustworkx betweenness_centrality over deduplicated adjacency
  (parallel edges deduped in the neighborhood index), normalized.
- components: explicit traversals — a DFS stack for weakly connected
  (push-marks-seen LIFO over neighbor order), the path-based strong
  component algorithm for SCC — because component and member order are
  observable output.
"""

from __future__ import annotations

import rustworkx as rx

from theloom.graph.hydrate import LoomGraph


def degree_centrality(graph: LoomGraph) -> dict[str, float]:
    ratio = graph.order - 1
    scores: dict[str, float] = {}
    for node in graph.nodes():
        degree = len(graph.out_edge_ids(node)) + len(graph.in_edge_ids(node))
        scores[node] = degree / ratio if ratio > 0 else 0.0
    return scores


def pagerank_centrality(
    graph: LoomGraph,
    alpha: float = 0.85,
    max_iterations: int = 100,
    tolerance: float = 1e-6,
) -> dict[str, float]:
    nodes = graph.nodes()
    n = len(nodes)
    if n == 0:
        return {}
    index = {node: i for i, node in enumerate(nodes)}
    p = 1 / n

    # Per-edge outbound targets (parallel edges kept, weight 1 each).
    out_targets: list[list[int]] = [[] for _ in range(n)]
    for node in nodes:
        i = index[node]
        for edge_id in graph.out_edge_ids(node):
            out_targets[i].append(index[graph.edge_target(edge_id)])
    dangling = [i for i in range(n) if not out_targets[i]]

    x = [p] * n
    for _ in range(max_iterations):
        x_last = x
        x = [0.0] * n
        dangle_sum = alpha * sum(x_last[i] for i in dangling)
        for i in range(n):
            targets = out_targets[i]
            if targets:
                share = alpha * x_last[i] / len(targets)
                for j in targets:
                    x[j] += share
            x[i] += dangle_sum * p + (1 - alpha) * p
        if sum(abs(x[i] - x_last[i]) for i in range(n)) < n * tolerance:
            return {node: x[index[node]] for node in nodes}
    raise RuntimeError("pagerank: failed to converge")


def betweenness_centrality(graph: LoomGraph) -> dict[str, float]:
    nodes = graph.nodes()
    rx_graph: rx.PyDiGraph[str, None] = rx.PyDiGraph()
    rx_index = {node: rx_graph.add_node(node) for node in nodes}
    for node in nodes:
        for neighbor in graph.out_neighbors(node):  # deduped adjacency
            rx_graph.add_edge(rx_index[node], rx_index[neighbor], None)
    scores = rx.betweenness_centrality(rx_graph, normalized=True)
    return {node: float(scores[rx_index[node]]) for node in nodes}


def connected_components(graph: LoomGraph) -> list[list[str]]:
    """Weakly connected components in DFS-stack order."""
    components: list[list[str]] = []
    seen: set[str] = set()
    for start in graph.nodes():
        if start in seen:
            continue
        component: list[str] = []
        stack: list[str] = []
        seen.add(start)
        stack.append(start)
        while stack:
            source = stack.pop()
            component.append(source)
            for neighbor in graph.neighbors(source):
                if neighbor not in seen:
                    seen.add(neighbor)
                    stack.append(neighbor)
        components.append(component)
    return components


def strongly_connected_components(graph: LoomGraph) -> list[list[str]]:
    """The path-based strong component algorithm."""
    nodes = graph.nodes()
    if not nodes:
        return []
    if graph.size == 0:
        return [[node] for node in nodes]

    count = 1
    preorder: dict[str, int] = {}
    assigned: set[str] = set()
    p_stack: list[str] = []
    s_stack: list[str] = []
    components: list[list[str]] = []

    def dfs(node: str) -> None:
        nonlocal count
        preorder[node] = count
        count += 1
        p_stack.append(node)
        s_stack.append(node)
        for neighbor in graph.out_neighbors(node):
            if neighbor in preorder:
                if neighbor not in assigned:
                    neighbor_order = preorder[neighbor]
                    while preorder[p_stack[-1]] > neighbor_order:
                        p_stack.pop()
            else:
                dfs(neighbor)
        if preorder[p_stack[-1]] == preorder[node]:
            component: list[str] = []
            while True:
                popped = s_stack.pop()
                component.append(popped)
                assigned.add(popped)
                if popped == node:
                    break
            components.append(component)
            p_stack.pop()

    for vertex in nodes:
        if vertex not in assigned:
            dfs(vertex)
    return components
