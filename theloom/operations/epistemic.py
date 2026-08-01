"""The 17 epistemic queries plus credit propagation.

Over epistemic props: default status universe is
['active','investigating'] (widened to all five by includeAllStatuses),
missing confidence means score 0 for uncertainty but exclusion from certainty,
and stale means "never evaluated OR older than daysOld".

Time-dependent caveat: stale-beliefs' daysSinceEvaluation counts days from NOW,
so tests pin only date-stable shapes (nulls / empty results); the sorted
dated path is unit-tested instead.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import Field

from theloom.errors import NotFoundError, OperationError, ValidationError
from theloom.model import (
    ALL_ENTITY_STATUSES,
    ALL_RELATION_TYPES,
    EntityFilter,
    EntityType,
    RelationFilter,
    confidence_label,
)
from theloom.operations.common import CommandInput, UuidStr
from theloom.store.falkor import FalkorGraphStore
from theloom.store.filters import matches_session
from theloom.store.multigraph import MultiGraph
from theloom.timeutil import iso_now

Doc = dict[str, Any]

MAX_DEPTH_LIMIT = 10
ALL_STATUSES = ["active", "investigating", "superseded", "deprecated", "retracted"]
DEFAULT_ACTIVE_STATUSES = ["active", "investigating"]
POSTMORTEM_HISTORY_KEY = "postmortem_evaluate.history"
MAX_HISTORY_ENTRIES = 100
VITERBI_STRENGTH_MAP = {"foundational": 0.95, "strong": 0.9, "moderate": 0.7, "weak": 0.4}


def _status_filter(include_all: bool | None) -> list[str]:
    return ALL_STATUSES if include_all else DEFAULT_ACTIVE_STATUSES


def _days_since(iso_date: str | None) -> int | None:
    if not iso_date:
        return None
    try:
        parsed = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
    except ValueError:
        return None
    diff = datetime.now(UTC) - parsed
    return int(diff.total_seconds() // 86_400)


def _list(
    store: FalkorGraphStore,
    statuses: list[str],
    entity_type: str | None = None,
    session: str | None = None,
) -> list[Doc]:
    doc: dict[str, Any] = {"statusFilter": statuses}
    if entity_type:
        doc["entityType"] = entity_type
    if session is not None:
        doc["session"] = session
    return [
        e.model_dump(by_alias=True, exclude_unset=True)
        for e in store.list_entities(EntityFilter.model_validate(doc))
    ]


def _read(store: FalkorGraphStore, entity_id: str) -> Doc | None:
    entity = store.read_entity(entity_id)
    return entity.model_dump(by_alias=True, exclude_unset=True) if entity else None


def _relations(
    store: FalkorGraphStore, entity_id: str, direction: str, relation_type: str | None = None
) -> list[Doc]:
    return [
        r.model_dump(by_alias=True, exclude_unset=True)
        for r in store.get_relations(entity_id, direction, relation_type)  # type: ignore[arg-type]
    ]


def _limited(results: list[Any], limit: int | None) -> list[Any]:
    if limit is not None and limit < len(results):
        return results[:limit]
    return results


# =============================================================================
# Input models
# =============================================================================


class EpistemicQueryInput(CommandInput):
    limit: int | None = Field(default=None, ge=1)
    include_all_statuses: bool | None = Field(default=None, alias="includeAllStatuses")
    graph: str | None = None
    session: str | None = None


class UncertainClaimsInput(EpistemicQueryInput):
    threshold: float | None = Field(default=None, ge=0, le=1)
    entity_type: EntityType | None = Field(default=None, alias="entityType")


class NeedsEvidenceInput(EpistemicQueryInput):
    min_supports: int | None = Field(default=None, ge=0, alias="minSupports")
    claim_id: UuidStr | None = Field(default=None, alias="claimId")


class StaleBeliefsInput(EpistemicQueryInput):
    # daysOld must be positive — daysOld: 0 is rejected and classified
    # VALIDATION_ERROR.
    days_old: int | None = Field(default=None, gt=0, alias="daysOld")
    entity_type: EntityType | None = Field(default=None, alias="entityType")


class ProvenanceChainInput(CommandInput):
    entity_id: UuidStr = Field(alias="entityId")
    max_depth: int | None = Field(default=None, ge=1, alias="maxDepth")
    graph: str | None = None
    session: str | None = None


class MostCertainInput(EpistemicQueryInput):
    top_k: int | None = Field(default=None, ge=1, alias="topK")
    entity_type: EntityType | None = Field(default=None, alias="entityType")


class ClaimsFromSourceInput(CommandInput):
    source_id: UuidStr = Field(alias="sourceId")
    limit: int | None = Field(default=None, ge=1)
    graph: str | None = None
    session: str | None = None


class TypedEpistemicInput(EpistemicQueryInput):
    entity_type: EntityType | None = Field(default=None, alias="entityType")


class BlockingQuestionsInput(EpistemicQueryInput):
    domain: str | None = None


class AnsweredQuestionsInput(EpistemicQueryInput):
    since: str | None = None


class SessionChangelogInput(CommandInput):
    since: str | None = None
    session: str | None = None
    graph: str | None = None
    include_relations: bool | None = Field(default=None, alias="includeRelations")
    dry_run: bool | None = Field(default=None, alias="dryRun")


class PostmortemEvaluateInput(CommandInput):
    graph: str | None = None
    dry_run: bool | None = Field(default=None, alias="dryRun")


class PropagateCreditInput(CommandInput):
    # The input schema REQUIRES entityIds; the op-level entityId
    # fallback is unreachable from the CLI.
    entity_ids: list[UuidStr] = Field(alias="entityIds")
    delta: float
    damping_factor: float | None = Field(default=None, alias="dampingFactor")
    max_depth: int | None = Field(default=None, ge=1, alias="maxDepth")
    min_delta: float | None = Field(default=None, ge=0, alias="minDelta")
    dry_run: bool | None = Field(default=None, alias="dryRun")
    relation_types: list[str] | None = Field(default=None, alias="relationTypes")
    propagation_mode: str | None = Field(default=None, alias="propagationMode")
    graph: str | None = None


class CrossSessionContradictionsInput(EpistemicQueryInput):
    entity_type: EntityType | None = Field(default=None, alias="entityType")
    min_confidence: float | None = Field(default=None, ge=0, le=1, alias="minConfidence")
    session_ids: list[str] | None = Field(default=None, alias="sessionIds")
    max_depth: int | None = Field(default=None, ge=1, alias="maxDepth")


# =============================================================================
# The 13 list-style queries
# =============================================================================


def uncertain_claims(params: UncertainClaimsInput, multi: MultiGraph) -> list[Doc]:
    threshold = params.threshold if params.threshold is not None else 0.5
    store = multi.get_store(params.graph)
    entities = _list(
        store,
        _status_filter(params.include_all_statuses),
        params.entity_type.value if params.entity_type else None,
        params.session,
    )
    results = []
    for entity in entities:
        score = (entity.get("confidence") or {}).get("score", 0)
        if score <= threshold:
            results.append(
                {
                    "entity": entity,
                    "confidenceScore": score,
                    "confidenceLabel": confidence_label(score).value,
                }
            )
    results.sort(key=lambda r: r["confidenceScore"])
    return _limited(results, params.limit)


def needs_evidence(params: NeedsEvidenceInput, multi: MultiGraph) -> list[Doc]:
    min_supports = params.min_supports if params.min_supports is not None else 2
    store = multi.get_store(params.graph)
    if params.claim_id:
        entity = _read(store, params.claim_id)
        if entity is None:
            raise NotFoundError(f"Entity not found: {params.claim_id}")
        if entity["entityType"] != "claim":
            raise OperationError(
                f"Entity {params.claim_id} is not a claim (type: {entity['entityType']})"
            )
        claims = [entity]
    else:
        claims = _list(store, _status_filter(params.include_all_statuses), "claim", params.session)

    results = []
    for claim in claims:
        evidence_ids = {r["from"] for r in _relations(store, claim["id"], "incoming", "supports")}
        evidence_ids |= {r["to"] for r in _relations(store, claim["id"], "outgoing", "supports")}
        support_count = len(evidence_ids)
        if support_count < min_supports:
            results.append(
                {
                    "entity": claim,
                    "supportCount": support_count,
                    "evidenceGap": (min_supports - support_count) / min_supports
                    if min_supports > 0
                    else 1,
                }
            )
    results.sort(key=lambda r: -float(str(r["evidenceGap"])))
    return _limited(results, params.limit)


def stale_beliefs(params: StaleBeliefsInput, multi: MultiGraph) -> list[Doc]:
    days_old = params.days_old if params.days_old is not None else 30
    store = multi.get_store(params.graph)
    entities = _list(
        store,
        _status_filter(params.include_all_statuses),
        params.entity_type.value if params.entity_type else None,
        params.session,
    )
    results = []
    for entity in entities:
        last_evaluated = (entity.get("confidence") or {}).get("lastEvaluated")
        days = _days_since(last_evaluated)
        if days is None or days >= days_old:
            results.append(
                {
                    "entity": entity,
                    "lastEvaluated": last_evaluated if last_evaluated else None,
                    "daysSinceEvaluation": days,
                }
            )

    def sort_key(row: Doc) -> tuple[int, float]:
        days_value = row["daysSinceEvaluation"]
        return (0, 0.0) if days_value is None else (1, -float(days_value))

    results.sort(key=sort_key)
    return _limited(results, params.limit)


def provenance_chain(params: ProvenanceChainInput, multi: MultiGraph) -> list[Doc]:
    max_depth = min(params.max_depth if params.max_depth is not None else 10, MAX_DEPTH_LIMIT)
    store = multi.get_store(params.graph)
    start = _read(store, params.entity_id)
    if start is None:
        raise NotFoundError(f"Entity not found: {params.entity_id}")
    chain: list[Doc] = []
    visited: set[str] = set()
    queue: list[tuple[str, int]] = [(params.entity_id, 0)]
    while queue:
        entity_id, depth = queue.pop(0)
        if entity_id in visited or depth > max_depth:
            continue
        visited.add(entity_id)
        entity = start if depth == 0 else _read(store, entity_id)
        if entity is None:
            continue
        chain.append({"entity": entity, "depth": depth})
        for relation in _relations(store, entity_id, "outgoing", "sources"):
            if relation["to"] not in visited and depth + 1 <= max_depth:
                queue.append((relation["to"], depth + 1))
    if params.session is not None:
        chain = [
            item
            for item in chain
            if item["depth"] == 0
            or matches_session(
                params.session,
                item["entity"].get("session"),
                item["entity"].get("observations") or [],
            )
        ]
    return chain


def single_source_claims(params: EpistemicQueryInput, multi: MultiGraph) -> list[Doc]:
    store = multi.get_store(params.graph)
    claims = _list(store, _status_filter(params.include_all_statuses), "claim", params.session)
    results = [
        claim for claim in claims if len(_relations(store, claim["id"], "outgoing", "sources")) == 1
    ]
    return _limited(results, params.limit)


def most_certain(params: MostCertainInput, multi: MultiGraph) -> list[Doc]:
    top_k = params.top_k if params.top_k is not None else 10
    store = multi.get_store(params.graph)
    entities = _list(
        store,
        _status_filter(params.include_all_statuses),
        params.entity_type.value if params.entity_type else None,
        params.session,
    )
    results = []
    for entity in entities:
        confidence = entity.get("confidence")
        if not confidence:
            continue
        results.append(
            {
                "entity": entity,
                "confidenceScore": confidence["score"],
                "confidenceLabel": confidence_label(confidence["score"]).value,
            }
        )
    results.sort(key=lambda r: -float(r["confidenceScore"]))
    return results[:top_k]


def contested_claims(params: EpistemicQueryInput, multi: MultiGraph) -> list[Doc]:
    store = multi.get_store(params.graph)
    claims = _list(store, _status_filter(params.include_all_statuses), "claim", params.session)
    results = []
    for claim in claims:
        supports = _relations(store, claim["id"], "incoming", "supports")
        contradicts = _relations(store, claim["id"], "incoming", "contradicts")
        if supports and contradicts:
            results.append(
                {
                    "entity": claim,
                    "supportCount": len(supports),
                    "contradictCount": len(contradicts),
                }
            )
    results.sort(key=lambda r: -min(int(str(r["supportCount"])), int(str(r["contradictCount"]))))
    return _limited(results, params.limit)


def claims_from_source(params: ClaimsFromSourceInput, multi: MultiGraph) -> list[Doc]:
    store = multi.get_store(params.graph)
    if _read(store, params.source_id) is None:
        raise NotFoundError(f"Source entity not found: {params.source_id}")
    results = []
    for relation in _relations(store, params.source_id, "incoming", "sources"):
        entity = _read(store, relation["from"])
        if entity:
            results.append(entity)
    if params.session is not None:
        results = [
            e
            for e in results
            if matches_session(params.session, e.get("session"), e.get("observations") or [])
        ]
    results.sort(key=lambda e: (e["entityType"], e["name"]))
    return _limited(results, params.limit)


def inferred_claims(params: TypedEpistemicInput, multi: MultiGraph) -> list[Doc]:
    store = multi.get_store(params.graph)
    entities = _list(
        store,
        _status_filter(params.include_all_statuses),
        params.entity_type.value if params.entity_type else None,
        params.session,
    )
    results = [
        e
        for e in entities
        if (e.get("provenance") or {}).get("sourceType") == "inference"
        or (e.get("confidence") or {}).get("basis") == "inference"
    ]
    return _limited(results, params.limit)


def unprovenanced(params: TypedEpistemicInput, multi: MultiGraph) -> list[Doc]:
    store = multi.get_store(params.graph)
    entities = _list(
        store,
        _status_filter(params.include_all_statuses),
        params.entity_type.value if params.entity_type else None,
        params.session,
    )
    return _limited([e for e in entities if not e.get("provenance")], params.limit)


def open_questions(params: EpistemicQueryInput, multi: MultiGraph) -> list[Doc]:
    store = multi.get_store(params.graph)
    questions = _list(
        store, _status_filter(params.include_all_statuses), "question", params.session
    )
    return _limited(questions, params.limit)


def blocking_questions(params: BlockingQuestionsInput, multi: MultiGraph) -> list[Doc]:
    store = multi.get_store(params.graph)
    questions = _list(store, DEFAULT_ACTIVE_STATUSES, "question", params.session)
    results = []
    for question in questions:
        blocking = [
            *_relations(store, question["id"], "outgoing", "requires"),
            *_relations(store, question["id"], "outgoing", "questions"),
        ]
        if not blocking:
            continue
        blocked_entities = []
        for relation in blocking:
            blocked = _read(store, relation["to"])
            if blocked:
                blocked_entities.append(blocked)
        if params.domain:
            needle = params.domain.lower()
            blocked_entities = [e for e in blocked_entities if needle in e["name"].lower()]
            if not blocked_entities:
                continue
        results.append(
            {
                "entity": question,
                "blockedEntities": blocked_entities,
                "blockedCount": len(blocked_entities),
            }
        )
    results.sort(key=lambda r: -int(str(r["blockedCount"])))
    return _limited(results, params.limit)


def answered_questions(params: AnsweredQuestionsInput, multi: MultiGraph) -> list[Doc]:
    store = multi.get_store(params.graph)
    superseded = _list(store, ["superseded"], "question", params.session)
    active = _list(store, DEFAULT_ACTIVE_STATUSES, "question", params.session)
    answered_ids = {q["id"] for q in superseded}
    results = [*superseded]
    for question in active:
        if question["id"] in answered_ids:
            continue
        if _relations(store, question["id"], "incoming", "supports"):
            results.append(question)
            answered_ids.add(question["id"])
    if params.since:
        since = params.since
        results = [q for q in results if q["updated_at"] >= since]
    return _limited(results, params.limit)


# =============================================================================
# session-changelog / postmortem-evaluate / cross-session-contradictions
# =============================================================================


def session_changelog(params: SessionChangelogInput, multi: MultiGraph) -> Doc:
    # A session-scoped changelog needs no 'since' — the session is the window.
    if not params.since and not params.session:
        raise ValidationError("'since' parameter is required (ISO timestamp or 'last_postmortem')")
    store = multi.get_store(params.graph)
    if params.since == "last_postmortem":
        stored = store.get_metadata("lastPostmortemTimestamp")
        since = str(stored) if stored else "1970-01-01T00:00:00.000Z"
    else:
        since = params.since or "1970-01-01T00:00:00.000Z"
    try:
        datetime.fromisoformat(since.replace("Z", "+00:00"))
    except ValueError:
        raise ValidationError(
            "'since' must be a valid ISO timestamp or 'last_postmortem'"
        ) from None

    now = iso_now()
    all_entities = _list(store, [s.value for s in ALL_ENTITY_STATUSES])
    if params.session is not None:
        session = params.session
        all_entities = [
            e
            for e in all_entities
            if matches_session(session, e.get("session"), e.get("observations") or [])
        ]
    created_entities = []
    modified_entities = []
    status_changed = []
    for entity in all_entities:
        if entity["created_at"] >= since:
            created_entities.append(entity)
        elif entity["updated_at"] >= since:
            modified_entities.append(entity)
        if entity.get("statusChangedAt") and entity["statusChangedAt"] >= since:
            status_changed.append(entity)

    include_relations = params.include_relations is not False
    created_relations: list[Doc] = []
    modified_relations: list[Doc] = []
    if include_relations:
        for relation in store.list_relations():
            doc = relation.model_dump(by_alias=True, exclude_unset=True)
            if params.session is not None and doc.get("session") != params.session:
                continue
            if doc["created_at"] >= since:
                created_relations.append(doc)
            elif doc["updated_at"] >= since:
                modified_relations.append(doc)

    # A session-scoped changelog is a read-only view; only the unscoped
    # postmortem pass advances the checkpoint.
    if params.dry_run is not True and params.session is None:
        store.set_metadata("lastPostmortemTimestamp", now)

    result_head: Doc = {"since": since, "generatedAt": now}
    if params.session is not None:
        result_head["session"] = params.session
    return {
        **result_head,
        "entities": {
            "created": created_entities,
            "modified": modified_entities,
            "statusChanged": status_changed,
        },
        "relations": {"created": created_relations, "modified": modified_relations},
        "totals": {
            "entities": {
                "created": len(created_entities),
                "modified": len(modified_entities),
                "statusChanged": len(status_changed),
            },
            "relations": {
                "created": len(created_relations),
                "modified": len(modified_relations),
            },
            "total": len(created_entities)
            + len(modified_entities)
            + len(created_relations)
            + len(modified_relations),
        },
    }


def _compute_trend(history: list[Doc]) -> Doc:
    if len(history) < 3:
        return {"trend": "insufficient_data", "volatile": False}
    recent = history[-3:]
    first = float(recent[0]["score"])
    last = float(recent[-1]["score"])
    delta = last - first
    if delta > 0.05:
        trend = "improving"
    elif delta < -0.05:
        trend = "declining"
    else:
        trend = "stable"
    max_deviation = 0.0
    n = len(recent)
    for i in range(1, n - 1):
        t = i / (n - 1)
        interpolated = first + t * (last - first)
        max_deviation = max(max_deviation, abs(float(recent[i]["score"]) - interpolated))
    return {"trend": trend, "volatile": max_deviation > 0.15}


def postmortem_evaluate(params: PostmortemEvaluateInput, multi: MultiGraph) -> Doc:
    store = multi.get_store(params.graph)
    all_entities = _list(store, [s.value for s in ALL_ENTITY_STATUSES])
    all_relations = [
        r.model_dump(by_alias=True, exclude_unset=True) for r in store.list_relations()
    ]
    entity_by_id = {e["id"]: e for e in all_entities}
    incoming_supports: dict[str, list[Doc]] = {}
    for relation in all_relations:
        if relation["relationType"] == "supports":
            incoming_supports.setdefault(relation["to"], []).append(relation)

    def is_postmortem(doc: Doc) -> bool:
        method = (doc.get("provenance") or {}).get("extractionMethod") or ""
        return method.startswith("postmortem_")

    items: list[Doc] = []
    for entity in (e for e in all_entities if is_postmortem(e)):
        status = entity.get("status") or "active"
        if status in ("retracted", "deprecated"):
            classification = "rejected"
        elif status == "superseded":
            classification = "evolved"
        else:
            classification = "reinforced" if incoming_supports.get(entity["id"]) else "untested"
        items.append(
            {
                "id": entity["id"],
                "name": entity["name"],
                "itemType": "entity",
                "classification": classification,
                "status": status,
                "extractionMethod": (entity.get("provenance") or {}).get(
                    "extractionMethod", "unknown"
                ),
            }
        )

    for relation in (r for r in all_relations if is_postmortem(r)):
        from_entity = entity_by_id.get(relation["from"])
        to_entity = entity_by_id.get(relation["to"])
        if not from_entity or not to_entity:
            classification = "rejected"
        else:
            from_status = from_entity.get("status") or "active"
            to_status = to_entity.get("status") or "active"
            if {from_status, to_status} & {"retracted", "deprecated"}:
                classification = "rejected"
            elif "superseded" in (from_status, to_status):
                classification = "evolved"
            else:
                external_from = [
                    r
                    for r in incoming_supports.get(relation["from"], [])
                    if r["id"] != relation["id"]
                ]
                external_to = [
                    r
                    for r in incoming_supports.get(relation["to"], [])
                    if r["id"] != relation["id"]
                ]
                classification = "reinforced" if external_from or external_to else "untested"
        items.append(
            {
                "id": relation["id"],
                "name": f"{relation['from']} -> {relation['to']} ({relation['relationType']})",
                "itemType": "relation",
                "classification": classification,
                "extractionMethod": (relation.get("provenance") or {}).get(
                    "extractionMethod", "unknown"
                ),
            }
        )

    counts = {
        "reinforced": sum(1 for i in items if i["classification"] == "reinforced"),
        "untested": sum(1 for i in items if i["classification"] == "untested"),
        "rejected": sum(1 for i in items if i["classification"] == "rejected"),
        "evolved": sum(1 for i in items if i["classification"] == "evolved"),
        "total": len(items),
    }
    testable = counts["reinforced"] + counts["evolved"] + counts["rejected"]
    utility_score = (counts["reinforced"] + counts["evolved"]) / testable if testable else None
    flagged = utility_score is not None and utility_score < 0.3

    raw_history = store.get_metadata(POSTMORTEM_HISTORY_KEY)
    history: list[Doc] = raw_history if isinstance(raw_history, list) else []

    if utility_score is not None:
        updated = [*history, {"timestamp": iso_now(), "score": utility_score}][
            -MAX_HISTORY_ENTRIES:
        ]
        if not params.dry_run:
            store.set_metadata(POSTMORTEM_HISTORY_KEY, updated)
        return {
            "counts": counts,
            "utilityScore": utility_score,
            "flagged": flagged,
            "trend": _compute_trend(updated),
            "history": updated,
            "items": items,
        }
    return {
        "counts": counts,
        "utilityScore": utility_score,
        "flagged": flagged,
        "trend": _compute_trend(history),
        "history": history,
        "items": items,
    }


def _trace_session(store: FalkorGraphStore, entity_id: str, max_depth: int) -> Doc | None:
    visited: set[str] = set()
    queue: list[tuple[str, int]] = [(entity_id, 0)]
    while queue:
        current_id, depth = queue.pop(0)
        if current_id in visited:
            continue
        visited.add(current_id)
        if depth > 0:
            entity = _read(store, current_id)
            if entity and entity["entityType"] == "research_session":
                return entity
        if depth >= max_depth:
            continue
        for relation in [
            *_relations(store, current_id, "outgoing", "part_of"),
            *_relations(store, current_id, "outgoing", "sources"),
        ]:
            if relation["to"] not in visited:
                queue.append((relation["to"], depth + 1))
    return None


def cross_session_contradictions(
    params: CrossSessionContradictionsInput, multi: MultiGraph
) -> list[Doc]:
    store = multi.get_store(params.graph)
    max_depth = min(params.max_depth if params.max_depth is not None else 3, MAX_DEPTH_LIMIT)
    statuses = _status_filter(params.include_all_statuses)
    relations = store.list_relations(RelationFilter.model_validate({"relationType": "contradicts"}))
    seen_pairs: set[str] = set()
    results: list[Doc] = []
    for relation in relations:
        doc = relation.model_dump(by_alias=True, exclude_unset=True)
        pair_key = "|".join(sorted([doc["from"], doc["to"]]))
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)
        entity_a = _read(store, doc["from"])
        entity_b = _read(store, doc["to"])
        if not entity_a or not entity_b:
            continue
        if params.entity_type and (
            entity_a["entityType"] != params.entity_type.value
            or entity_b["entityType"] != params.entity_type.value
        ):
            continue
        status_a = entity_a.get("status") or "active"
        status_b = entity_b.get("status") or "active"
        if status_a not in statuses or status_b not in statuses:
            continue
        if params.min_confidence is not None:
            conf_a = (entity_a.get("confidence") or {}).get("score")
            conf_b = (entity_b.get("confidence") or {}).get("score")
            if conf_a is None or conf_b is None:
                continue
            if conf_a < params.min_confidence or conf_b < params.min_confidence:
                continue
        session_a = _trace_session(store, entity_a["id"], max_depth)
        session_b = _trace_session(store, entity_b["id"], max_depth)
        if not session_a or not session_b or session_a["id"] == session_b["id"]:
            continue
        if params.session_ids and (
            session_a["id"] not in params.session_ids and session_b["id"] not in params.session_ids
        ):
            continue
        results.append(
            {
                "entityA": entity_a,
                "entityB": entity_b,
                "sessionA": session_a,
                "sessionB": session_b,
                "relation": doc,
            }
        )
    return _limited(results, params.limit)


# =============================================================================
# propagate-credit
# =============================================================================


def _propagate_one(
    store: FalkorGraphStore,
    trigger_id: str,
    trigger_delta: float,
    options: dict[str, Any],
) -> Doc:
    trigger = _read(store, trigger_id)
    if trigger is None:
        raise NotFoundError(f"Trigger entity not found: {trigger_id}")
    empty = {
        "triggerId": trigger_id,
        "triggerDelta": 0,
        "changes": [],
        "skippedNoConfidence": 0,
        "totalEntitiesAffected": 0,
        "maxDepthReached": 0,
    }
    if not trigger.get("confidence"):
        return empty
    damping = options.get("dampingFactor", 0.5)
    if not 0 <= damping <= 1:
        raise ValidationError(f"dampingFactor must be between 0 and 1 (inclusive), got {damping}")
    max_depth = min(options.get("maxDepth", 3), MAX_DEPTH_LIMIT)
    min_delta = options.get("minDelta", 0.01)
    dry_run = options.get("dryRun", True)
    relation_types = options.get("relationTypes", ["supports", "contradicts"])
    mode = options.get("propagationMode", "signal")
    if trigger_delta == 0:
        return empty

    changes: list[Doc] = []
    skipped_no_confidence = 0
    max_depth_reached = 0
    visited = {trigger_id}
    queue: list[Doc] = [
        {"entityId": trigger_id, "depth": 0, "incomingDelta": trigger_delta, "path": [trigger_id]}
    ]
    while queue:
        item = queue.pop(0)
        if item["depth"] >= max_depth or abs(item["incomingDelta"]) < min_delta:
            continue
        outgoing = _relations(store, item["entityId"], "outgoing")
        for relation in (r for r in outgoing if r["relationType"] in relation_types):
            target_id = relation["to"]
            if target_id in visited:
                continue
            visited.add(target_id)
            new_depth = item["depth"] + 1
            max_depth_reached = max(max_depth_reached, new_depth)
            polarity = -1 if relation["relationType"] == "contradicts" else 1
            strength_mult = VITERBI_STRENGTH_MAP.get(relation["strength"], 0.5)
            incoming = _relations(store, target_id, "incoming")
            n = max(sum(1 for r in incoming if r["relationType"] in relation_types), 1)
            hop_delta = (1 / n) * damping * item["incomingDelta"] * strength_mult * polarity
            if abs(hop_delta) < min_delta:
                continue
            target = _read(store, target_id)
            if target is None:
                continue
            new_path = [*item["path"], target_id]
            if not target.get("confidence"):
                skipped_no_confidence += 1
                queue.append(
                    {
                        "entityId": target_id,
                        "depth": new_depth,
                        "incomingDelta": hop_delta,
                        "path": new_path,
                    }
                )
                continue
            previous = float(target["confidence"]["score"])
            new_score = max(0.0, min(1.0, previous + hop_delta))
            actual_delta = new_score - previous
            if abs(actual_delta) < min_delta:
                continue
            reason = (
                f"Credit propagated from {trigger_id} (Δ{trigger_delta:.3f}) "
                f"via {' → '.join(new_path)} "
                f"({relation['relationType']}, strength={relation['strength']}, "
                f"N={n}, depth={new_depth})"
            )
            changes.append(
                {
                    "entityId": target_id,
                    "entityName": target["name"],
                    "previousConfidence": previous,
                    "newConfidence": new_score,
                    "delta": actual_delta,
                    "reason": reason,
                    "propagationPath": new_path,
                    "depth": new_depth,
                }
            )
            downstream = actual_delta if mode == "applied" else hop_delta
            queue.append(
                {
                    "entityId": target_id,
                    "depth": new_depth,
                    "incomingDelta": downstream,
                    "path": new_path,
                }
            )

    if not dry_run:
        now = iso_now()
        for change in changes:
            current = _read(store, str(change["entityId"]))
            if not current or not current.get("confidence"):
                continue
            store.update_entity(
                str(change["entityId"]),
                {
                    "confidence": {
                        "score": change["newConfidence"],
                        "basis": "calculated",
                        "lastEvaluated": now,
                    },
                    "provenance": {
                        "sourceType": "inference",
                        "sourceId": trigger_id,
                        "externalRef": None,
                        "extractionDate": now,
                        "extractor": "credit-propagation",
                        "extractionMethod": "automated",
                    },
                    "changeType": "confidence_updated",
                    "changeReason": change["reason"],
                },
            )

    return {
        "triggerId": trigger_id,
        "triggerDelta": trigger_delta,
        "changes": changes,
        "skippedNoConfidence": skipped_no_confidence,
        "totalEntitiesAffected": len(changes),
        "maxDepthReached": max_depth_reached,
    }


def propagate_credit(params: PropagateCreditInput, multi: MultiGraph) -> list[Doc]:
    store = multi.get_store(params.graph)
    ids = params.entity_ids
    if not ids:
        raise ValidationError("At least one entity ID is required (provide entityIds or entityId)")
    valid_types = {t.value for t in ALL_RELATION_TYPES}
    options: dict[str, Any] = {}
    if params.damping_factor is not None:
        options["dampingFactor"] = params.damping_factor
    if params.max_depth is not None:
        options["maxDepth"] = params.max_depth
    if params.min_delta is not None:
        options["minDelta"] = params.min_delta
    if params.dry_run is not None:
        options["dryRun"] = params.dry_run
    if params.relation_types is not None:
        options["relationTypes"] = [t for t in params.relation_types if t in valid_types]
    if params.propagation_mode is not None:
        options["propagationMode"] = params.propagation_mode
    return [_propagate_one(store, entity_id, params.delta, options) for entity_id in ids]
