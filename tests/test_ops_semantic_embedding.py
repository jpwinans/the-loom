"""The embedding commands, honest under the state machine.

flush-pending-embeddings/retry-failed-embeddings/list-dead-letters used to
return hard-coded empty-queue constants (there is no in-memory queue in a
one-shot CLI). They now act on real entity state through
theloom.semantic.embedding_state: flush embeds everything needs_embedding,
retry re-embeds status=error entities, dead-letters lists status=error
entities with their embeddingError. embedding-reconcile repairs both
directions a recorded status can diverge from an actual vector. And
embedding-status's counts no longer carry the unreachable pending/processing
keys the state machine never writes (a deliberate output-shape shrink).
"""

from __future__ import annotations

import pytest

from tests.fakes import FailingEmbedder, FakeEmbedder
from theloom.model import EntityCreate
from theloom.operations.semantic import (
    EmbeddingReconcileInput,
    EmbedEntityInput,
    GraphArgInput,
    embed_entity,
    embedding_reconcile,
    embedding_status,
    flush_pending_embeddings,
    list_dead_letters,
    retry_failed_embeddings,
)
from theloom.store.multigraph import MultiGraph


def _create(multi: MultiGraph, name: str) -> str:
    entity = multi.get_store().create_entity(
        EntityCreate.model_validate({"name": name, "entityType": "concept", "observations": []})
    )
    return entity.id


# =============================================================================
# embedding-status: no pending/processing keys (the shrink is the point)
# =============================================================================


def test_embedding_status_counts_have_no_pending_or_processing_keys(multi: MultiGraph) -> None:
    _create(multi, "bare")
    result = embedding_status(GraphArgInput(), multi)
    assert set(result["counts"]) == {"completed", "error", "none", "total"}


def test_embedding_status_tallies_completed_and_error(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "theloom.operations.semantic.get_embedder", lambda: FakeEmbedder([1.0, 0.0, 0.0])
    )
    ok_id = _create(multi, "ok")
    bad_id = _create(multi, "bad")
    _create(multi, "untouched")
    embed_entity(EmbedEntityInput.model_validate({"id": ok_id}), multi)
    monkeypatch.setattr("theloom.operations.semantic.get_embedder", lambda: FailingEmbedder())
    embed_entity(EmbedEntityInput.model_validate({"id": bad_id}), multi)

    result = embedding_status(GraphArgInput(), multi)
    assert result["counts"] == {"completed": 1, "error": 1, "none": 1, "total": 3}


# =============================================================================
# flush-pending-embeddings: embeds everything needs_embedding
# =============================================================================


def test_flush_pending_embeddings_embeds_every_unembedded_entity(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    embedder = FakeEmbedder([1.0, 0.0, 0.0])
    monkeypatch.setattr("theloom.operations.semantic.get_embedder", lambda: embedder)
    _create(multi, "a")
    _create(multi, "b")

    result = flush_pending_embeddings(GraphArgInput(), multi)

    assert result["total"] == 2
    assert result["completed"] == 2
    assert embedder.document_calls == 2
    assert embedding_status(GraphArgInput(), multi)["counts"]["completed"] == 2


def test_flush_pending_embeddings_skips_an_already_completed_unchanged_entity(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    embedder = FakeEmbedder([1.0, 0.0, 0.0])
    monkeypatch.setattr("theloom.operations.semantic.get_embedder", lambda: embedder)
    stable_id = _create(multi, "stable")
    embed_entity(EmbedEntityInput.model_validate({"id": stable_id}), multi)
    assert embedder.document_calls == 1

    result = flush_pending_embeddings(GraphArgInput(), multi)

    assert result["total"] == 0  # nothing needed embedding
    assert embedder.document_calls == 1  # no redundant re-embed


# =============================================================================
# retry-failed-embeddings: re-embeds status=error entities, nothing else
# =============================================================================


def test_retry_failed_embeddings_only_touches_error_status_entities(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("theloom.operations.semantic.get_embedder", lambda: FailingEmbedder())
    failed_id = _create(multi, "failed")
    embed_entity(EmbedEntityInput.model_validate({"id": failed_id}), multi)
    _create(multi, "never-tried")

    good_embedder = FakeEmbedder([1.0, 0.0, 0.0])
    monkeypatch.setattr("theloom.operations.semantic.get_embedder", lambda: good_embedder)

    result = retry_failed_embeddings(GraphArgInput(), multi)

    assert result["retriedCount"] == 1
    assert result["results"]["completed"] == 1
    counts = embedding_status(GraphArgInput(), multi)["counts"]
    assert counts["completed"] == 1
    assert counts["error"] == 0
    assert counts["none"] == 1  # the never-tried entity is untouched


# =============================================================================
# list-dead-letters: status=error entities with their embeddingError
# =============================================================================


def test_list_dead_letters_reports_error_entities_with_their_reason(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("theloom.operations.semantic.get_embedder", lambda: FailingEmbedder())
    failed_id = _create(multi, "doomed")
    embed_entity(EmbedEntityInput.model_validate({"id": failed_id}), multi)
    _create(multi, "untouched")

    result = list_dead_letters(GraphArgInput(), multi)

    assert result["count"] == 1
    assert len(result["items"]) == 1
    entry = result["items"][0]
    assert entry["entityId"] == failed_id
    assert entry["embeddingError"] == "embedding backend unavailable"


def test_list_dead_letters_is_empty_with_no_failures(multi: MultiGraph) -> None:
    _create(multi, "fine")
    assert list_dead_letters(GraphArgInput(), multi) == {"items": [], "count": 0}


# =============================================================================
# embedding-reconcile: both repair directions, through the command seam
# =============================================================================


def test_reconcile_dry_run_reports_without_writing(multi: MultiGraph) -> None:
    store = multi.get_store()
    ghost_id = _create(multi, "ghost")  # status completed, no vector
    store.update_entity(
        ghost_id,
        {
            "embeddingStatus": "completed",
            "contentHash": "stale-hash",
            "lastEmbeddedAt": "2020-01-01T00:00:00.000Z",
            "embeddingVersion": "some-old-model",
        },
    )
    orphan_id = _create(multi, "orphan")  # vector, no status
    store.set_entity_vector(orphan_id, [1.0, 0.0, 0.0])

    result = embedding_reconcile(EmbeddingReconcileInput.model_validate({"dryRun": True}), multi)

    assert result["dryRun"] is True
    assert result["entitiesScanned"] == 2
    assert result["statusFixedMissingVector"] == 1
    assert result["statusFixedHasVector"] == 1
    # nothing written: the entities are exactly as seeded
    entities = {e.name: e for e in store.list_entities()}
    assert entities["ghost"].embedding_status == "completed"
    assert entities["orphan"].embedding_status is None


def test_reconcile_clears_a_completed_status_with_no_vector(multi: MultiGraph) -> None:
    store = multi.get_store()
    ghost_id = _create(multi, "ghost")
    store.update_entity(
        ghost_id,
        {
            "embeddingStatus": "completed",
            "contentHash": "stale-hash",
            "lastEmbeddedAt": "2020-01-01T00:00:00.000Z",
            "embeddingVersion": "some-old-model",
        },
    )

    result = embedding_reconcile(EmbeddingReconcileInput.model_validate({"dryRun": False}), multi)

    assert result["statusFixedMissingVector"] == 1
    assert result["statusFixedHasVector"] == 0
    fixed = store.read_entity(ghost_id)
    assert fixed is not None
    assert fixed.embedding_status is None
    assert fixed.content_hash is None
    assert fixed.last_embedded_at is None
    assert fixed.embedding_version is None


def test_reconcile_marks_a_vector_with_no_status_as_completed(multi: MultiGraph) -> None:
    store = multi.get_store()
    orphan_id = _create(multi, "orphan")
    store.set_entity_vector(orphan_id, [1.0, 0.0, 0.0])

    result = embedding_reconcile(EmbeddingReconcileInput.model_validate({"dryRun": False}), multi)

    assert result["statusFixedHasVector"] == 1
    assert result["statusFixedMissingVector"] == 0
    fixed = store.read_entity(orphan_id)
    assert fixed is not None
    assert fixed.embedding_status == "completed"
    assert fixed.content_hash is not None
    assert fixed.last_embedded_at is not None


def test_reconcile_leaves_agreeing_entities_alone(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "theloom.operations.semantic.get_embedder", lambda: FakeEmbedder([1.0, 0.0, 0.0])
    )
    fine_id = _create(multi, "fine")
    embed_entity(EmbedEntityInput.model_validate({"id": fine_id}), multi)
    untouched_id = _create(multi, "never-embedded")

    result = embedding_reconcile(EmbeddingReconcileInput.model_validate({"dryRun": False}), multi)

    assert result["entitiesScanned"] == 2
    assert result["statusFixedMissingVector"] == 0
    assert result["statusFixedHasVector"] == 0
    assert multi.get_store().read_entity(untouched_id).embedding_status is None  # type: ignore[union-attr]
