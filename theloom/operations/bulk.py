"""Bulk import of entities and relations.

Entities are created via the store directly — no ops-layer revision fields —
and deduplicated idempotently by the ``name::entityType`` composite key
(merging new observations into existing entities). Relations reference
entities by NAME; names resolve against the import batch first, then existing
graph entities (non-retracted, active preferred). An existing relation of the
same type between a resolved pair is skipped; other types between the same
pair import normally (parallel typed edges are native to the storage model).
Relations are validated against the same polarity partition create-relation
gates on: only causal types may carry polarity. ``dryRun`` validates and maps
(placeholder UUIDs) without writing. JSONL input parses per line with collected
errors prepended to import errors.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Any

from pydantic import Field

from theloom.model import (
    ALL_ENTITY_TYPES,
    ALL_RELATION_TYPES,
    CAUSAL_RELATION_TYPES,
    EntityCreate,
    EntityFilter,
    RelationCreate,
)
from theloom.operations.common import CommandInput
from theloom.store.falkor import FalkorGraphStore
from theloom.store.multigraph import MultiGraph
from theloom.timeutil import iso_now
from theloom.verification.checks import non_causal_polarity_error

MAX_ENTITIES_LIMIT = 100_000
MAX_RELATIONS_LIMIT = 100_000
MAX_OBSERVATIONS_PER_ENTITY = 10_000

_ENTITY_TYPE_VALUES = {t.value for t in ALL_ENTITY_TYPES}
_RELATION_TYPE_VALUES = {t.value for t in ALL_RELATION_TYPES}
_CAUSAL_RELATION_TYPE_VALUES = {t.value for t in CAUSAL_RELATION_TYPES}
_NON_RETRACTED = EntityFilter.model_validate(
    {"statusFilter": ["active", "superseded", "deprecated", "investigating"]}
)


class BulkImportInput(CommandInput):
    """Inline arrays and/or raw JSONL. Items are validated per-item inside the
    import (reported as per-item errors), not by this model — the import
    collects errors rather than failing fast."""

    entities: list[dict[str, Any]] | None = Field(default=None, max_length=MAX_ENTITIES_LIMIT)
    relations: list[dict[str, Any]] | None = Field(default=None, max_length=MAX_RELATIONS_LIMIT)
    jsonl_input: str | None = Field(default=None, alias="jsonlInput")
    dry_run: bool | None = Field(default=None, alias="dryRun")
    graph: str | None = None


def parse_jsonl(text: str) -> dict[str, Any]:
    """Parse JSONL into {data: {entities, relations}, errors} with per-line messages."""
    entities: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for index, raw_line in enumerate(text.split("\n")):
        line = raw_line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(
                {"type": "validation_error", "message": f"Line {index + 1}: invalid JSON: {exc}"}
            )
            continue
        kind = parsed.get("type") if isinstance(parsed, dict) else None
        if kind == "entity":
            entities.append(
                {
                    "name": parsed.get("name"),
                    "entityType": parsed.get("entityType"),
                    "observations": parsed.get("observations") or [],
                    "confidence": parsed.get("confidence"),
                    "provenance": parsed.get("provenance"),
                }
            )
        elif kind == "relation":
            relations.append(
                {
                    "from": parsed.get("from"),
                    "to": parsed.get("to"),
                    "relationType": parsed.get("relationType"),
                    "polarity": parsed.get("polarity"),
                    "strength": parsed.get("strength"),
                    "evidence": parsed.get("evidence"),
                }
            )
        else:
            errors.append(
                {
                    "type": "validation_error",
                    "message": (
                        f"Line {index + 1}: unknown type '{kind}', expected 'entity' or 'relation'"
                    ),
                }
            )
    return {"data": {"entities": entities, "relations": relations}, "errors": errors}


def _validate_entity(entity: dict[str, Any]) -> dict[str, Any] | None:
    name = entity.get("name")
    if not name or not isinstance(name, str) or not name.strip():
        return {
            "type": "validation_error",
            "entity": name or "(empty)",
            "message": "Entity name is required and must be a non-empty string",
        }
    entity_type = entity.get("entityType")
    if entity_type not in _ENTITY_TYPE_VALUES:
        return {
            "type": "validation_error",
            "entity": name,
            "message": (
                f'Invalid entityType "{entity_type}": must be one of '
                + ", ".join(t.value for t in ALL_ENTITY_TYPES)
            ),
        }
    observations = entity.get("observations")
    if not isinstance(observations, list):
        return {
            "type": "validation_error",
            "entity": name,
            "message": "Entity observations must be an array of strings",
        }
    if len(observations) > MAX_OBSERVATIONS_PER_ENTITY:
        return {
            "type": "validation_error",
            "entity": name,
            "message": (
                f"Entity has {len(observations)} observations, exceeding maximum "
                f"allowed limit of {MAX_OBSERVATIONS_PER_ENTITY}"
            ),
        }
    if not all(isinstance(o, str) for o in observations):
        return {
            "type": "validation_error",
            "entity": name,
            "message": "All observations must be strings",
        }
    confidence = entity.get("confidence")
    if confidence is not None and (
        not isinstance(confidence, dict)
        or not isinstance(confidence.get("score"), int | float)
        or isinstance(confidence.get("score"), bool)
        or not 0 <= confidence["score"] <= 1
        or not isinstance(confidence.get("basis"), str)
    ):
        return {
            "type": "validation_error",
            "entity": name,
            "message": "Confidence must have a numeric score between 0 and 1 and a string basis",
        }
    provenance = entity.get("provenance")
    if provenance is not None and (
        not isinstance(provenance, dict)
        or not isinstance(provenance.get("sourceType"), str)
        or not isinstance(provenance.get("extractor"), str)
    ):
        return {
            "type": "validation_error",
            "entity": name,
            "message": "Provenance must have string sourceType and string extractor",
        }
    return None


def _validate_relation(relation: dict[str, Any]) -> dict[str, Any] | None:
    from_name = relation.get("from")
    to_name = relation.get("to")
    if not from_name or not isinstance(from_name, str) or not from_name.strip():
        return {
            "type": "validation_error",
            "from": from_name,
            "to": to_name,
            "message": 'Relation "from" is required and must be a non-empty string',
        }
    if not to_name or not isinstance(to_name, str) or not to_name.strip():
        return {
            "type": "validation_error",
            "from": from_name,
            "to": to_name,
            "message": 'Relation "to" is required and must be a non-empty string',
        }
    relation_type = relation.get("relationType")
    if relation_type not in _RELATION_TYPE_VALUES:
        return {
            "type": "validation_error",
            "from": from_name,
            "to": to_name,
            "message": (
                f'Invalid relationType "{relation_type}": must be one of '
                + ", ".join(t.value for t in ALL_RELATION_TYPES)
            ),
        }
    polarity = relation.get("polarity")
    # The same partition create-relation gates on: polarity is causal-only, so
    # bulk-import cannot be a side door for a polarized structural edge.
    if polarity is not None and relation_type not in _CAUSAL_RELATION_TYPE_VALUES:
        return {
            "type": "validation_error",
            "from": from_name,
            "to": to_name,
            "message": non_causal_polarity_error(str(relation_type), str(polarity)),
        }
    return None


def _composite_key(name: str, entity_type: str) -> str:
    return f"{name}::{entity_type}"


def _existing_lookup(store: FalkorGraphStore) -> dict[str, Any]:
    """name::entityType → entity, preferring active over non-active."""
    lookup: dict[str, Any] = {}
    for entity in store.list_entities(_NON_RETRACTED):
        key = _composite_key(entity.name, entity.entity_type.value)
        if key not in lookup:
            lookup[key] = entity
        else:
            existing_active = lookup[key].status is None or lookup[key].status == "active"
            candidate_active = entity.status is None or entity.status == "active"
            if not existing_active and candidate_active:
                lookup[key] = entity
    return lookup


def resolve_bulk_import_document(input_doc: dict[str, Any]) -> dict[str, Any]:
    """The bulk-import transport policy: file path / stdin-JSONL / inline modes.

    This runs on the raw pre-validation input document (the CLI's raw_handler
    hatch), because the transport mode selects which raw keys to forward
    before ``BulkImportInput`` ever sees them — inline mode, in particular,
    intentionally drops ``jsonlInput`` (JSONL is reachable via stdin only).
    """
    if isinstance(input_doc.get("file"), str):
        file_data = json.loads(Path(input_doc["file"]).resolve().read_text(encoding="utf-8"))
        return {
            "entities": file_data.get("entities") or [],
            "relations": file_data.get("relations") or [],
            "graph": input_doc.get("graph"),
            "dryRun": input_doc.get("dryRun"),
        }
    if input_doc.get("stdin") is True:
        return {
            "jsonlInput": sys.stdin.read(),
            "graph": input_doc.get("graph"),
            "dryRun": input_doc.get("dryRun"),
        }
    return {
        "entities": input_doc.get("entities") or [],
        "relations": input_doc.get("relations") or [],
        "graph": input_doc.get("graph"),
        "dryRun": input_doc.get("dryRun"),
    }


def bulk_import_raw(input_doc: dict[str, Any], multi: MultiGraph) -> dict[str, Any]:
    """Resolve the raw input document's transport mode, then run the import."""
    doc = resolve_bulk_import_document(input_doc)
    return bulk_import(BulkImportInput.model_validate(doc), multi)


def bulk_import(params: BulkImportInput, multi: MultiGraph) -> dict[str, Any]:
    store = multi.get_store(params.graph)

    entities = list(params.entities or [])
    relations = list(params.relations or [])
    parse_errors: list[dict[str, Any]] = []
    if params.jsonl_input is not None:
        parsed = parse_jsonl(params.jsonl_input)
        entities = [*parsed["data"]["entities"], *entities]
        relations = [*parsed["data"]["relations"], *relations]
        parse_errors = parsed["errors"]

    if len(entities) > MAX_ENTITIES_LIMIT:
        return _limit_result(
            f"Entity count {len(entities)} exceeds maximum allowed limit of {MAX_ENTITIES_LIMIT}"
        )
    if len(relations) > MAX_RELATIONS_LIMIT:
        return _limit_result(
            f"Relation count {len(relations)} exceeds maximum allowed limit of "
            f"{MAX_RELATIONS_LIMIT}"
        )

    dry_run = params.dry_run or False
    errors: list[dict[str, Any]] = []
    mapping: dict[str, str] = {}
    entities_created = 0
    entities_merged = 0
    relations_created = 0
    relations_skipped = 0

    lookup = _existing_lookup(store) if entities else {}

    for entity_input in entities:
        validation_error = _validate_entity(entity_input)
        if validation_error:
            errors.append(validation_error)
            continue
        key = _composite_key(entity_input["name"], entity_input["entityType"])
        existing = lookup.get(key)
        if existing is not None:
            if not dry_run:
                existing_obs = list(existing.observations)
                new_obs = [o for o in entity_input["observations"] if o not in set(existing_obs)]
                if new_obs:
                    merged = [*existing_obs, *new_obs]
                    updated = store.update_entity(existing.id, {"observations": merged})
                    lookup[key] = updated
            mapping[entity_input["name"]] = existing.id
            entities_merged += 1
        elif dry_run:
            mapping[entity_input["name"]] = str(uuid.uuid4())
            entities_created += 1
        else:
            doc: dict[str, Any] = {
                "name": entity_input["name"],
                "entityType": entity_input["entityType"],
                "observations": entity_input["observations"],
            }
            if entity_input.get("confidence"):
                doc["confidence"] = {
                    **entity_input["confidence"],
                    "lastEvaluated": entity_input["confidence"].get("lastEvaluated") or iso_now(),
                }
            if entity_input.get("provenance"):
                provenance = entity_input["provenance"]
                doc["provenance"] = {
                    **provenance,
                    "sourceId": provenance.get("sourceId"),
                    "externalRef": provenance.get("externalRef"),
                    "extractionDate": provenance.get("extractionDate") or iso_now(),
                    "extractionMethod": provenance.get("extractionMethod"),
                }
            created = store.create_entity(EntityCreate.model_validate(doc))
            mapping[entity_input["name"]] = created.id
            lookup[key] = created
            entities_created += 1

    # Combined name resolution: import batch first, then existing graph
    # entities (non-retracted, active preferred).
    if relations:
        by_name: dict[str, list[Any]] = {}
        for entity in store.list_entities(_NON_RETRACTED):
            by_name.setdefault(entity.name, []).append(entity)
        for name, candidates in by_name.items():
            if name in mapping:
                continue
            active = next((e for e in candidates if e.status is None or e.status == "active"), None)
            mapping[name] = (active or candidates[0]).id

    for relation_input in relations:
        validation_error = _validate_relation(relation_input)
        if validation_error:
            errors.append(validation_error)
            relations_skipped += 1
            continue
        from_id = mapping.get(relation_input["from"])
        if not from_id:
            errors.append(
                {
                    "type": "unresolvable_relation",
                    "from": relation_input["from"],
                    "to": relation_input["to"],
                    "message": (
                        f'Cannot resolve "from" entity name "{relation_input["from"]}" to a UUID'
                    ),
                }
            )
            relations_skipped += 1
            continue
        to_id = mapping.get(relation_input["to"])
        if not to_id:
            errors.append(
                {
                    "type": "unresolvable_relation",
                    "from": relation_input["from"],
                    "to": relation_input["to"],
                    "message": (
                        f'Cannot resolve "to" entity name "{relation_input["to"]}" to a UUID'
                    ),
                }
            )
            relations_skipped += 1
            continue

        if store.read_relation(from_id, to_id, relation_input["relationType"]) is not None:
            relations_skipped += 1
            continue

        if dry_run:
            relations_created += 1
            continue
        try:
            store.create_relation(
                RelationCreate.model_validate(
                    {
                        "from": from_id,
                        "to": to_id,
                        "relationType": relation_input["relationType"],
                        "polarity": relation_input.get("polarity"),
                        "strength": relation_input.get("strength") or "moderate",
                        "evidence": relation_input.get("evidence"),
                    }
                )
            )
            relations_created += 1
        except Exception as exc:  # per-item creation error, collected not raised
            errors.append(
                {
                    "type": "creation_error",
                    "from": relation_input["from"],
                    "to": relation_input["to"],
                    "message": str(exc),
                }
            )

    return {
        "entitiesCreated": entities_created,
        "entitiesMerged": entities_merged,
        "relationsCreated": relations_created,
        "relationsSkipped": relations_skipped,
        "errors": [*parse_errors, *errors],
        "mapping": mapping,
    }


def _limit_result(message: str) -> dict[str, Any]:
    return {
        "entitiesCreated": 0,
        "entitiesMerged": 0,
        "relationsCreated": 0,
        "relationsSkipped": 0,
        "errors": [{"type": "validation_error", "message": message}],
        "mapping": {},
    }
