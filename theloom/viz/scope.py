"""Bundle scoping — which slice of the graph goes into the visualization."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from theloom.errors import NotFoundError, ValidationError
from theloom.graph.subgraph import (
    extract_causal_subgraph,
    extract_ego_subgraph,
    extract_typed_subgraph,
)
from theloom.model import EntityFilter, EntityStatus
from theloom.operations.common import CommandInput
from theloom.operations.semantic import _search_similar  # shared search internal
from theloom.store.falkor import FalkorGraphStore

Doc = dict[str, Any]

_MODES = ("full", "ego", "causal", "typed", "search")
_SEARCH_LIMIT = 25  # top-k entities the search scope keeps
# A relevance floor, not find_clusters' near-duplicate bar (0.7): that threshold is
# calibrated for comparing one entity's full name+observations text against another's
# (find_clusters embeds each entity's own text as the "query"), which runs consistently
# hot. A short, natural user query embedded the same way scores systematically lower
# against a long stored document vector — verified against the live embedder on the
# tapestry-dev fixture, realistic queries such as "resource scarcity suppresses
# population growth rate" or "training neural networks with gradient descent" score
# their true topical matches in the 0.55-0.69 range with a clear gap down to ~0.48 for
# unrelated entities, while 0.7 requires near-verbatim text and returns nothing. 0.5
# sits in that gap: it excludes orthogonal vectors while accepting paraphrase-level
# matches, so a search scope keeps genuine matches rather than every stored vector
# ranked with no cutoff.
_SEARCH_MIN_SCORE = 0.5

# store.list_entities(None) defaults to active-only (mirrors list-entities'
# opt-in include_deprecated/etc. flags). The visualization bundle ships every
# status so the Explorer's client-side status filter and Chronicle's replay
# have non-active entities to show — status is a display concern here, not an
# access-control one.
_ALL_STATUSES = EntityFilter(statusFilter=list(EntityStatus))


class ScopeInput(CommandInput):
    mode: str = "full"
    center: str | None = None
    depth: int = Field(default=1, ge=1, le=5)
    entity_type: str | None = Field(default=None, alias="entityType")
    relation_type: str | None = Field(default=None, alias="relationType")
    query: str | None = None


def _docs(store: FalkorGraphStore, as_of: str | None = None) -> tuple[list[Doc], list[Doc]]:
    """The graph's entity and relation wire docs — now, or as of a bound.

    A bounded read is one store call: ``read_graph_as_of`` owns the whole
    bi-temporal reconstruction, including the closed ``:_RelationVersion``
    intervals that no reader outside the store can see. This module used to
    approximate it from live reads (one ``read_entity_as_of`` per listed
    entity, then relations filtered on ``created_at`` here) — which cost an
    N+1 and still could not resurrect an edge retired since the bound.
    """
    if as_of is not None:
        entities, relations = store.read_graph_as_of(as_of)
    else:
        entities = store.list_entities(_ALL_STATUSES)
        relations = store.list_relations()
    return (
        [e.model_dump(by_alias=True, exclude_unset=True) for e in entities],
        [r.model_dump(by_alias=True, exclude_unset=True) for r in relations],
    )


def resolve_scope(
    scope: ScopeInput, store: FalkorGraphStore, as_of: str | None = None
) -> tuple[list[Doc], list[Doc], str]:
    if scope.mode not in _MODES:
        raise ValidationError(
            f"Invalid scope mode: '{scope.mode}'. Must be one of: {', '.join(_MODES)}"
        )
    entities, relations = _docs(store, as_of)
    if scope.mode == "search":
        if scope.query is None or not scope.query.strip():
            raise ValidationError("Scope mode 'search' requires a non-empty 'query'.")
        entity_types = [scope.entity_type] if scope.entity_type else None
        matched = {
            result["id"]
            for result in _search_similar(
                store,
                scope.query,
                limit=_SEARCH_LIMIT,
                min_score=_SEARCH_MIN_SCORE,
                entity_types=entity_types,
            )
        }
        search_entities = [e for e in entities if e["id"] in matched]
        matched_ids = {e["id"] for e in search_entities}
        search_relations = [
            r for r in relations if r["from"] in matched_ids and r["to"] in matched_ids
        ]
        return search_entities, search_relations, f"search:{scope.query}"
    if scope.mode == "full":
        return entities, relations, "full"
    if scope.mode == "causal":
        causal_entities, causal_relations = extract_causal_subgraph(entities, relations)
        return causal_entities, causal_relations, "causal"
    if scope.mode == "ego":
        if scope.center is None:
            raise ValidationError("Scope mode 'ego' requires 'center' (an entity id).")
        result = extract_ego_subgraph(entities, relations, scope.center, depth=scope.depth)
        if result is None:
            raise NotFoundError(f"Entity not found with ID: {scope.center}")
        ego_entities, ego_relations = result
        return ego_entities, ego_relations, f"ego:{scope.center}:d{scope.depth}"
    typed_entities, typed_relations = extract_typed_subgraph(
        entities, relations, scope.entity_type, scope.relation_type
    )
    label = f"typed:{scope.entity_type or '*'}/{scope.relation_type or '*'}"
    return typed_entities, typed_relations, label
