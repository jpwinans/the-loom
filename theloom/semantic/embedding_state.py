"""The embedding state machine — one owner for what "needs embedding" means,
how a transition gets written, and how a status/vector divergence is repaired.

The real machine is binary: ``None -> completed`` (an embed that landed a
vector) or ``None -> error`` (one that raised). ``EmbeddingStatus`` used to
also declare ``PENDING``/``PROCESSING`` for a queue-worker this one-shot CLI
never had; nothing here writes them, so they were removed from the model
(see :mod:`theloom.model`) rather than left as a state no code could reach.

Every caller that reads or writes ``embeddingStatus``/``contentHash``/
``lastEmbeddedAt``/``embeddingVersion`` goes through this module instead of
composing the 4-key tuple or the hash-skip predicate itself — that duplication
(each written out twice, independently, in the pre-refactor
``operations/semantic.py``) is exactly the bug class a single owner prevents.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from theloom.model import EmbeddingStatus
from theloom.semantic.embed import EMBEDDING_VERSION, compute_content_hash
from theloom.timeutil import iso_now

Doc = dict[str, Any]

# The only statuses status_counts ever zero-inits: the two the machine can
# reach, plus "none" for an entity that was never embedded. Matches
# EmbeddingStatus minus PENDING/PROCESSING.
KNOWN_STATUSES: tuple[str, ...] = (
    EmbeddingStatus.COMPLETED.value,
    EmbeddingStatus.ERROR.value,
    "none",
)


class SupportsEntityUpdate(Protocol):
    """The slice of the store a transition writer needs."""

    def update_entity(self, entity_id: str, updates: dict[str, Any]) -> Any: ...


# =============================================================================
# The predicate
# =============================================================================


def needs_embedding(entity: Doc, *, force: bool = False) -> bool:
    """Whether ``entity`` should be (re-)embedded.

    True when forced, never successfully embedded (no status, or a prior
    error), or when the content that would be embedded has changed since the
    last completed embed (hash mismatch). False only for a completed embed
    whose content hash still matches — the skip case.
    """
    if force:
        return True
    if entity.get("embeddingStatus") != EmbeddingStatus.COMPLETED.value:
        return True
    return entity.get("contentHash") != compute_content_hash(entity)


# =============================================================================
# Transition metadata (what a writer sets, decoupled from setting it)
# =============================================================================


def completed_metadata(content_hash: str) -> dict[str, Any]:
    """The 4-key tuple a successful embed writes."""
    return {
        "embeddingStatus": EmbeddingStatus.COMPLETED.value,
        "contentHash": content_hash,
        "lastEmbeddedAt": iso_now(),
        "embeddingVersion": EMBEDDING_VERSION,
    }


def error_metadata(message: str) -> dict[str, Any]:
    """What a failed embed writes: status plus the reason, nothing else."""
    return {
        "embeddingStatus": EmbeddingStatus.ERROR.value,
        "embeddingError": message,
    }


def cleared_metadata() -> dict[str, Any]:
    """Reconcile's "status without vector" repair: null every embedding key
    so the entity reads as never-embedded, matching what the store actually
    holds."""
    return {
        "embeddingStatus": None,
        "contentHash": None,
        "lastEmbeddedAt": None,
        "embeddingVersion": None,
    }


# =============================================================================
# Transition writers
# =============================================================================


def mark_completed(store: SupportsEntityUpdate, entity_id: str, content_hash: str) -> None:
    store.update_entity(entity_id, completed_metadata(content_hash))


def mark_error(store: SupportsEntityUpdate, entity_id: str, message: str) -> None:
    store.update_entity(entity_id, error_metadata(message))


def clear_status(store: SupportsEntityUpdate, entity_id: str) -> None:
    store.update_entity(entity_id, cleared_metadata())


# =============================================================================
# Reconcile: plan (pure) + apply (needs a store)
# =============================================================================


@dataclass(frozen=True)
class ReconcileAction:
    """One entity's status disagrees with whether it actually has a vector."""

    entity_id: str
    kind: str  # "clear_status" | "mark_completed"


def plan_reconcile(entities: list[Doc], vector_ids: set[str]) -> list[ReconcileAction]:
    """Compute the reconcile plan without touching a store: for each entity,
    does its recorded status agree with whether ``vector_ids`` says it has a
    vector? Two ways to disagree, both actionable:

    - status says completed, no vector -> ``clear_status`` (null the tuple).
    - a vector exists, status isn't completed -> ``mark_completed``.
    """
    actions: list[ReconcileAction] = []
    for entity in entities:
        has_vector = entity["id"] in vector_ids
        status_completed = entity.get("embeddingStatus") == EmbeddingStatus.COMPLETED.value
        if status_completed and not has_vector:
            actions.append(ReconcileAction(entity["id"], "clear_status"))
        elif not status_completed and has_vector:
            actions.append(ReconcileAction(entity["id"], "mark_completed"))
    return actions


def apply_reconcile_action(
    store: SupportsEntityUpdate, action: ReconcileAction, entity: Doc
) -> None:
    """Write one planned action. ``entity`` is the same doc the plan was
    computed from — its content hash feeds a ``mark_completed`` write."""
    if action.kind == "clear_status":
        clear_status(store, action.entity_id)
    else:
        mark_completed(store, action.entity_id, compute_content_hash(entity))


# =============================================================================
# Status counting (embedding-status's command output)
# =============================================================================


def status_counts(entities: list[Doc]) -> dict[str, int]:
    """Tally entities by ``embeddingStatus`` (missing -> ``"none"``), zero-
    initialized over :data:`KNOWN_STATUSES` — the statuses the machine can
    actually produce, not every member the model ever declared."""
    counts: dict[str, int] = dict.fromkeys(KNOWN_STATUSES, 0)
    for entity in entities:
        status = entity.get("embeddingStatus") or "none"
        counts[status] = counts.get(status, 0) + 1
    return counts
