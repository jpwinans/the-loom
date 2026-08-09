"""merge-entities — consolidate a duplicate entity into its canonical twin.

The operation computes a merge plan from store reads, then applies it through
``FalkorGraphStore.apply_entity_merge`` — ONE atomic query plus ONE
``entities_merged`` event:

1. Union the secondary's observations into the primary (dedup identical).
2. Merge confidence (keep the stronger score) and provenance (primary wins,
   the secondary fills a missing record).
3. Redirect every relation on the secondary to the primary (both directions,
   preserving relation ids), skipping would-be self-loops and duplicates —
   skipped relations stay on the secondary as history.
4. Supersede the secondary: status 'superseded' (reason 'duplicate') plus a
   primary→supersedes→secondary relation. The secondary is NEVER hard-deleted;
   bi-temporal invalidation preserves both entities' prior incarnations.

A merge with nothing to do (already merged) writes nothing, so the operation
is idempotent. ``dryRun`` previews the counts without mutating.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import Field

from theloom.errors import NotFoundError, ValidationError
from theloom.model import Entity, EntityStatus, Relation, is_valid_transition
from theloom.operations.common import CommandInput, UuidStr
from theloom.store.falkor import FalkorGraphStore
from theloom.store.multigraph import MultiGraph
from theloom.timeutil import iso_now

Doc = dict[str, Any]


class MergeEntitiesInput(CommandInput):
    primary: UuidStr
    secondary: UuidStr
    graph: str | None = None
    dry_run: bool | None = Field(default=None, alias="dryRun")


def _read_doc_or_raise(store: FalkorGraphStore, entity_id: str, role: str) -> Doc:
    doc = store.read_entity_doc(entity_id)
    if doc is None:
        raise NotFoundError(
            f"{role} entity not found with ID: {entity_id}. "
            "Use list_entities to see available entities."
        )
    return doc


def _merged_observations(primary: Doc, secondary: Doc) -> tuple[list[str], int]:
    """Primary's observations followed by the secondary's new ones, deduped."""
    merged: list[str] = [*primary["observations"]]
    seen = set(merged)
    added = 0
    for observation in secondary["observations"]:
        if observation not in seen:
            merged.append(observation)
            seen.add(observation)
            added += 1
    return merged, added


def _stronger_confidence(primary: Doc, secondary: Doc) -> Doc | None:
    """The secondary's confidence iff strictly stronger (or the primary has
    none); None when the primary's record already wins."""
    ours = primary.get("confidence")
    theirs = secondary.get("confidence")
    if theirs is None:
        return None
    if ours is None or float(theirs["score"]) > float(ours["score"]):
        return dict(theirs)
    return None


def _redirect_plan(
    store: FalkorGraphStore, primary_id: str, secondary_id: str, now: str
) -> list[Doc]:
    """New relation docs (from/to rewritten to the primary) for every relation
    on the secondary that neither self-loops nor duplicates one on the primary."""
    existing_keys = {
        (r.from_, r.to, r.relation_type.value) for r in store.get_relations(primary_id, "both")
    }
    redirects: list[Doc] = []
    planned_ids: set[str] = set()
    for relation in store.get_relations(secondary_id, "both"):
        if relation.id in planned_ids:
            continue
        planned_ids.add(relation.id)
        if relation.from_ == secondary_id:
            new_from, new_to = primary_id, relation.to
        else:
            new_from, new_to = relation.from_, primary_id
        if new_from == new_to:
            continue  # would self-loop (relation between the merge pair)
        key = (new_from, new_to, relation.relation_type.value)
        if key in existing_keys:
            continue  # duplicate of a relation already on the primary
        existing_keys.add(key)
        doc = relation.model_dump(by_alias=True, exclude_unset=True)
        doc.update({"from": new_from, "to": new_to, "updated_at": now})
        redirects.append(doc)
    return redirects


def _supersedes_doc(
    store: FalkorGraphStore, primary_id: str, secondary_id: str, now: str
) -> Doc | None:
    """The primary→secondary supersedes relation doc, or None if one exists."""
    if store.read_relations(primary_id, secondary_id, "supersedes"):
        return None
    return {
        "id": str(uuid.uuid4()),
        "from": primary_id,
        "to": secondary_id,
        "relationType": "supersedes",
        "polarity": None,
        "strength": "strong",
        "evidence": (f"Auto-created when entity {secondary_id} was merged into {primary_id}"),
        "created_at": now,
        "updated_at": now,
    }


def _bumped(doc: Doc, now: str, change_reason: str) -> Doc:
    """Ops-layer revision metadata for a merged incarnation."""
    return {
        **doc,
        "updated_at": now,
        "version": (doc.get("version") or 0) + 1,
        "previousVersionId": doc["id"],
        "changeType": "merged",
        "changeReason": change_reason,
    }


def merge_entities(params: MergeEntitiesInput, multi: MultiGraph) -> Doc:
    if params.primary == params.secondary:
        raise ValidationError(
            "Cannot merge an entity into itself: primary and secondary must differ"
        )
    store = multi.get_store(params.graph)
    primary = _read_doc_or_raise(store, params.primary, "Primary")
    secondary = _read_doc_or_raise(store, params.secondary, "Secondary")
    if not is_valid_transition(secondary.get("status"), EntityStatus.SUPERSEDED):
        raise ValidationError(
            f"Cannot merge: invalid status transition from "
            f"'{secondary.get('status') or 'active'}' to 'superseded' for the secondary entity"
        )

    now = iso_now()
    observations, observations_merged = _merged_observations(primary, secondary)
    confidence = _stronger_confidence(primary, secondary)
    provenance = secondary.get("provenance") if primary.get("provenance") is None else None
    redirects = _redirect_plan(store, params.primary, params.secondary, now)
    supersedes = _supersedes_doc(store, params.primary, params.secondary, now)

    result: Doc = {
        "primaryId": params.primary,
        "primaryName": primary["name"],
        "secondaryId": params.secondary,
        "secondaryName": secondary["name"],
        "observationsMerged": observations_merged,
        "relationsRedirected": len(redirects),
        "dryRun": params.dry_run is True,
    }
    if params.dry_run is True:
        return result

    primary_changed = observations_merged > 0 or confidence is not None or provenance is not None
    secondary_changed = secondary.get("status") != EntityStatus.SUPERSEDED.value
    if not (primary_changed or secondary_changed or redirects or supersedes):
        return result  # already fully merged — idempotent no-op

    new_primary = _bumped(
        {**primary, "observations": observations},
        now,
        f"Merged entity {params.secondary} into this entity",
    )
    if confidence is not None:
        new_primary["confidence"] = confidence
    if provenance is not None:
        new_primary["provenance"] = dict(provenance)
    new_secondary = _bumped(
        {
            **secondary,
            "status": EntityStatus.SUPERSEDED.value,
            "statusReason": "duplicate",
            "statusChangedAt": now,
        },
        now,
        f"Merged into entity {params.primary}",
    )
    Entity.model_validate(new_primary)
    Entity.model_validate(new_secondary)
    for doc in redirects:
        Relation.model_validate(doc)
    if supersedes is not None:
        Relation.model_validate(supersedes)

    store.apply_entity_merge(
        primary_doc=new_primary,
        secondary_doc=new_secondary,
        redirects=redirects,
        supersedes_doc=supersedes,
        previous_primary=primary,
        previous_secondary=secondary,
        now=now,
    )
    return result
