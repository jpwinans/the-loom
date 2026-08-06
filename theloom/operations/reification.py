"""reify-patterns + trigger queue commands.

reify-patterns: Weisfeiler-Leman ego fingerprints — every non-pattern entity
gets a hash of its rooted neighborhood (depth default 2); entities sharing a
hash form a pattern when >= minOccurrences (default 3); dry-run by default.
Hashes are the first 16 hex chars of SHA-256 over a canonical string; relation
type lists keep per-edge duplicates and are sorted; instance_of edges and
pattern entities are excluded from fingerprinting for idempotency (an existing
pattern is recognized by its exact `fingerprint: <hash>` observation line).

trigger-status / process-triggers are exposed as CLI commands returning the
standard JSON envelope. Queue state lives in graph metadata under 'trigger_queue'.
The mutation-trigger screening itself is not wired: in the one-shot CLI the
handler never sees a second loaded graph, so it can never enqueue — the
queue's observable CLI behavior is read/dequeue only.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from theloom.graph.hydrate import hydrate_graph
from theloom.model import EntityCreate, EntityFilter, RelationCreate
from theloom.operations.common import CommandInput
from theloom.reification.fingerprint import (
    compute_fingerprint as hash_at_depth,
)
from theloom.reification.fingerprint import (
    describe_fingerprint as _describe,
)
from theloom.reification.fingerprint import (
    neighborhood_meta as _neighborhood_meta,
)
from theloom.store.falkor import FalkorGraphStore
from theloom.store.multigraph import MultiGraph
from theloom.timeutil import iso_now

__all__ = [
    "ReifyPatternsInput",
    "TriggerStatusInput",
    "ProcessTriggersInput",
    "hash_at_depth",
    "reify_patterns",
    "trigger_status",
    "process_triggers",
]

Doc = dict[str, Any]

MAX_DEPTH_LIMIT = 10
DEFAULT_MAX_DEPTH = 2
DEFAULT_MIN_OCCURRENCES = 3
DEFAULT_MAX_PATTERNS = 5
TRIGGER_QUEUE_METADATA_KEY = "trigger_queue"


class ReifyPatternsInput(CommandInput):
    min_occurrences: int | None = Field(default=None, ge=1, alias="minOccurrences")
    max_depth: int | None = Field(default=None, ge=1, le=5, alias="maxDepth")
    max_patterns: int | None = Field(default=None, ge=1, alias="maxPatterns")
    dry_run: bool | None = Field(default=None, alias="dryRun")
    graph: str | None = None


class TriggerStatusInput(CommandInput):
    graph: str | None = Field(default=None, max_length=200)


class ProcessTriggersInput(CommandInput):
    graph: str | None = Field(default=None, max_length=200)
    limit: int | None = Field(default=None, ge=1, le=50)


# =============================================================================
# WL fingerprints
# =============================================================================
#
# hash_at_depth / _neighborhood_meta / _describe are re-exports of
# theloom.reification.fingerprint's compute_fingerprint / neighborhood_meta /
# describe_fingerprint (see the import block above) so reify-patterns'
# established output and call-site names stay unchanged while there is a
# single implementation.


def _pattern_name(info: dict[str, Any]) -> str:
    incoming = info["incomingRelationTypes"]
    outgoing = info["outgoingRelationTypes"]
    summary = []
    if incoming:
        summary.append(f"{len(incoming)} in: {', '.join(incoming)}")
    if outgoing:
        summary.append(f"{len(outgoing)} out: {', '.join(outgoing)}")
    if not summary:
        return f"Structural Motif: isolated {info['entityType']}"
    return f"Structural Motif: {info['entityType']} ({'; '.join(summary)})"


def reify_patterns(params: ReifyPatternsInput, multi: MultiGraph) -> Doc:
    store = multi.get_store(params.graph)
    dry_run = params.dry_run if params.dry_run is not None else True
    max_depth = min(max(params.max_depth if params.max_depth is not None else 2, 0), 10)
    min_occurrences = params.min_occurrences if params.min_occurrences is not None else 3
    max_patterns = params.max_patterns if params.max_patterns is not None else 5

    all_entities = [e.model_dump(by_alias=True, exclude_unset=True) for e in store.list_entities()]
    all_relations = [
        r.model_dump(by_alias=True, exclude_unset=True) for r in store.list_relations()
    ]
    fingerprint_entities = [e for e in all_entities if e["entityType"] != "pattern"]
    entity_ids = {e["id"] for e in fingerprint_entities}
    fingerprint_relations = [
        r
        for r in all_relations
        if r["relationType"] != "instance_of" and r["from"] in entity_ids and r["to"] in entity_ids
    ]
    graph = hydrate_graph(fingerprint_entities, fingerprint_relations)

    cache: dict[str, str] = {}
    buckets: dict[str, dict[str, Any]] = {}
    for node_id in graph.nodes():
        digest = hash_at_depth(graph, node_id, max_depth, cache)
        meta = _neighborhood_meta(graph, node_id)
        bucket = buckets.get(digest)
        if bucket is None:
            buckets[digest] = {
                "info": {
                    "entityType": graph.node_docs[node_id]["entityType"],
                    **meta,
                },
                "entityIds": [node_id],
            }
        else:
            bucket["entityIds"].append(node_id)

    groups = [
        {
            "fingerprint": digest,
            "description": _describe(bucket["info"]),
            "info": bucket["info"],
            "entityIds": bucket["entityIds"],
            "count": len(bucket["entityIds"]),
        }
        for digest, bucket in buckets.items()
        if len(bucket["entityIds"]) >= min_occurrences
    ]
    groups.sort(key=lambda g: (-int(str(g["count"])), str(g["fingerprint"])))
    groups = groups[:max_patterns]

    existing_patterns = [
        e.model_dump(by_alias=True, exclude_unset=True)
        for e in store.list_entities(EntityFilter.model_validate({"entityType": "pattern"}))
    ]

    patterns_created = 0
    patterns_skipped = 0
    results: list[Doc] = []
    for group in groups:
        name = _pattern_name(group["info"])
        marker = f"fingerprint: {group['fingerprint']}"
        existing = next(
            (p for p in existing_patterns if marker in (p.get("observations") or [])), None
        )
        row: Doc = {
            "fingerprint": group["fingerprint"],
            "description": group["description"],
            "memberCount": group["count"],
            "memberIds": group["entityIds"],
            "created": False,
            "skipped": False,
            "patternName": name,
        }
        if existing is not None:
            patterns_skipped += 1
            row["skipped"] = True
            row["patternEntityId"] = existing["id"]
        elif not dry_run:
            pattern_entity = store.create_entity(
                EntityCreate.model_validate(
                    {
                        "name": name,
                        "entityType": "pattern",
                        "observations": [
                            marker,
                            f"description: {group['description']}",
                            f"member_count: {group['count']}",
                            f"detected_at: {iso_now()}",
                            (
                                "detection_params: "
                                f"maxDepth={params.max_depth or 2}, "
                                f"minOccurrences={params.min_occurrences or 3}"
                            ),
                        ],
                    }
                )
            )
            for member_id in group["entityIds"]:
                store.create_relation(
                    RelationCreate.model_validate(
                        {
                            "from": member_id,
                            "to": pattern_entity.id,
                            "relationType": "instance_of",
                            "polarity": None,
                            "strength": "moderate",
                            "evidence": (
                                "Detected via structural fingerprinting "
                                f"(fingerprint: {group['fingerprint']})"
                            ),
                        }
                    )
                )
            patterns_created += 1
            row["created"] = True
            row["patternEntityId"] = pattern_entity.id
        # dryRun + new pattern: patternEntityId stays absent (undefined-omitted).
        # Reorder to the canonical construction order when the id is present.
        if "patternEntityId" in row:
            row = {
                "fingerprint": row["fingerprint"],
                "description": row["description"],
                "memberCount": row["memberCount"],
                "memberIds": row["memberIds"],
                "created": row["created"],
                "skipped": row["skipped"],
                "patternEntityId": row["patternEntityId"],
                "patternName": row["patternName"],
            }
        results.append(row)

    return {
        "patternsDetected": len(groups),
        "patternsCreated": patterns_created,
        "patternsSkipped": patterns_skipped,
        "dryRun": dry_run,
        "patterns": results,
    }


# =============================================================================
# Trigger queue (CLI-exposed, standard JSON envelope)
# =============================================================================


def _load_queue(store: FalkorGraphStore) -> Doc | None:
    queue = store.get_metadata(TRIGGER_QUEUE_METADATA_KEY)
    return queue if isinstance(queue, dict) else None


def trigger_status(params: TriggerStatusInput, multi: MultiGraph) -> Doc:
    store = multi.get_store(params.graph)
    queue = _load_queue(store) or {
        "pending": [],
        "processed": [],
        "maxPending": 50,
        "lastProcessed": "",
    }
    by_recommendation: dict[str, int] = {}
    for candidate in queue.get("pending") or []:
        recommendation = candidate.get("recommendation")
        if recommendation:
            by_recommendation[recommendation] = by_recommendation.get(recommendation, 0) + 1
    return {
        "pendingCount": len(queue.get("pending") or []),
        "processedCount": len(queue.get("processed") or []),
        "maxPending": queue.get("maxPending", 50),
        "lastProcessed": queue.get("lastProcessed", ""),
        "byRecommendation": by_recommendation,
    }


def process_triggers(params: ProcessTriggersInput, multi: MultiGraph) -> Doc:
    store = multi.get_store(params.graph)
    queue = _load_queue(store)
    if not queue or not queue.get("pending"):
        return {"candidates": [], "message": "No pending trigger candidates"}
    limit = params.limit if params.limit is not None else 10
    pending = sorted(queue["pending"], key=lambda c: -float(c.get("farAnalogyScore", 0)))
    candidates = pending[:limit]
    remaining = pending[limit:]
    dedupe_keys = [f"{c.get('mutatedEntityId')}::{c.get('targetComponentId')}" for c in candidates]
    processed = [*(queue.get("processed") or []), *dedupe_keys][-500:]
    updated = {
        **queue,
        "pending": remaining,
        "processed": processed,
        "lastProcessed": iso_now(),
    }
    store.set_metadata(TRIGGER_QUEUE_METADATA_KEY, updated)
    return {
        "candidates": candidates,
        "dequeuedCount": len(candidates),
        "remainingPending": len(remaining),
    }
