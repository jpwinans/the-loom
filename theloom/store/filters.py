"""Entity/relation filter semantics.

Pure functions over model objects. Composition order for entities (AND):
status (default ['active']) → entityType → name (partial, case-insensitive) →
query (name or observations) → version/minVersion → session. The sourcedFrom /
excludeSourcedFrom filters need edge access and live in the store
(exclude wins when both match).

Session matching: the first-class ``session`` field, with the legacy
``"subgraph: {sid}-{qid}"`` observation tag accepted as a fallback for
entities (exact ``subgraph: {sid}`` or a ``subgraph: {sid}-`` prefix, so one
sid never prefix-matches another). Relations match on the field only.

Note: ``EntityFilter`` declares 3D-memory filters (memoryType, domain,
durability, excludeExpired) but the entity-filter path never implements them —
the store path ignores them. That is intentional; if an operations-layer filter
turns out to be needed, it lands there.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from theloom.model import Entity, EntityFilter, Relation, RelationFilter

# Every status but 'retracted' — the population a name might still usefully
# resolve against (a retracted entity is gone, full stop; the others are
# still real nodes an edge can legitimately point at).
NON_RETRACTED_ENTITY_STATUSES: tuple[str, ...] = (
    "active",
    "superseded",
    "deprecated",
    "investigating",
)


def prefer_active_by_name(entities: Iterable[Entity]) -> dict[str, Entity]:
    """Collapse entities to one per ``name``, the one name->id tie-break used
    everywhere a name is resolved against a non-retracted read: an active
    candidate wins over a superseded/deprecated/investigating one, and the
    first one seen wins a tie between two equally (in)active candidates.
    Shared by bulk import's relation resolution and the incremental-update
    diff planner's, so the two never drift into different answers for the
    same ambiguous name.
    """
    by_name: dict[str, Entity] = {}
    for entity in entities:
        current = by_name.get(entity.name)
        if current is None:
            by_name[entity.name] = entity
            continue
        current_active = current.status is None or current.status == "active"
        candidate_active = entity.status is None or entity.status == "active"
        if not current_active and candidate_active:
            by_name[entity.name] = entity
    return by_name


def matches_session(session: str, record_session: str | None, observations: Sequence[str]) -> bool:
    """True iff a record belongs to the session — via the first-class field or
    the legacy "subgraph: {sid}-{qid}" observation tag."""
    if record_session == session:
        return True
    exact = f"subgraph: {session}"
    prefixed = f"{exact}-"
    return any(str(obs) == exact or str(obs).startswith(prefixed) for obs in observations)


def apply_entity_filters(entities: list[Entity], filter: EntityFilter | None) -> list[Entity]:
    """Status/type/name/query/version filtering, in the documented order and semantics."""
    status_filter = (
        [s.value for s in filter.status_filter]
        if filter is not None and filter.status_filter is not None
        else ["active"]
    )
    result = [e for e in entities if e.effective_status.value in status_filter]
    if filter is None:
        return result

    def matches(entity: Entity) -> bool:
        if filter.entity_type is not None and entity.entity_type != filter.entity_type:
            return False
        if filter.name is not None and filter.name.lower() not in entity.name.lower():
            return False
        if filter.query is not None:
            query = filter.query.lower()
            in_name = query in entity.name.lower()
            in_observations = any(query in str(obs).lower() for obs in entity.observations)
            if not in_name and not in_observations:
                return False
        if filter.version is not None and entity.version != filter.version:
            return False
        if filter.min_version is not None and (entity.version or 0) < filter.min_version:
            return False
        return not (
            filter.session is not None
            and not matches_session(filter.session, entity.session, entity.observations)
        )

    return [e for e in result if matches(e)]


def apply_relation_filters(
    relations: list[Relation], filter: RelationFilter | None
) -> list[Relation]:
    """from/to/relationType/polarity filtering, AND logic."""
    if filter is None:
        return relations

    def matches(relation: Relation) -> bool:
        if filter.from_ is not None and relation.from_ != filter.from_:
            return False
        if filter.to is not None and relation.to != filter.to:
            return False
        if filter.relation_type is not None and relation.relation_type != filter.relation_type:
            return False
        if filter.session is not None and relation.session != filter.session:
            return False
        # pydantic can't distinguish polarity absent vs explicit null; both mean
        # "no polarity filter" — only an explicit polarity value narrows the
        # match.
        return not (filter.polarity is not None and relation.polarity != filter.polarity)

    return [r for r in relations if matches(r)]


def extract_neighbor_ids(entity_id: str, relations: list[Relation], direction: str) -> list[str]:
    """Neighbor ids from relations, deduplicated, first-seen order."""
    seen: dict[str, None] = {}
    for relation in relations:
        if direction == "outgoing":
            seen.setdefault(relation.to)
        elif direction == "incoming":
            seen.setdefault(relation.from_)
        elif relation.from_ == entity_id:
            seen.setdefault(relation.to)
        else:
            seen.setdefault(relation.from_)
    return list(seen)
