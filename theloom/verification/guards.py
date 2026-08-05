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

Default relation guards: CAUSAL_MISSING_POLARITY (post-inference), the mirror
NON_CAUSAL_POLARITY (structural/epistemic types carry no polarity), SELF_LOOP,
ORPHAN_RELATION_FROM/TO (an endpoint that does not exist — or that exists only
as a retracted doc, which is the same thing for attachment purposes) — all
error severity. The endpoint verdict is exported as :func:`endpoint_error` so
the batch create path, which prefetches statuses instead of reading endpoints
one at a time, enforces exactly the same rule. The polarity partition is
enforced on every write path (create, batch create, update, bulk-import) and
reported on the read side by checks.guard_non_causal_polarity, which shares
this module's message.
"""

from __future__ import annotations

from theloom.model import CAUSAL_RELATION_TYPES, EntityFilter, EntityStatus
from theloom.store.falkor import FalkorGraphStore
from theloom.verification.checks import non_causal_polarity_error

__all__ = [
    "endpoint_error",
    "entity_gate_warnings",
    "non_causal_polarity_error",
    "relation_gate_errors",
]


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
    if relation_type in CAUSAL_RELATION_TYPES:
        if polarity not in ("+", "-"):
            errors.append(
                f"Causal relation type '{relation_type}' requires polarity ('+' or '-'), "
                f"got: {polarity if polarity is not None else 'undefined'}"
            )
    elif polarity is not None:
        errors.append(non_causal_polarity_error(relation_type, polarity))
    if from_id == to_id:
        errors.append(
            f"Relation cannot reference the same entity as source and target: '{from_id}'"
        )
    errors.extend(_endpoint_errors(store, "Source", from_id))
    errors.extend(_endpoint_errors(store, "Target", to_id))
    return errors


def endpoint_error(role: str, entity_id: str, status: EntityStatus | None) -> str | None:
    """ORPHAN_RELATION_FROM/TO for one endpoint, from its effective status
    (``None`` when no such entity exists) — ``None`` when it is attachable.

    The verdict lives here, taking a status rather than a store, so both arities
    of the same operation reach it: create-relation reads one endpoint at a
    time, create-relations prefetches every id in the target graph, and neither
    may be more permissive than the other.

    A retracted entity is not orphaned but is not attachable either: deletion
    invalidates rather than erases, so its doc still reads back, while
    retraction closed out every edge it had. Attaching a new one would recreate
    exactly the state ``checks.retracted_isolated`` reports as a violation, from
    a sequence of commands that each reported success — so the gate refuses it
    here, where the refusal is a typed error.
    """
    if status is None:
        return f"{role} entity '{entity_id}' does not exist"
    if status == EntityStatus.RETRACTED:
        return f"{role} entity '{entity_id}' is retracted and cannot be a relation endpoint"
    return None


def _endpoint_errors(store: FalkorGraphStore, role: str, entity_id: str) -> list[str]:
    entity = store.read_entity(entity_id)
    error = endpoint_error(role, entity_id, entity.effective_status if entity is not None else None)
    return [error] if error is not None else []
