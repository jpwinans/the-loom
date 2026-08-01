"""Mutation-gate guards.

The default gates ship here because their behavior is observable in
Entity/Relation CRUD output: entity-gate warnings are appended to created
entities' observations as ``[guard:CODE] message``, and relation-gate errors
block creation — including cross-graph relations, whose bridge branch is never
reached because the gate checks the resolved single store first. The full
verification surface is built on top of this module.

Default entity guards: confidence bounds and entity type are pre-validated by
the input models, so only the two warning guards can fire here:
OBSERVATIONS_REQUIRED and DUPLICATE_NAME. The duplicate check uses
listEntities({name}) — a *partial*, case-insensitive match.

Default relation guards: CAUSAL_MISSING_POLARITY (post-inference), SELF_LOOP,
ORPHAN_RELATION_FROM/TO — all error severity.
"""

from __future__ import annotations

from theloom.model import CAUSAL_RELATION_TYPES, EntityFilter
from theloom.store.falkor import FalkorGraphStore


def entity_gate_warnings(store: FalkorGraphStore, name: str, observations: list[str]) -> list[str]:
    """Warning observations for create-entity, in guard order."""
    warnings: list[str] = []
    if len(observations) == 0:
        warnings.append("[guard:OBSERVATIONS_REQUIRED] Entity must have at least one observation")
    existing = store.list_entities(EntityFilter.model_validate({"name": name}))
    if existing:
        warnings.append(
            f"[guard:DUPLICATE_NAME] An entity with name '{name}' already exists "
            f"(id: {existing[0].id})"
        )
    return warnings


def relation_gate_errors(
    store: FalkorGraphStore,
    from_id: str,
    to_id: str,
    relation_type: str,
    polarity: str | None,
) -> list[str]:
    """Error messages for create-relation, in guard order."""
    errors: list[str] = []
    if relation_type in CAUSAL_RELATION_TYPES and polarity not in ("+", "-"):
        errors.append(
            f"Causal relation type '{relation_type}' requires polarity ('+' or '-'), "
            f"got: {polarity if polarity is not None else 'undefined'}"
        )
    if from_id == to_id:
        errors.append(
            f"Relation cannot reference the same entity as source and target: '{from_id}'"
        )
    if store.read_entity(from_id) is None:
        errors.append(f"Source entity '{from_id}' does not exist")
    if store.read_entity(to_id) is None:
        errors.append(f"Target entity '{to_id}' does not exist")
    return errors
