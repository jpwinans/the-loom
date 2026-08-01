"""Cycle and feedback-loop analysis.

These algorithms are chosen deliberately over rustworkx.simple_cycles: the
outputs are enumeration-order- and rotation-sensitive — loop names derive from
the first entities in each cycle path — so stable, reproducible output requires
the exact algorithms:

- ``find_cycle_paths``: a DFS back-edge walk in insertion order.
- ``find_circuits``: Johnson's algorithm for elementary circuits of a directed
  graph, including a B-set bookkeeping quirk (it marks ``B[w][w]`` rather than
  ``B[w][v]``) that the expected outputs encode.
"""

from __future__ import annotations

from typing import Any

from theloom.graph.hydrate import Doc, LoomGraph
from theloom.model import CAUSAL_RELATION_TYPES
from theloom.store.falkor import FalkorGraphStore
from theloom.timeutil import iso_now

_CAUSAL = {t.value for t in CAUSAL_RELATION_TYPES}


# =============================================================================
# DFS cycle paths (detect-cycles includePaths)
# =============================================================================


def find_cycle_paths(graph: LoomGraph) -> list[list[str]]:
    """Back-edge cycles in DFS discovery order."""
    cycles: list[list[str]] = []
    visited: set[str] = set()
    recursion_stack: set[str] = set()
    current_path: list[str] = []

    def dfs(node: str) -> None:
        visited.add(node)
        recursion_stack.add(node)
        current_path.append(node)
        for neighbor in graph.out_neighbors(node):
            if neighbor not in visited:
                dfs(neighbor)
            elif neighbor in recursion_stack:
                start = current_path.index(neighbor)
                if start != -1:
                    cycles.append([*current_path[start:], neighbor])
        current_path.pop()
        recursion_stack.discard(node)

    for node in graph.nodes():
        if node not in visited:
            dfs(node)
    return cycles


def has_cycle(graph: LoomGraph) -> bool:
    """Cycle existence via DFS coloring."""
    visited: set[str] = set()
    in_stack: set[str] = set()

    def dfs(node: str) -> bool:
        visited.add(node)
        in_stack.add(node)
        for neighbor in graph.out_neighbors(node):
            if neighbor in in_stack:
                return True
            if neighbor not in visited and dfs(neighbor):
                return True
        in_stack.discard(node)
        return False

    return any(node not in visited and dfs(node) for node in graph.nodes())


# =============================================================================
# Johnson's circuits (elementary circuits of a directed graph)
# =============================================================================


def find_circuits(edges: list[list[int]]) -> list[list[int]]:
    """All elementary circuits in Johnson's enumeration order.
    ``edges`` is a mutable adjacency list indexed by vertex; the implementation
    keeps the destructive subgraph filtering and B-set quirk."""
    circuits: list[list[int]] = []
    stack: list[int] = []
    blocked: dict[int, bool] = {}
    b_sets: dict[int, dict[int, bool]] = {}
    adjacency: list[list[int]] = []
    start_vertex = 0

    def unblock(u: int) -> None:
        blocked[u] = False
        if u in b_sets:
            for w in list(b_sets[u]):
                del b_sets[u][w]
                if blocked.get(w):
                    unblock(w)

    def circuit(v: int) -> bool:
        found = False
        stack.append(v)
        blocked[v] = True
        for w in adjacency[v]:
            if w == start_vertex:
                circuits.append([*stack, start_vertex])
                found = True
            elif not blocked.get(w):
                if circuit(w):
                    found = True
        if found:
            unblock(v)
        else:
            for w in adjacency[v]:
                entry = b_sets.setdefault(w, {})
                entry[w] = True  # package quirk, kept verbatim (paper says B[w][v])
        stack.pop()
        return found

    def subgraph(min_id: int) -> None:
        for i in range(len(edges)):
            if i < min_id or edges[i] is None:
                edges[i] = []
            edges[i] = [j for j in edges[i] if j >= min_id]

    def adjacency_structure_scc(from_vertex: int) -> tuple[int, list[list[int]]] | None:
        subgraph(from_vertex)
        components = [c for c in _tarjan_scc(edges) if len(c) > 1]
        least_vertex = None
        least_component: list[int] | None = None
        for component in components:
            for vertex in component:
                if least_vertex is None or vertex < least_vertex:
                    least_vertex = vertex
                    least_component = component
        if least_component is None or least_vertex is None:
            return None
        member = set(least_component)
        adj = [
            [j for j in row if j in member] if index in member else []
            for index, row in enumerate(edges)
        ]
        return least_vertex, adj

    s = 0
    n = len(edges)
    while s < n:
        result = adjacency_structure_scc(s)
        if result is None:
            break
        s, adjacency = result
        for row in adjacency:
            for vertex in row:
                blocked[vertex] = False
                b_sets[vertex] = {}
        start_vertex = s
        circuit(s)
        s += 1
    return circuits


def _tarjan_scc(adjacency: list[list[int]]) -> list[list[int]]:
    """Iterative Tarjan SCC over an adjacency list (component order is
    irrelevant to find_circuits, which scans for the global least vertex)."""
    n = len(adjacency)
    index_of = [-1] * n
    low = [0] * n
    on_stack = [False] * n
    stack: list[int] = []
    components: list[list[int]] = []
    counter = 0

    for root in range(n):
        if index_of[root] != -1:
            continue
        work: list[tuple[int, int]] = [(root, 0)]
        while work:
            v, edge_index = work[-1]
            if edge_index == 0:
                index_of[v] = counter
                low[v] = counter
                counter += 1
                stack.append(v)
                on_stack[v] = True
            advanced = False
            for next_index in range(edge_index, len(adjacency[v])):
                w = adjacency[v][next_index]
                if index_of[w] == -1:
                    work[-1] = (v, next_index + 1)
                    work.append((w, 0))
                    advanced = True
                    break
                if on_stack[w]:
                    low[v] = min(low[v], index_of[w])
            if advanced:
                continue
            work.pop()
            if low[v] == index_of[v]:
                component: list[int] = []
                while True:
                    w = stack.pop()
                    on_stack[w] = False
                    component.append(w)
                    if w == v:
                        break
                components.append(component)
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[v])
    return components


# =============================================================================
# Causal cycles + loop classification
# =============================================================================


def find_all_cycles(entities: list[Doc], relations: list[Doc]) -> list[list[str]]:
    if not entities or not relations:
        return []
    causal_relations = [r for r in relations if r["relationType"] in _CAUSAL]
    if not causal_relations:
        return []
    causal_ids: set[str] = set()
    for relation in causal_relations:
        causal_ids.add(relation["from"])
        causal_ids.add(relation["to"])
    causal_entities = [e for e in entities if e["id"] in causal_ids]
    if not causal_entities:
        return []
    index_to_uuid = [e["id"] for e in causal_entities]
    uuid_to_index = {uuid: i for i, uuid in enumerate(index_to_uuid)}
    adjacency: list[list[int]] = [[] for _ in causal_entities]
    for relation in causal_relations:
        from_index = uuid_to_index.get(relation["from"])
        to_index = uuid_to_index.get(relation["to"])
        if from_index is not None and to_index is not None:
            adjacency[from_index].append(to_index)
    return [[index_to_uuid[i] for i in cycle] for cycle in find_circuits(adjacency)]


def classify_loop(cycle: list[str], relations: list[Doc]) -> dict[str, Any]:
    if len(cycle) < 3:
        raise ValueError(
            f"Invalid cycle: minimum length is 3 (e.g., [A, B, A]), got {len(cycle)} elements"
        )
    polarity_chain: list[str] = []
    for i in range(len(cycle) - 1):
        relation = next(
            (r for r in relations if r["from"] == cycle[i] and r["to"] == cycle[i + 1]), None
        )
        if relation is None:
            raise ValueError(
                f"Relation not found for edge {cycle[i]} -> {cycle[i + 1]} "
                f"in cycle [{' -> '.join(cycle)}]"
            )
        polarity_chain.append(relation.get("polarity") or "+")
    negative_count = polarity_chain.count("-")
    net_polarity = "+" if negative_count % 2 == 0 else "-"
    return {
        "path": cycle,
        "polarityChain": polarity_chain,
        "netPolarity": net_polarity,
        "classification": "reinforcing" if net_polarity == "+" else "balancing",
        "memberCount": len(cycle) - 1,
    }


def generate_loop_name(loop_analysis: dict[str, Any], entity_map: dict[str, Doc]) -> str:
    member_ids: list[str] = loop_analysis["path"][:-1]
    max_entity_name = 20
    max_total = 60
    names_to_use = min(len(member_ids), 3)
    member_names: list[str] = []
    for member_id in member_ids[:names_to_use]:
        entity = entity_map.get(member_id)
        name = entity["name"] if entity else member_id
        if len(name) > max_entity_name:
            name = name[: max_entity_name - 1] + "…"
        member_names.append(name)
    suffix_label = "Growth" if loop_analysis["classification"] == "reinforcing" else "Balancing"
    suffix = f" {suffix_label} Loop"
    prefix = "-".join(member_names)
    while len(prefix) + len(suffix) > max_total and len(member_names) > 1:
        member_names.pop()
        prefix = "-".join(member_names)
    if len(prefix) + len(suffix) > max_total:
        prefix = prefix[: max_total - len(suffix) - 1] + "…"
    return f"{prefix}{suffix}"


def create_loop_entity(
    loop_analysis: dict[str, Any], entities: list[Doc], store: FalkorGraphStore
) -> dict[str, Any]:
    """Persist a loop entity + part_of member relations (store-direct — no
    ops-layer revision fields)."""
    from theloom.model import EntityCreate, RelationCreate

    entity_map = {e["id"]: e for e in entities}
    member_ids: list[str] = loop_analysis["path"][:-1]
    observations = [
        f"classification: {loop_analysis['classification']}",
        f"net_polarity: {loop_analysis['netPolarity']}",
        f"member_ids: {', '.join(member_ids)}",
        f"path: {' -> '.join(loop_analysis['path'])}",
        f"polarity_chain: {', '.join(loop_analysis['polarityChain'])}",
        f"detected_at: {iso_now()}",
    ]
    loop_entity = store.create_entity(
        EntityCreate.model_validate(
            {
                "name": generate_loop_name(loop_analysis, entity_map),
                "entityType": "loop",
                "observations": observations,
            }
        )
    )
    relations = [
        store.create_relation(
            RelationCreate.model_validate(
                {
                    "from": member_id,
                    "to": loop_entity.id,
                    "relationType": "part_of",
                    "polarity": None,
                    "strength": "strong",
                    "evidence": "Detected via cycle analysis",
                }
            )
        )
        for member_id in member_ids
    ]
    return {"loopEntity": loop_entity, "relations": relations}


def detect_loops(
    entities: list[Doc],
    relations: list[Doc],
    store: FalkorGraphStore | None,
    min_size: int | None = None,
    max_size: int | None = None,
    persist: bool = False,
) -> dict[str, Any]:
    cycles = find_all_cycles(entities, relations)
    analyses = [classify_loop(cycle, relations) for cycle in cycles]
    if min_size is not None:
        analyses = [a for a in analyses if a["memberCount"] >= min_size]
    if max_size is not None:
        analyses = [a for a in analyses if a["memberCount"] <= max_size]

    entity_map = {e["id"]: e for e in entities}
    persisted: list[dict[str, Any]] = []
    if persist and store is not None:
        persisted = [create_loop_entity(a, entities, store) for a in analyses]

    loops = [
        {
            "id": persisted[i]["loopEntity"].id if persist and i < len(persisted) else None,
            "name": generate_loop_name(analysis, entity_map),
            "classification": analysis["classification"],
            "netPolarity": analysis["netPolarity"],
            "memberCount": analysis["memberCount"],
            "path": analysis["path"],
            "memberIds": analysis["path"][:-1],
            "persisted": persist,
        }
        for i, analysis in enumerate(analyses)
    ]
    return {
        "loopCount": len(analyses),
        "reinforcingCount": sum(1 for a in analyses if a["classification"] == "reinforcing"),
        "balancingCount": sum(1 for a in analyses if a["classification"] == "balancing"),
        "loops": loops,
    }
