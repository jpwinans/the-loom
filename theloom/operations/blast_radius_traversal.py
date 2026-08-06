"""blast-radius's traversal, as one testable unit: the reverse-reachability
walk over a curated dependency allowlist, the hub-suppression rule, and
grouping the result by module — independent of how ``blast_radius`` (the
command) turns that answer into a wire response (limit-spreading, the
truncation block, row shapes).

**Store-read shape.** Two different needs, two different reads:

- The hub rule compares a node's degree against a percentile threshold
  computed over the WHOLE graph's degree distribution in the allowlisted
  relation types — that is a global aggregate by definition, and there is no
  cheaper store primitive that yields it than one server-side-filtered full
  pass per relation type (:func:`_dependency_index`). That same pass also
  builds the reverse ``target -> dependants`` index the walk needs, so the
  walk itself costs nothing extra on top of the read the hub rule already
  requires.
- Seeding via ``part_of`` (:func:`_members_of`) has no such global
  requirement — it is a bounded subtree walk from one seed — so it uses
  ``get_relations`` per node visited instead of loading every ``part_of``
  edge in the graph. In a codebase graph, ``part_of`` is one of the densest
  relation types (every symbol has one to its file); loading it whole to
  answer "what is inside this one class" would be paying for the entire
  codebase to answer a question about one seed.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from theloom.model import RelationFilter
from theloom.operations.consumption_budget import percentile_threshold
from theloom.store.falkor import FalkorGraphStore


@dataclass(frozen=True)
class Traversal:
    """The answer to "what depends on this, transitively, and what did we
    refuse to walk through" — everything ``blast_radius`` needs to build its
    wire response, independent of limit/reporting concerns.

    ``members`` — the seed's transitive ``part_of`` members (reported as
    ``seededMembers``, never padded into ``affected``: they ARE the change,
    not its fallout).
    ``affected`` — entity id -> hop depth (1-based) reached through the
    allowlist, excluding the seed and its members.
    ``suppressed`` — entity id -> degree, for hubs refused expansion through;
    their dependants never enter ``affected``.
    ``docs`` — every doc read along the way (seed, members, affected,
    suppressed), for callers that need to render names/types/files.
    """

    members: list[str]
    affected: dict[str, int]
    suppressed: dict[str, int]
    docs: dict[str, dict[str, Any]] = field(default_factory=dict)


def _dependency_index(
    store: FalkorGraphStore, relation_types: Sequence[str]
) -> tuple[dict[str, list[str]], dict[str, int]]:
    """``target -> dependants`` over the allowlist, plus each node's degree in
    that same edge set. One server-side filtered query per relation type —
    see the module docstring for why this stays a full pass."""
    dependants: dict[str, list[str]] = defaultdict(list)
    degree: dict[str, int] = defaultdict(int)
    for relation_type in relation_types:
        relation_filter = RelationFilter.model_validate({"relationType": relation_type})
        for relation in store.list_relations(relation_filter):
            dependants[relation.to].append(relation.from_)
            degree[relation.to] += 1
            degree[relation.from_] += 1
    return dependants, degree


def _members_of(store: FalkorGraphStore, seed_id: str) -> list[str]:
    """The seed's members, transitively, via ``part_of`` — bounded per-node
    reads during the walk itself, one ``get_relations`` call per node
    actually in the subtree, rather than loading every ``part_of`` edge in
    the graph up front."""
    seeded: list[str] = []
    seen = {seed_id}
    frontier = [seed_id]
    while frontier:
        current = frontier.pop(0)
        for relation in store.get_relations(current, "incoming", "part_of"):
            member = relation.from_
            if member in seen:
                continue
            seen.add(member)
            seeded.append(member)
            frontier.append(member)
    return seeded


def run_traversal(
    store: FalkorGraphStore,
    seed_id: str,
    seed_doc: dict[str, Any],
    *,
    relation_types: Sequence[str],
    depth: int,
    hub_percentile: float,
    min_hub_degree: int,
    is_active: Callable[[dict[str, Any] | None], bool],
) -> Traversal:
    """Reverse-reachability from ``seed_id`` over ``relation_types``, seeded
    with the transitive ``part_of`` members (so a caller bound to a method is
    reached from the class), capped at ``depth`` hops, refusing to expand
    through a node whose degree exceeds both ``min_hub_degree`` and the
    ``hub_percentile`` of the graph's degree distribution.

    Retired state is not current state: an inactive member, dependant or hub
    is dropped by ``is_active`` before it can be reported as any of the
    three — it is neither fallout nor a route to fallout.
    """
    dependants, degree = _dependency_index(store, relation_types)
    docs: dict[str, dict[str, Any]] = {seed_id: seed_doc}
    hydrated: set[str] = {seed_id}

    def hydrate(entity_ids: list[str]) -> None:
        unknown = [entity_id for entity_id in entity_ids if entity_id not in hydrated]
        if unknown:
            hydrated.update(unknown)
            docs.update(store.read_entity_docs(unknown))

    candidate_members = _members_of(store, seed_id)
    hydrate(candidate_members)
    members = [member for member in candidate_members if is_active(docs.get(member))]
    seeds = {seed_id, *members}
    threshold = percentile_threshold(list(degree.values()), hub_percentile)

    affected: dict[str, int] = {}
    suppressed: dict[str, int] = {}
    visited = set(seeds)
    frontier = sorted(seeds)
    for hop in range(depth):
        candidates: list[str] = []
        for node in frontier:
            node_degree = degree.get(node, 0)
            if node not in seeds and node_degree > threshold and node_degree >= min_hub_degree:
                suppressed[node] = node_degree  # affected, but not a route to everything
                continue
            for dependant in dependants.get(node, []):
                if dependant in visited:
                    continue
                visited.add(dependant)
                candidates.append(dependant)
        hydrate(candidates)
        next_frontier = [node for node in candidates if is_active(docs.get(node))]
        for node in next_frontier:
            affected[node] = hop + 1
        frontier = next_frontier
        if not frontier:
            break

    return Traversal(members=members, affected=affected, suppressed=suppressed, docs=docs)


def group_by_module(
    docs: dict[str, dict[str, Any]],
    affected: dict[str, int],
    module_of: Callable[[dict[str, Any]], str],
) -> list[tuple[str, list[dict[str, Any]]]]:
    """``affected`` bucketed by ``module_of(doc)``, each bucket sorted by hop
    depth then name, buckets sorted by size (largest first) then module name.
    Plain data in (docs, hop depths, a module-of function), plain data out —
    no store."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entity_id, hops in affected.items():
        doc = docs[entity_id]  # only live, hydrated nodes ever enter ``affected``
        grouped[module_of(doc)].append({"name": doc["name"], "depth": hops})
    for rows in grouped.values():
        rows.sort(key=lambda row: (row["depth"], row["name"]))
    return sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0]))
