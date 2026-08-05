"""trigger-status / process-triggers tests.

Both are registered CLI commands, but these tests exercise the operations in
`theloom.operations.reification` directly rather than through the CLI, so the
trigger-queue shapes (status and dequeue, plus the misc handlers) are pinned at
the ops layer where the queue behaviour actually lives.
"""

from __future__ import annotations

from theloom.operations.reification import (
    ProcessTriggersInput,
    TriggerStatusInput,
    process_triggers,
    trigger_status,
)
from theloom.store.multigraph import MultiGraph


def candidate(entity: str, component: str, score: float, recommendation: str) -> dict:
    return {
        "mutatedEntityId": entity,
        "mutatedGraphName": "default",
        "targetComponentId": component,
        "targetGraphName": "other",
        "structuralSimilarity": 0.8,
        "semanticDissimilarity": score / 0.8,
        "farAnalogyScore": score,
        "recommendation": recommendation,
    }


def test_trigger_status_fresh_graph_shape(multi: MultiGraph) -> None:
    assert trigger_status(TriggerStatusInput.model_validate({}), multi) == {
        "pendingCount": 0,
        "processedCount": 0,
        "maxPending": 50,
        "lastProcessed": "",
        "byRecommendation": {},
    }


def test_process_triggers_empty_queue_message(multi: MultiGraph) -> None:
    assert process_triggers(ProcessTriggersInput.model_validate({}), multi) == {
        "candidates": [],
        "message": "No pending trigger candidates",
    }


def test_process_triggers_dequeues_by_score_and_persists(multi: MultiGraph) -> None:
    store = multi.get_store()
    store.set_metadata(
        "trigger_queue",
        {
            "pending": [
                candidate("e1", "c1", 0.3, "marginal"),
                candidate("e2", "c2", 0.7, "run_full_pipeline"),
                candidate("e3", "c3", 0.5, "worth_investigating"),
            ],
            "processed": [],
            "maxPending": 50,
            "lastProcessed": "",
        },
    )
    status = trigger_status(TriggerStatusInput.model_validate({}), multi)
    assert status["pendingCount"] == 3
    assert status["byRecommendation"] == {
        "marginal": 1,
        "run_full_pipeline": 1,
        "worth_investigating": 1,
    }

    result = process_triggers(ProcessTriggersInput.model_validate({"limit": 2}), multi)
    assert result["dequeuedCount"] == 2
    assert result["remainingPending"] == 1
    assert [c["farAnalogyScore"] for c in result["candidates"]] == [0.7, 0.5]
    assert "message" not in result

    after = trigger_status(TriggerStatusInput.model_validate({}), multi)
    assert after["pendingCount"] == 1
    assert after["processedCount"] == 2
    assert after["lastProcessed"] != ""
