"""Shared input-model machinery for command operations.

CommandInput uses strict object schemas: unknown keys are
stripped, known keys validated. UuidStr enforces a strict UUID string so
malformed ids fail with VALIDATION_ERROR before any store work.

``resolve_entity_ref`` is the one name-first addressing path: every
entity-addressed read takes either an id or a name, and a name resolves through
the store's server-side filtered read — never a full scan.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Annotated

import pydantic
from pydantic import AfterValidator

from theloom.errors import NotFoundError, ValidationError
from theloom.extraction.encoding import FILE_PATH_PREFIX, find_observation
from theloom.model import Entity, EntityFilter, EntityStatus, LoomModel

if TYPE_CHECKING:
    from theloom.store.falkor import FalkorGraphStore

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _validate_uuid(value: str) -> str:
    if not _UUID_RE.match(value):
        raise ValueError(f"Invalid uuid: {value!r}")
    return value


UuidStr = Annotated[str, AfterValidator(_validate_uuid)]


class CommandInput(LoomModel):
    """Command input base: unknown keys stripped by strict object schemas.

    ``world`` (branchable belief worlds, desire 12 / Part 5): every command
    gains this optional field structurally, the same way ``graph`` is
    declared per-command today — but unlike ``graph``, ``world`` needs no
    per-command handler change at all. ``theloom.cli.registry.run_handler``
    opens a ``theloom.store.worldctx.active(params.world)`` scope around the
    handler dispatch, and ``theloom.store.multigraph.MultiGraph.get_store``
    — the one place any command ever gets a store instance — reads it from
    there. A command whose own handler never mentions ``world`` still
    resolves it correctly the moment it calls ``multi.get_store(params.
    graph)``, which every graph-touching handler already does. Commands with
    no graph at all (symbolic math, LLM synthesis) simply never read the
    field — present in their schema for uniformity, inert in their handler.
    Defaults to ``"main"``, never mutable from inside a fork.

    ``exclude=True``: ``CommandInput`` is also the base every *nested* value
    object (``ConfidenceArg``, ``ProvenanceArg``, a ``RelationItem`` inside a
    batch, ...) is built on, and several of those get wholesale
    ``.model_dump()``'d into a stricter downstream model (``theloom.model``'s
    ``extra="forbid"`` wire shapes) that has never heard of ``world`` and
    never should — a nested value object doesn't carry its own world, the
    command around it does. Excluding it from serialization means every
    existing ``.model_dump()`` call site — and there are dozens, none of them
    written with this field in mind — stays exactly as it was; the field is
    still validated on input and still readable via ``params.world``, just
    never written back out.
    """

    model_config = pydantic.ConfigDict(populate_by_name=True, extra="ignore")

    world: str | None = pydantic.Field(
        default=None,
        exclude=True,
        description="The belief world to read/write in (a worldId from fork-world, or omitted "
        "for 'main'). Reads project the fork point plus the world's own writes; writes land "
        "only in the world's own segment -- main is never mutable from inside a fork.",
    )

    def provided(self, field: str) -> bool:
        """True iff the caller explicitly supplied the field (even as null) —
        the "explicitly set vs. absent" distinction. Accepts the field name or
        its wire alias (model_fields_set stores field names)."""
        if field in self.model_fields_set:
            return True
        for name, info in type(self).model_fields.items():
            if info.alias == field:
                return name in self.model_fields_set
        return False


# =============================================================================
# Name-first addressing
# =============================================================================

_MAX_CANDIDATES = 25
# Name addressing must reach exactly what id addressing reaches: the id-matched
# reads carry no status predicate, and status transitions are ordinary
# event-sourced state, so the resolver looks at every status and only *prefers*
# active when a name matches both a live and a retired entity.
_ALL_STATUSES = [status.value for status in EntityStatus]


def _file_path_hint(entity: Entity) -> str:
    """The entity's File path observation, if it has one — the cheapest
    disambiguator for code symbols, which collide by name constantly."""
    observation = find_observation(entity.observations, FILE_PATH_PREFIX)
    return f" {observation}" if observation is not None else ""


def _candidate_line(entity: Entity) -> str:
    return f"{entity.name} [{entity.entity_type.value}] id={entity.id}{_file_path_hint(entity)}"


def _ambiguous(name: str, candidates: list[Entity]) -> ValidationError:
    shown = candidates[:_MAX_CANDIDATES]
    lines = "\n".join(_candidate_line(entity) for entity in shown)
    suffix = ""
    if len(candidates) > len(shown):
        suffix = f"\n… and {len(candidates) - len(shown)} more."
    return ValidationError(
        f"Ambiguous entity name '{name}': {len(candidates)} entities match. "
        f"Retry with one of these ids:\n{lines}{suffix}"
    )


def resolve_entity_ref(
    store: FalkorGraphStore,
    *,
    entity_id: str | None,
    name: str | None,
    id_field: str = "id",
    name_field: str = "name",
) -> str:
    """Resolve an entity reference to an id. Exactly one of ``entity_id`` /
    ``name`` must be supplied.

    A name resolves by exact (case-insensitive) match first; failing that, by
    case-insensitive substring; within either pool an active entity wins over a
    retired one. Otherwise more than one match is refused with a candidate
    listing rather than guessed, and no match is NOT_FOUND.

    A blank name counts as no name: an empty substring would match every
    entity, so it is refused as a missing argument, never run as a query.
    """
    return resolve_entity_ref_multi(
        [store], entity_id=entity_id, name=name, id_field=id_field, name_field=name_field
    )


def resolve_entity_ref_multi(
    stores: Sequence[FalkorGraphStore],
    *,
    entity_id: str | None,
    name: str | None,
    id_field: str = "id",
    name_field: str = "name",
) -> str:
    """``resolve_entity_ref`` over one or more graph stores — the form for
    commands whose ``graph`` param accepts an array. Candidates are gathered
    per store through the same pushed-down filter (deduped by id, first store
    wins, mirroring the merged view), then judged by the same exact-match /
    active-preference / ambiguity rules a single store gets.
    """
    supplied_name = name if name is not None and name.strip() else None
    if (entity_id is None) == (supplied_name is None):
        raise ValidationError(
            f"Provide exactly one of '{id_field}' or '{name_field}' "
            "(an entity id, or a name to resolve)."
        )
    if entity_id is not None:
        return entity_id
    assert supplied_name is not None
    name = supplied_name

    # EntityFilter.name is a case-insensitive substring match pushed down into
    # Cypher, so the exact-match pool is always a subset of this window.
    name_filter = EntityFilter.model_validate({"name": name, "statusFilter": _ALL_STATUSES})
    seen: set[str] = set()
    candidates: list[Entity] = []
    for store in stores:
        for entity in store.list_entities(name_filter):
            if entity.id not in seen:
                seen.add(entity.id)
                candidates.append(entity)
    lowered = name.lower()
    exact = [entity for entity in candidates if entity.name.lower() == lowered]
    pool = exact or candidates
    active = [entity for entity in pool if entity.effective_status is EntityStatus.ACTIVE]
    pool = active or pool
    if len(pool) == 1:
        return pool[0].id
    if not pool:
        raise NotFoundError(
            f"Entity not found with name: '{name}'. Use list-entities to see available entities."
        )
    raise _ambiguous(name, pool)
