"""what-changed: replaying a span of the event log as a compact diff.

Exercised both directly against the operations handler (for the diffing
logic itself) and through the real registry dispatch (``run_handler``), since
the ``eventIds``/``causedBy`` receipt loop only proves itself end to end: a
mutating command's response names the events, and what-changed replays
exactly those ids back into the same diff.
"""

from __future__ import annotations

from theloom.cli.registry import run_handler
from theloom.operations.entity import CreateEntityInput, create_entity
from theloom.operations.receipts import WhatChangedInput, what_changed
from theloom.operations.relations import CreateRelationInput, create_relation
from theloom.store.multigraph import MultiGraph


def _create_entity(multi: MultiGraph, name: str, **overrides: object) -> dict[str, object]:
    doc: dict[str, object] = {"name": name, "entityType": "concept", "observations": []}
    doc.update(overrides)
    return create_entity(CreateEntityInput.model_validate(doc), multi)


# =============================================================================
# entity_created / entity_updated / entity_deleted diffing
# =============================================================================


def test_entity_created_diffs_against_nothing(multi: MultiGraph) -> None:
    a = _create_entity(multi, "Alpha", observations=["first"])
    result = what_changed(WhatChangedInput.model_validate({}), multi)
    rows = {row["field"]: row for row in result["items"] if row["entity"] == a["id"]}
    assert rows["name"]["old"] is None
    assert rows["name"]["new"] == "Alpha"
    assert rows["name"]["entityName"] == "Alpha"
    assert rows["observations"]["old"] is None
    assert rows["observations"]["new"] == ["first"]
    # bookkeeping fields never appear as diff rows
    assert "id" not in rows
    assert "created_at" not in rows
    assert "updated_at" not in rows


def test_entity_updated_diffs_only_changed_fields(multi: MultiGraph) -> None:
    from theloom.operations.entity import UpdateEntityInput, update_entity

    a = _create_entity(multi, "Alpha")
    update_entity(UpdateEntityInput.model_validate({"id": a["id"], "name": "Alpha Prime"}), multi)
    result = what_changed(WhatChangedInput.model_validate({}), multi)
    name_rows = [
        row
        for row in result["items"]
        if row["entity"] == a["id"]
        and row["field"] == "name"
        and row["eventType"] == "entity_updated"
    ]
    assert len(name_rows) == 1
    assert name_rows[0]["old"] == "Alpha"
    assert name_rows[0]["new"] == "Alpha Prime"
    # version/changeType/previousVersionId genuinely changed too and are real rows
    version_rows = [
        row
        for row in result["items"]
        if row["entity"] == a["id"]
        and row["field"] == "version"
        and row["eventType"] == "entity_updated"
    ]
    assert version_rows[0]["old"] == 1
    assert version_rows[0]["new"] == 2


def test_entity_hard_delete_diffs_every_field_to_none(multi: MultiGraph) -> None:
    from theloom.operations.entity import DeleteEntityInput, delete_entity

    a = _create_entity(multi, "Alpha", observations=["gone soon"])
    delete_entity(DeleteEntityInput.model_validate({"id": a["id"], "hard": True}), multi)
    result = what_changed(WhatChangedInput.model_validate({}), multi)
    rows = {row["field"]: row for row in result["items"] if row["entity"] == a["id"]}
    assert rows["name"]["old"] == "Alpha"
    assert rows["name"]["new"] is None
    assert rows["observations"]["old"] == ["gone soon"]
    assert rows["observations"]["new"] is None


def test_entity_soft_delete_is_a_status_field_change_not_an_erasure(multi: MultiGraph) -> None:
    from theloom.operations.entity import DeleteEntityInput, delete_entity

    a = _create_entity(multi, "Alpha")
    delete_entity(DeleteEntityInput.model_validate({"id": a["id"]}), multi)
    result = what_changed(WhatChangedInput.model_validate({}), multi)
    retraction_rows = [
        row
        for row in result["items"]
        if row["entity"] == a["id"] and row["eventType"] == "entity_retracted"
    ]
    status_rows = [row for row in retraction_rows if row["field"] == "status"]
    assert len(status_rows) == 1
    assert status_rows[0]["old"] is None  # unset means active
    assert status_rows[0]["new"] == "retracted"
    # the name is untouched by retraction, so it must not appear in this event's rows
    assert not any(row["field"] == "name" for row in retraction_rows)


# =============================================================================
# relation_created / relation_updated / relation_invalidated + names (desire 11)
# =============================================================================


def test_relation_created_diff_carries_from_to_names(multi: MultiGraph) -> None:
    a = _create_entity(multi, "Cause")
    b = _create_entity(multi, "Effect")
    create_relation(
        CreateRelationInput.model_validate(
            {
                "from": a["id"],
                "to": b["id"],
                "relationType": "causes",
                "polarity": "+",
                "strength": "strong",
                "evidence": None,
            }
        ),
        multi,
    )
    result = what_changed(WhatChangedInput.model_validate({}), multi)
    rows = [row for row in result["items"] if row["recordType"] == "relation"]
    assert rows, "expected at least one relation diff row"
    for row in rows:
        assert row["from"] == a["id"]
        assert row["to"] == b["id"]
        assert row["fromName"] == "Cause"
        assert row["toName"] == "Effect"
    relation_type_rows = [row for row in rows if row["field"] == "relationType"]
    assert relation_type_rows[0]["old"] is None
    assert relation_type_rows[0]["new"] == "causes"


def test_relation_invalidated_diffs_every_field_to_none(multi: MultiGraph) -> None:
    from theloom.operations.relations import DeleteRelationInput, delete_relation

    a = _create_entity(multi, "Cause")
    b = _create_entity(multi, "Effect")
    create_relation(
        CreateRelationInput.model_validate(
            {
                "from": a["id"],
                "to": b["id"],
                "relationType": "causes",
                "polarity": "+",
                "strength": "strong",
                "evidence": None,
            }
        ),
        multi,
    )
    delete_relation(DeleteRelationInput.model_validate({"from": a["id"], "to": b["id"]}), multi)
    result = what_changed(WhatChangedInput.model_validate({}), multi)
    removed = [
        row
        for row in result["items"]
        if row["recordType"] == "relation" and row["eventType"] == "relation_invalidated"
    ]
    strength_rows = [row for row in removed if row["field"] == "strength"]
    assert strength_rows[0]["old"] == "strong"
    assert strength_rows[0]["new"] is None


# =============================================================================
# Span selection: eventIds vs from/to range
# =============================================================================


def test_event_ids_replays_exactly_the_named_events_in_request_order(
    multi: MultiGraph,
) -> None:
    a = _create_entity(multi, "Alpha")
    b = _create_entity(multi, "Beta")
    all_events = what_changed(WhatChangedInput.model_validate({}), multi)
    a_event_id = next(row["eventId"] for row in all_events["items"] if row["entity"] == a["id"])
    b_event_id = next(row["eventId"] for row in all_events["items"] if row["entity"] == b["id"])
    result = what_changed(
        WhatChangedInput.model_validate({"eventIds": [b_event_id, a_event_id]}), multi
    )
    seen_ids = [row["eventId"] for row in result["items"]]
    assert seen_ids[0] == b_event_id
    assert seen_ids[-1] == a_event_id
    assert all(row["entity"] in (a["id"], b["id"]) for row in result["items"])


def test_unknown_event_ids_are_silently_skipped(multi: MultiGraph) -> None:
    result = what_changed(WhatChangedInput.model_validate({"eventIds": ["0-0"]}), multi)
    assert result == {"items": [], "count": 0}


def test_from_and_to_event_id_bound_a_contiguous_range(multi: MultiGraph) -> None:
    _create_entity(multi, "Alpha")
    b = _create_entity(multi, "Beta")
    _create_entity(multi, "Gamma")
    all_events = what_changed(WhatChangedInput.model_validate({}), multi)
    b_event_id = next(row["eventId"] for row in all_events["items"] if row["entity"] == b["id"])
    result = what_changed(
        WhatChangedInput.model_validate({"fromEventId": b_event_id, "toEventId": b_event_id}),
        multi,
    )
    assert all(row["entity"] == b["id"] for row in result["items"])
    assert result["items"]


# =============================================================================
# The envelope itself (desire 9)
# =============================================================================


def test_empty_graph_returns_the_uniform_empty_envelope(multi: MultiGraph) -> None:
    assert what_changed(WhatChangedInput.model_validate({}), multi) == {"items": [], "count": 0}


def test_count_matches_the_number_of_diff_rows(multi: MultiGraph) -> None:
    _create_entity(multi, "Alpha")
    result = what_changed(WhatChangedInput.model_validate({}), multi)
    assert result["count"] == len(result["items"])
    assert result["count"] > 0


# =============================================================================
# End to end through run_handler: eventIds receipt -> what-changed causedBy
# =============================================================================


def test_a_mutating_commands_receipt_replays_with_its_own_command_name(
    multi: MultiGraph,
) -> None:
    created = run_handler(
        "create-entity",
        {"name": "Alpha", "entityType": "concept", "observations": []},
        multi,
    )
    assert created["eventIds"]  # the write-receipt itself (desire 1)

    replay = run_handler("what-changed", {"eventIds": created["eventIds"]}, multi)
    assert replay["count"] > 0
    assert all(row["causedBy"] == "create-entity" for row in replay["items"])
    assert all(row["entity"] == created["id"] for row in replay["items"])


def test_what_changed_itself_is_read_only_and_earns_no_receipt(multi: MultiGraph) -> None:
    run_handler(
        "create-entity", {"name": "Alpha", "entityType": "concept", "observations": []}, multi
    )
    result = run_handler("what-changed", {}, multi)
    assert "eventIds" not in result
