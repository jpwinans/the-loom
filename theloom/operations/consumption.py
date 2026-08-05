"""Consumption commands — one-call comprehension answers over a code graph.

Three commands, all name-addressable through the shared resolver
(:func:`theloom.operations.common.resolve_entity_ref`) and all reading through
server-side filtered store calls:

- ``explore`` — everything an agent needs about one symbol: what it is, where
  it is defined, who calls it, what it calls, what it imports and is imported
  by, what it contains / belongs to, its inheritance, and the semantic-layer
  claims/patterns/tensions attached to it *or to its file*.
- ``find-callers`` / ``find-callees`` — the ranked call list on its own, each
  row anchored at the call site parsed out of the typed ``calls`` evidence.
- ``blast-radius`` — reverse reachability over a curated dependency allowlist
  (``calls`` / ``requires`` / ``instance_of`` — a ``related_to`` mention is not
  a dependency), seeded with the symbol's ``part_of`` members so callers bound
  to a method are reached from the class, capped by depth, and refusing to
  expand *through* hub nodes (degree above the 99th percentile) — which hubs
  were suppressed is part of the answer, not a silent omission.

**Honesty under budget pressure** is the contract, and it is what the shapes
here are built around:

1. A budget cut never emits one section whole and then runs out: rows are taken
   round-robin across sections, so breadth degrades evenly and every populated
   section stays represented. Every section keeps at least its first row no
   matter how small the budget is — a section that exists must be visible.
2. The queried entity's own row is never cut. The budget governs the
   neighbourhood, not the answer to "what is this".
3. Rows that do not fit are never dropped silently: the remainder is rolled up
   grouped-by-file (``byFile``: ``theloom/operations/bulk.py: 12``), so the
   count and the location survive even when the names do not.
4. The ``truncation`` block states what was cut (per section) and how to widen
   it — ``shown + sum(cut) == total`` always. ``applied`` means "this answer is
   incomplete", which includes blast-radius withholding a hub's subtree: those
   rows are in neither count, so the flag is the only thing that can say so.
5. Neighbourhood reads are reads of *current* state. Hydration by id has no
   status filter of its own, so these commands apply one: an entity that has
   been superseded or retracted is gone from every list here, exactly as it is
   gone from ``list-entities`` and from the name resolver.

Output is compact JSON with short strings, never prose blobs: the CLI's
JSON-in/JSON-out contract holds.
"""

from __future__ import annotations

import json
import math
import posixpath
import re
from collections import defaultdict
from typing import Any

from pydantic import Field

from theloom.errors import NotFoundError
from theloom.model import Relation, RelationFilter
from theloom.operations.common import CommandInput, UuidStr, resolve_entity_ref
from theloom.operations.entity import compact_entity_doc
from theloom.store.falkor import FalkorGraphStore
from theloom.store.multigraph import MultiGraph

# -- budget ---------------------------------------------------------------------

DEFAULT_BUDGET_TOKENS = 2000
CHARS_PER_TOKEN = 4
MAX_ROLLUP_FILES = 12

# -- call lists -----------------------------------------------------------------

DEFAULT_CALL_LIMIT = 30

# -- blast radius ---------------------------------------------------------------

#: The curated dependency allowlist: an edge type here means "the source
#: depends on the target", so reversing it answers "who breaks if this changes".
#: related_to is deliberately absent — a mention is not a dependency.
BLAST_RELATION_TYPES = ("calls", "requires", "instance_of")
DEFAULT_BLAST_DEPTH = 4
DEFAULT_BLAST_LIMIT = 100
DEFAULT_HUB_PERCENTILE = 99.0
#: Percentiles are meaningless on a handful of nodes; a hub must also be a hub
#: in absolute terms before expansion through it is refused.
MIN_HUB_DEGREE = 8

UNKNOWN_MODULE = "(unknown)"
UNKNOWN_FILE = "(unknown)"
ANCHOR_MAX_CHARS = 200

#: Semantic-layer entity types (the map-codebase enrichment vocabulary). A
#: ``concept`` is ALSO how a class is typed, so concepts qualify only via the
#: explicit ``map_layer: semantic`` observation.
SEMANTIC_ENTITY_TYPES = frozenset(
    {"claim", "pattern", "tension", "insight", "convergence", "question", "hypothesis"}
)
SEMANTIC_LAYER_TAG = "map_layer: semantic"
#: Observation prefixes that carry the substance of a semantic entity, best
#: first — the anchor line quotes one of these rather than bookkeeping tags.
SEMANTIC_ANCHOR_PREFIXES = ("statement:", "purpose:", "description:", "pole_a:", "risk:")
_BOOKKEEPING_PREFIXES = ("map_layer:", "module_group:")

SECTION_KEYS = (
    "callersIn",
    "callsOut",
    "imports",
    "importedBy",
    "contains",
    "partOf",
    "inheritance",
    "semantic",
)

#: ``<caller> calls <callee> at <file>:<line>`` — the fixed evidence form the
#: codebase extractor writes for every call edge.
_CALL_SITE_RE = re.compile(r"\bat\s+(?P<site>\S+:\d+)\s*$")
_FILE_PATH_PREFIX = "file path:"
_LINE_RANGE_PREFIX = "line range:"


# =============================================================================
# Input models
# =============================================================================


class ExploreInput(CommandInput):
    """Addressed by ``entityId`` or by ``name`` — exactly one."""

    entity_id: UuidStr | None = Field(default=None, alias="entityId")
    name: str | None = None
    budget: int | None = Field(default=None, ge=1)
    graph: str | None = None


class FindCallsInput(CommandInput):
    """Addressed by ``entityId`` or by ``name`` — exactly one."""

    entity_id: UuidStr | None = Field(default=None, alias="entityId")
    name: str | None = None
    limit: int | None = Field(default=None, ge=1)
    graph: str | None = None


class BlastRadiusInput(CommandInput):
    """Addressed by ``entityId`` or by ``name`` — exactly one."""

    entity_id: UuidStr | None = Field(default=None, alias="entityId")
    name: str | None = None
    depth: int | None = Field(default=None, ge=1, le=10)
    limit: int | None = Field(default=None, ge=1)
    hub_percentile: float | None = Field(default=None, ge=50, le=100, alias="hubPercentile")
    graph: str | None = None


# =============================================================================
# Doc helpers
# =============================================================================


def _observation(doc: dict[str, Any], prefix: str) -> str | None:
    for observation in doc.get("observations") or []:
        text = str(observation)
        if text.lower().startswith(prefix):
            return text[len(prefix) :].strip()
    return None


def _file_of(doc: dict[str, Any]) -> str | None:
    return _observation(doc, _FILE_PATH_PREFIX)


def _definition(doc: dict[str, Any]) -> str | None:
    """``file:lines`` — where this symbol is written, or None off-code."""
    path = _file_of(doc)
    if path is None:
        return None
    lines = _observation(doc, _LINE_RANGE_PREFIX)
    return f"{path}:{lines}" if lines else path


def _module_of(doc: dict[str, Any]) -> str:
    """The module an entity belongs to: the directory of its file. The first
    path segment alone is one bucket for a single-package repo, so the
    containing package is the grouping that actually separates impact."""
    path = _file_of(doc)
    if not path:
        return UNKNOWN_MODULE
    parent = posixpath.dirname(path.replace("\\", "/"))
    return parent or path


def _call_site(evidence: str | None) -> str | None:
    if not evidence:
        return None
    match = _CALL_SITE_RE.search(evidence)
    return match.group("site") if match else None


def _is_semantic(doc: dict[str, Any]) -> bool:
    if doc.get("entityType") in SEMANTIC_ENTITY_TYPES:
        return True
    return any(str(o).lower().startswith(SEMANTIC_LAYER_TAG) for o in doc.get("observations") or [])


def _truncate(text: str) -> str:
    if len(text) > ANCHOR_MAX_CHARS:
        return text[:ANCHOR_MAX_CHARS].rstrip() + "…"
    return text


def _semantic_anchor(doc: dict[str, Any]) -> str | None:
    """One line of substance, not bookkeeping — enough to decide whether to
    read the entity itself."""
    observations = [str(o) for o in doc.get("observations") or []]
    for prefix in SEMANTIC_ANCHOR_PREFIXES:
        for text in observations:
            if text.lower().startswith(prefix):
                return _truncate(text)
    for text in observations:
        if not text.lower().startswith(_BOOKKEEPING_PREFIXES):
            return _truncate(text)
    return None


def _row(doc: dict[str, Any], *, at: str | None = None, kind: str | None = None) -> dict[str, Any]:
    """One neighbour line: who it is, where it lives, and — for a call edge —
    the call site it was reached through."""
    row: dict[str, Any] = {"name": doc["name"], "entityType": doc["entityType"]}
    if kind is not None:
        row["kind"] = kind
    if at is not None:
        row["at"] = at
    file_path = _file_of(doc)
    if file_path is not None:
        row["file"] = file_path
    return row


def _cost(value: Any) -> int:
    return len(json.dumps(value, separators=(",", ":")))


def _rollup(rows: list[dict[str, Any]], key: str = "file") -> list[dict[str, Any]]:
    """Grouped counts for rows that did not fit — the count and the place
    survive even when the names do not. Code rows group by file; semantic-layer
    rows have no file, so they group by entity type instead."""
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        counts[str(row.get(key) or UNKNOWN_FILE)] += 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    if len(ordered) <= MAX_ROLLUP_FILES:
        return [{key: name, "count": count} for name, count in ordered]
    head = ordered[:MAX_ROLLUP_FILES]
    rest = sum(count for _, count in ordered[MAX_ROLLUP_FILES:])
    entries = [{key: name, "count": count} for name, count in head]
    entries.append({key: f"({len(ordered) - MAX_ROLLUP_FILES} more)", "count": rest})
    return entries


def _section_rollup(key: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Semantic-layer entities live outside any file, so they roll up by type;
    everything else rolls up by file."""
    return _rollup(rows, "entityType") if key == "semantic" else _rollup(rows)


def _is_active(doc: dict[str, Any] | None) -> bool:
    """Whether a doc is part of *current* state.

    Updates invalidate, they never overwrite: a superseded / retracted /
    deprecated / investigating entity has left the live projection, and its
    edges are not deleted when it goes. Every list read in the repo defaults to
    ``status=active`` (``EntityFilter.status_filter``); the neighbourhood reads
    here hydrate by id, which has no such filter, so they apply it themselves.
    An absent doc is an entity that is gone — also not current. Unset means
    active (``Entity.effective_status``).
    """
    return doc is not None and str(doc.get("status") or "active") == "active"


def _active_docs(docs: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {entity_id: doc for entity_id, doc in docs.items() if _is_active(doc)}


def _unique_relations(relations: list[Relation]) -> list[Relation]:
    """A ``both``-direction read runs the incoming and outgoing patterns as two
    queries and concatenates them, so a self-edge — direct recursion, which the
    extractor emits — comes back twice. One edge is one relation."""
    seen: set[str] = set()
    unique: list[Relation] = []
    for relation in relations:
        if relation.id in seen:
            continue
        seen.add(relation.id)
        unique.append(relation)
    return unique


def _entity_doc(store: FalkorGraphStore, entity_id: str) -> dict[str, Any]:
    doc = store.read_entity_doc(entity_id)
    if doc is None:
        raise NotFoundError(
            f"Entity not found with ID: {entity_id}. Use list-entities to see available entities."
        )
    return doc


# =============================================================================
# explore
# =============================================================================


def _allocate(sections: dict[str, list[dict[str, Any]]], available: int) -> dict[str, int]:
    """How many rows of each section fit the budget.

    Round-robin, one row per section per pass, so a wide section can never eat
    the budget before a narrow one is reached. The first row of every populated
    section is unconditional: a section that exists must be visible, whatever
    the budget says.
    """
    counts = {key: (1 if rows else 0) for key, rows in sections.items()}
    spent = sum(_cost(rows[0]) for rows in sections.values() if rows)
    blocked: set[str] = set()
    while True:
        progressed = False
        for key, rows in sections.items():
            if key in blocked or len(rows) <= counts[key]:
                continue
            cost = _cost(rows[counts[key]])
            if spent + cost > available:
                blocked.add(key)  # this row is too big; smaller rows elsewhere may still fit
                continue
            counts[key] += 1
            spent += cost
            progressed = True
        if not progressed:
            return counts


def _semantic_rows(
    store: FalkorGraphStore, entity_id: str, docs: dict[str, dict[str, Any]], file_id: str | None
) -> list[dict[str, Any]]:
    """Semantic-layer entities attached to the entity or to its file. The
    enrichment layer grounds claims/patterns/tensions on the FILE, so a symbol
    that ignored its file's semantics would report none."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def collect(neighbour_ids: list[str], neighbour_docs: dict[str, dict[str, Any]]) -> None:
        for neighbour_id in neighbour_ids:
            doc = neighbour_docs.get(neighbour_id)
            if doc is None or neighbour_id in seen or neighbour_id == entity_id:
                continue
            if not _is_semantic(doc):
                continue
            seen.add(neighbour_id)
            row: dict[str, Any] = {"name": doc["name"], "entityType": doc["entityType"]}
            anchor = _semantic_anchor(doc)
            if anchor is not None:
                row["anchor"] = anchor
            rows.append(row)

    collect(list(docs), docs)
    if file_id is not None and file_id != entity_id:
        file_relations = _unique_relations(store.get_relations(file_id, "both", None))
        file_neighbour_ids = [
            relation.to if relation.from_ == file_id else relation.from_
            for relation in file_relations
        ]
        file_docs = _active_docs(store.read_entity_docs(file_neighbour_ids))
        collect(file_neighbour_ids, file_docs)
    return rows


def explore(params: ExploreInput, multi: MultiGraph) -> dict[str, Any]:
    store = multi.get_store(params.graph)
    entity_id = resolve_entity_ref(
        store, entity_id=params.entity_id, name=params.name, id_field="entityId"
    )
    doc = _entity_doc(store, entity_id)
    budget = params.budget if params.budget is not None else DEFAULT_BUDGET_TOKENS

    relations = _unique_relations(store.get_relations(entity_id, "both", None))
    neighbour_ids = [
        relation.to if relation.from_ == entity_id else relation.from_ for relation in relations
    ]
    neighbour_docs = _active_docs(store.read_entity_docs(neighbour_ids))

    sections: dict[str, list[dict[str, Any]]] = {key: [] for key in SECTION_KEYS}
    file_id: str | None = None
    for relation in relations:
        relation_type = relation.relation_type.value
        # A self-edge is genuinely both directions — direct recursion calls and
        # is called — so it is filed under each, once. Every other edge matches
        # exactly one arm.
        for outgoing in (True, False):
            if (relation.from_ if outgoing else relation.to) != entity_id:
                continue
            other_id = relation.to if outgoing else relation.from_
            other = neighbour_docs.get(other_id)
            if other is None:
                continue
            if relation_type == "calls":
                key = "callsOut" if outgoing else "callersIn"
                sections[key].append(_row(other, at=_call_site(relation.evidence)))
            elif relation_type == "requires":
                sections["imports" if outgoing else "importedBy"].append(_row(other))
            elif relation_type == "part_of":
                if outgoing:
                    sections["partOf"].append(_row(other))
                    if file_id is None and str(other["name"]).startswith("file:"):
                        file_id = other_id
                else:
                    sections["contains"].append(_row(other))
            elif relation_type == "instance_of":
                kind = "extends" if outgoing else "extendedBy"
                sections["inheritance"].append(_row(other, kind=kind))

    sections["semantic"] = _semantic_rows(store, entity_id, neighbour_docs, file_id)

    # The same agent-shaped projection read-entity/list-entities emit under
    # "compact" — one entity shape across the surface, not a private variant.
    head: dict[str, Any] = {"entity": compact_entity_doc(doc), "definition": _definition(doc)}
    # Everything that is not a neighbour row — the entity, the section
    # skeleton, the truncation block and a reserve for the rollups — is charged
    # against the budget FIRST: those parts are not negotiable, so the rows are
    # what the budget actually governs.
    available = budget * CHARS_PER_TOKEN - _overhead(head, sections, budget)
    counts = _allocate(sections, max(available, 0))

    total = sum(len(rows) for rows in sections.values())
    shown = sum(counts.values())
    cut: dict[str, int] = {}
    result: dict[str, Any] = dict(head)
    for key in SECTION_KEYS:
        rows = sections[key]
        kept = rows[: counts[key]]
        dropped = rows[counts[key] :]
        section: dict[str, Any] = {"total": len(rows), "shown": kept}
        if dropped:
            cut[key] = len(dropped)
            section["byType" if key == "semantic" else "byFile"] = _section_rollup(key, dropped)
        result[key] = section

    result["truncation"] = {
        "applied": bool(cut),
        "shown": shown,
        "total": total,
        "cut": cut,
        "hint": _explore_hint(budget, total - shown),
    }
    # The floor — the entity plus one row per populated section — is not
    # negotiable, so a budget below it is reported rather than obeyed.
    spent_tokens = _cost(result) // CHARS_PER_TOKEN
    if spent_tokens > budget:
        result["truncation"]["hint"] += (
            f" This answer is ~{spent_tokens} tokens: the entity plus one row per section is the "
            "smallest honest answer and it does not fit the budget."
        )
    return result


def _overhead(head: dict[str, Any], sections: dict[str, list[dict[str, Any]]], budget: int) -> int:
    """Cost of the answer with every neighbour row removed: the entity, the
    definition, the per-section skeletons, the truncation block, and one
    rollup-entry reserve per section that could be cut."""
    skeleton: dict[str, Any] = dict(head)
    for key, rows in sections.items():
        skeleton[key] = {"total": len(rows), "shown": []}
    skeleton["truncation"] = {
        "applied": True,
        "shown": 0,
        "total": 0,
        "cut": {key: 0 for key in SECTION_KEYS},
        "hint": _explore_hint(budget, 1),
    }
    # Reserve the worst-case rollup for every section that can be cut: rolling
    # up fewer rows can only produce fewer groups, never more, so this is an
    # upper bound and the rollups can never overrun the budget behind our back.
    reserve = sum(
        _cost(_section_rollup(key, rows)) for key, rows in sections.items() if len(rows) > 1
    )
    return _cost(skeleton) + reserve


def _explore_hint(budget: int, dropped: int) -> str:
    if not dropped:
        return "Nothing was cut."
    return (
        f'{dropped} row(s) did not fit the budget of {budget} tokens. Raise "budget", or call '
        "find-callers / find-callees / blast-radius for the full list."
    )


# =============================================================================
# find-callers / find-callees
# =============================================================================


def _call_rows(store: FalkorGraphStore, entity_id: str, *, incoming: bool) -> list[dict[str, Any]]:
    relations = store.get_relations(entity_id, "incoming" if incoming else "outgoing", "calls")
    other_ids = [relation.from_ if incoming else relation.to for relation in relations]
    docs = _active_docs(store.read_entity_docs(other_ids))
    rows: list[dict[str, Any]] = []
    for relation in relations:
        other_id = relation.from_ if incoming else relation.to
        doc = docs.get(other_id)
        if doc is None:
            continue
        rows.append(_row(doc, at=_call_site(relation.evidence)))
    # Ranked by call site: same file together, in line order — the reading
    # order, and stable across runs.
    return sorted(rows, key=_call_sort_key)


def _call_sort_key(row: dict[str, Any]) -> tuple[str, int, str]:
    at = str(row.get("at") or "")
    line = 0
    if ":" in at:
        _, _, tail = at.rpartition(":")
        if tail.isdigit():
            line = int(tail)
    return (str(row.get("file") or ""), line, str(row["name"]))


def _find_calls(params: FindCallsInput, multi: MultiGraph, *, incoming: bool) -> dict[str, Any]:
    store = multi.get_store(params.graph)
    entity_id = resolve_entity_ref(
        store, entity_id=params.entity_id, name=params.name, id_field="entityId"
    )
    doc = _entity_doc(store, entity_id)
    limit = params.limit if params.limit is not None else DEFAULT_CALL_LIMIT

    rows = _call_rows(store, entity_id, incoming=incoming)
    shown = rows[:limit]
    dropped = rows[limit:]
    result: dict[str, Any] = {
        "entity": {"id": doc["id"], "name": doc["name"], "entityType": doc["entityType"]},
        "callers" if incoming else "callees": shown,
    }
    if dropped:
        result["byFile"] = _rollup(dropped)
    result["truncation"] = {
        "applied": bool(dropped),
        "shown": len(shown),
        "total": len(rows),
        "hint": (
            f'{len(dropped)} row(s) past the limit of {limit} are rolled up in "byFile". '
            'Raise "limit" to list them.'
            if dropped
            else "Nothing was cut."
        ),
    }
    return result


def find_callers(params: FindCallsInput, multi: MultiGraph) -> dict[str, Any]:
    return _find_calls(params, multi, incoming=True)


def find_callees(params: FindCallsInput, multi: MultiGraph) -> dict[str, Any]:
    return _find_calls(params, multi, incoming=False)


# =============================================================================
# blast-radius
# =============================================================================


def _dependency_index(
    store: FalkorGraphStore,
) -> tuple[dict[str, list[str]], dict[str, int]]:
    """``target -> dependants`` over the allowlist, plus each node's degree in
    that same edge set. One server-side filtered query per relation type."""
    dependants: dict[str, list[str]] = defaultdict(list)
    degree: dict[str, int] = defaultdict(int)
    for relation_type in BLAST_RELATION_TYPES:
        relation_filter = RelationFilter.model_validate({"relationType": relation_type})
        for relation in store.list_relations(relation_filter):
            dependants[relation.to].append(relation.from_)
            degree[relation.to] += 1
            degree[relation.from_] += 1
    return dependants, degree


def _members_of(store: FalkorGraphStore, seed_id: str) -> list[str]:
    """The seed's members, transitively, via part_of — one query, then a walk
    over the projection. A change to a class is a change to its methods, so
    callers bound to a method are inside the radius of the class; the members
    themselves are seeds (reported as ``seededMembers``), not fallout."""
    members: dict[str, list[str]] = defaultdict(list)
    relation_filter = RelationFilter.model_validate({"relationType": "part_of"})
    for relation in store.list_relations(relation_filter):
        members[relation.to].append(relation.from_)
    seeded: list[str] = []
    seen = {seed_id}
    frontier = [seed_id]
    while frontier:
        current = frontier.pop(0)
        for member in members.get(current, []):
            if member in seen:
                continue
            seen.add(member)
            seeded.append(member)
            frontier.append(member)
    return seeded


def _degree_threshold(degrees: list[int], percentile: float) -> int:
    if not degrees:
        return 0
    ordered = sorted(degrees)
    index = int(math.floor(percentile / 100 * (len(ordered) - 1)))
    return ordered[index]


def _spread(groups: list[list[dict[str, Any]]], limit: int) -> list[list[dict[str, Any]]]:
    """Take up to ``limit`` rows round-robin across groups, so a cut narrows
    every module a little instead of erasing the last ones entirely."""
    taken: list[list[dict[str, Any]]] = [[] for _ in groups]
    remaining = limit
    index = 0
    while remaining > 0:
        progressed = False
        for position, group in enumerate(groups):
            if remaining == 0:
                break
            if len(group) > index:
                taken[position].append(group[index])
                remaining -= 1
                progressed = True
        if not progressed:
            break
        index += 1
    return taken


def blast_radius(params: BlastRadiusInput, multi: MultiGraph) -> dict[str, Any]:
    store = multi.get_store(params.graph)
    seed_id = resolve_entity_ref(
        store, entity_id=params.entity_id, name=params.name, id_field="entityId"
    )
    seed_doc = _entity_doc(store, seed_id)
    depth = params.depth if params.depth is not None else DEFAULT_BLAST_DEPTH
    limit = params.limit if params.limit is not None else DEFAULT_BLAST_LIMIT
    percentile = (
        params.hub_percentile if params.hub_percentile is not None else DEFAULT_HUB_PERCENTILE
    )

    dependants, degree = _dependency_index(store)
    docs: dict[str, dict[str, Any]] = {seed_id: seed_doc}
    hydrated: set[str] = {seed_id}

    def hydrate(entity_ids: list[str]) -> None:
        unknown = [entity_id for entity_id in entity_ids if entity_id not in hydrated]
        if unknown:
            hydrated.update(unknown)
            docs.update(store.read_entity_docs(unknown))

    # Retired state is not current state, and retiring an entity does not delete
    # its edges — so a superseded member, dependant or hub is neither fallout
    # nor a route to it, and is dropped before it can be reported as either.
    candidate_members = _members_of(store, seed_id)
    hydrate(candidate_members)
    members = [member for member in candidate_members if _is_active(docs.get(member))]
    seeds = {seed_id, *members}
    threshold = _degree_threshold(list(degree.values()), percentile)

    # The seed and its members ARE the change, not its fallout: they seed the
    # traversal (so a caller bound to a method is reached from the class) but
    # they are reported as ``seededMembers``, never padded into ``affected``.
    affected: dict[str, int] = {}
    suppressed: dict[str, int] = {}
    visited = set(seeds)
    frontier = sorted(seeds)
    for hop in range(depth):
        candidates: list[str] = []
        for node in frontier:
            node_degree = degree.get(node, 0)
            if node not in seeds and node_degree > threshold and node_degree >= MIN_HUB_DEGREE:
                suppressed[node] = node_degree  # affected, but not a route to everything
                continue
            for dependant in dependants.get(node, []):
                if dependant in visited:
                    continue
                visited.add(dependant)
                candidates.append(dependant)
        hydrate(candidates)
        next_frontier = [node for node in candidates if _is_active(docs.get(node))]
        for node in next_frontier:
            affected[node] = hop + 1
        frontier = next_frontier
        if not frontier:
            break

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entity_id, hops in affected.items():
        doc = docs[entity_id]  # only live, hydrated nodes ever enter ``affected``
        grouped[_module_of(doc)].append({"name": doc["name"], "depth": hops})
    for rows in grouped.values():
        rows.sort(key=lambda row: (row["depth"], row["name"]))
    modules = sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0]))
    kept = _spread([rows for _, rows in modules], limit)

    by_module = [
        {"module": module, "count": len(rows), "entities": kept[position]}
        for position, (module, rows) in enumerate(modules)
    ]
    total = len(affected)
    shown = sum(len(rows) for rows in kept)
    hub_rows = [
        {
            "name": docs[hub_id]["name"] if hub_id in docs else hub_id,
            "entityType": docs[hub_id]["entityType"] if hub_id in docs else "unknown",
            "degree": hub_degree,
        }
        for hub_id, hub_degree in sorted(suppressed.items(), key=lambda item: -item[1])
    ]
    return {
        "seed": {
            "id": seed_doc["id"],
            "name": seed_doc["name"],
            "entityType": seed_doc["entityType"],
        },
        "depth": depth,
        "relationTypes": list(BLAST_RELATION_TYPES),
        "seededMembers": len(members),
        "affected": {"total": total, "byModule": by_module},
        "suppressedHubs": hub_rows,
        "truncation": {
            # A suppressed hub withholds its whole dependant subtree, which
            # never enters ``affected`` and so is counted by neither number:
            # the list is incomplete even when every reached row is listed.
            "applied": shown < total or bool(hub_rows),
            "shown": shown,
            "total": total,
            "hint": _blast_hint(total - shown, limit, hub_rows, percentile),
        },
    }


def _blast_hint(dropped: int, limit: int, hubs: list[dict[str, Any]], percentile: float) -> str:
    parts: list[str] = []
    if dropped:
        parts.append(
            f'{dropped} entity name(s) past the limit of {limit} are counted in "byModule" '
            'but not listed. Raise "limit" to list them.'
        )
    if hubs:
        parts.append(
            f"{len(hubs)} hub(s) were not expanded through (degree above the "
            f'{percentile:g}th percentile); see "suppressedHubs".'
        )
    return " ".join(parts) if parts else "Nothing was cut."
