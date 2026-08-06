"""The embedding state machine (``theloom.semantic.embedding_state``) — pure,
no docker: ``needs_embedding``, the transition-metadata builders, the
reconcile plan, and status counting.

The real machine is binary: ``None -> completed`` (a successful embed) or
``None -> error`` (a failed one). ``EmbeddingStatus.PENDING``/``PROCESSING``
named a queue-worker's states this one-shot CLI never occupies, so nothing
here ever produces them.
"""

from __future__ import annotations

from theloom.model import EmbeddingStatus
from theloom.semantic.embed import compute_content_hash
from theloom.semantic.embedding_state import (
    ReconcileAction,
    cleared_metadata,
    completed_metadata,
    error_metadata,
    needs_embedding,
    plan_reconcile,
    status_counts,
)

ENTITY = {"id": "e1", "name": "alpha", "entityType": "concept", "observations": ["obs"]}


def _completed(entity: dict) -> dict:
    doc = dict(entity)
    doc["embeddingStatus"] = "completed"
    doc["contentHash"] = compute_content_hash(entity)
    return doc


# =============================================================================
# needs_embedding
# =============================================================================


def test_needs_embedding_is_true_for_a_never_embedded_entity() -> None:
    assert needs_embedding(ENTITY) is True


def test_needs_embedding_is_false_once_completed_with_a_matching_hash() -> None:
    assert needs_embedding(_completed(ENTITY)) is False


def test_needs_embedding_is_true_when_content_changed_since_the_last_embed() -> None:
    doc = _completed(ENTITY)
    doc["observations"] = ["obs", "a new observation"]
    assert needs_embedding(doc) is True


def test_needs_embedding_is_true_for_a_prior_error() -> None:
    doc = dict(ENTITY)
    doc["embeddingStatus"] = "error"
    doc["embeddingError"] = "boom"
    assert needs_embedding(doc) is True


def test_needs_embedding_is_true_when_forced_even_if_already_completed() -> None:
    assert needs_embedding(_completed(ENTITY), force=True) is True


# =============================================================================
# Transition-metadata builders (the writers act on these verbatim)
# =============================================================================


def test_completed_metadata_carries_the_four_key_tuple() -> None:
    meta = completed_metadata("hash-123")
    assert meta["embeddingStatus"] == EmbeddingStatus.COMPLETED.value
    assert meta["contentHash"] == "hash-123"
    assert meta["lastEmbeddedAt"]  # non-empty ISO timestamp
    assert meta["embeddingVersion"]


def test_error_metadata_carries_the_message() -> None:
    meta = error_metadata("model unavailable")
    assert meta == {
        "embeddingStatus": EmbeddingStatus.ERROR.value,
        "embeddingError": "model unavailable",
    }


def test_cleared_metadata_nulls_every_embedding_key() -> None:
    assert cleared_metadata() == {
        "embeddingStatus": None,
        "contentHash": None,
        "lastEmbeddedAt": None,
        "embeddingVersion": None,
    }


# =============================================================================
# status_counts: the embedding-status command's counting, with only the
# statuses the machine actually produces
# =============================================================================


def test_status_counts_has_no_pending_or_processing_keys() -> None:
    counts = status_counts([ENTITY])
    assert "pending" not in counts
    assert "processing" not in counts
    assert counts == {"completed": 0, "error": 0, "none": 1}


def test_status_counts_tallies_each_known_status() -> None:
    entities = [
        _completed(dict(ENTITY, id="e1")),
        dict(ENTITY, id="e2", embeddingStatus="error"),
        dict(ENTITY, id="e3"),
    ]
    assert status_counts(entities) == {"completed": 1, "error": 1, "none": 1}


# =============================================================================
# plan_reconcile: both repair directions, computed without touching a store
# =============================================================================


def test_plan_reconcile_flags_completed_status_with_no_vector_for_clearing() -> None:
    entity = _completed(dict(ENTITY, id="e1"))
    actions = plan_reconcile([entity], vector_ids=set())
    assert actions == [ReconcileAction(entity_id="e1", kind="clear_status")]


def test_plan_reconcile_flags_a_vector_with_no_completed_status_for_marking() -> None:
    entity = dict(ENTITY, id="e1")
    actions = plan_reconcile([entity], vector_ids={"e1"})
    assert actions == [ReconcileAction(entity_id="e1", kind="mark_completed")]


def test_plan_reconcile_is_silent_when_status_and_vector_already_agree() -> None:
    entity = _completed(dict(ENTITY, id="e1"))
    assert plan_reconcile([entity], vector_ids={"e1"}) == []
    bare = dict(ENTITY, id="e2")
    assert plan_reconcile([bare], vector_ids=set()) == []
