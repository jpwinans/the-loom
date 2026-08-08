"""First-class session provenance tests.

A top-level optional ``session`` string on create-entity / create-relation
inputs (persisted on the wire doc), a ``session`` filter on list-entities /
list-relations, and session scoping on session-changelog. The legacy
``"subgraph: {sid}-{qid}"`` observation tag is accepted as a fallback for
entity session matching (exact ``subgraph: {sid}`` or ``subgraph: {sid}-...``
prefix; no false prefix matches across distinct sids).
"""

from __future__ import annotations

from typing import Any

import pytest

from theloom.errors import LoomError
from theloom.operations.entity import (
    CreateEntityInput,
    ListEntitiesInput,
    create_entity,
    list_entities,
)
from theloom.operations.epistemic import SessionChangelogInput, session_changelog
from theloom.operations.relations import (
    CreateRelationInput,
    ListRelationsInput,
    create_relation,
    list_relations,
)
from theloom.store.multigraph import MultiGraph

EPOCH = "1970-01-01T00:00:00.000Z"


def make_entity(multi: MultiGraph, name: str, **overrides: object) -> dict[str, Any]:
    base: dict[str, object] = {
        "name": name,
        "entityType": "concept",
        "observations": [f"observation about {name}"],
    }
    base.update(overrides)
    result = create_entity(CreateEntityInput.model_validate(base), multi)
    assert isinstance(result, dict)
    return result


def make_relation(
    multi: MultiGraph, from_id: str, to_id: str, **overrides: object
) -> dict[str, Any]:
    base: dict[str, object] = {
        "from": from_id,
        "to": to_id,
        "relationType": "supports",
        "polarity": None,
        "strength": "moderate",
        "evidence": None,
    }
    base.update(overrides)
    return create_relation(CreateRelationInput.model_validate(base), multi)


# =============================================================================
# session on create inputs
# =============================================================================


def test_create_entity_persists_session(multi: MultiGraph) -> None:
    entity = make_entity(multi, "Tagged", session="session-1")
    assert entity["session"] == "session-1"
    stored = multi.get_store().read_entity_doc(entity["id"])
    assert stored is not None
    assert stored["session"] == "session-1"


def test_create_entity_without_session_omits_the_key(multi: MultiGraph) -> None:
    entity = make_entity(multi, "Untagged")
    assert "session" not in entity


def test_create_relation_persists_session(multi: MultiGraph) -> None:
    a = make_entity(multi, "A")
    b = make_entity(multi, "B")
    relation = make_relation(multi, a["id"], b["id"], session="session-1")
    assert relation["session"] == "session-1"
    untagged = make_relation(multi, b["id"], a["id"])
    assert "session" not in untagged


# =============================================================================
# session filter on list-entities / list-relations
# =============================================================================


def test_list_entities_filters_by_session(multi: MultiGraph) -> None:
    tagged = make_entity(multi, "In Session", session="session-1")
    make_entity(multi, "Other Session", session="session-2")
    make_entity(multi, "No Session")
    results = list_entities(ListEntitiesInput.model_validate({"session": "session-1"}), multi)
    assert [e["id"] for e in results["items"]] == [tagged["id"]]


def test_list_entities_session_matches_legacy_subgraph_tag(multi: MultiGraph) -> None:
    exact = make_entity(multi, "Legacy Exact", observations=["subgraph: sid"])
    prefixed = make_entity(multi, "Legacy Prefixed", observations=["subgraph: sid-q3"])
    make_entity(multi, "Legacy Other Sid", observations=["subgraph: sid2-q1"])
    make_entity(multi, "Unrelated", observations=["subgraph unrelated"])
    results = list_entities(ListEntitiesInput.model_validate({"session": "sid"}), multi)
    assert {e["id"] for e in results["items"]} == {exact["id"], prefixed["id"]}


def test_list_entities_session_combines_with_other_filters(multi: MultiGraph) -> None:
    claim = make_entity(multi, "A Claim", entityType="claim", session="session-1")
    make_entity(multi, "A Concept", entityType="concept", session="session-1")
    results = list_entities(
        ListEntitiesInput.model_validate({"session": "session-1", "entityType": "claim"}),
        multi,
    )
    assert [e["id"] for e in results["items"]] == [claim["id"]]


def test_list_relations_filters_by_session(multi: MultiGraph) -> None:
    a = make_entity(multi, "A")
    b = make_entity(multi, "B")
    tagged = make_relation(multi, a["id"], b["id"], session="session-1")
    make_relation(multi, b["id"], a["id"], session="session-2")
    make_relation(multi, a["id"], b["id"], relationType="related_to")
    results = list_relations(ListRelationsInput.model_validate({"session": "session-1"}), multi)
    assert [r["id"] for r in results["items"]] == [tagged["id"]]


# =============================================================================
# session-changelog session scoping
# =============================================================================


def test_session_changelog_scopes_to_a_session(multi: MultiGraph) -> None:
    in_session = make_entity(multi, "In Session", session="session-1")
    make_entity(multi, "Out Of Session", session="session-2")
    legacy = make_entity(multi, "Legacy Tagged", observations=["subgraph: session-1-q2"])
    a = make_entity(multi, "A", session="session-1")
    tagged_relation = make_relation(multi, a["id"], in_session["id"], session="session-1")
    make_relation(multi, in_session["id"], a["id"], session="session-2")

    result = session_changelog(
        SessionChangelogInput.model_validate({"session": "session-1"}), multi
    )
    assert result["session"] == "session-1"
    assert result["since"] == EPOCH
    created_ids = {e["id"] for e in result["entities"]["created"]}
    assert created_ids == {in_session["id"], legacy["id"], a["id"]}
    assert [r["id"] for r in result["relations"]["created"]] == [tagged_relation["id"]]
    assert result["totals"]["entities"]["created"] == 3
    assert result["totals"]["relations"]["created"] == 1


def test_session_changelog_session_combines_with_since(multi: MultiGraph) -> None:
    early = make_entity(multi, "Early", session="session-1")
    cutoff = early["created_at"]
    # Anything created strictly before the cutoff is out of the window.
    result = session_changelog(
        SessionChangelogInput.model_validate(
            {"session": "session-1", "since": "2999-01-01T00:00:00.000Z"}
        ),
        multi,
    )
    assert result["entities"]["created"] == []
    assert result["totals"]["total"] == 0
    windowed = session_changelog(
        SessionChangelogInput.model_validate({"session": "session-1", "since": cutoff}), multi
    )
    assert [e["id"] for e in windowed["entities"]["created"]] == [early["id"]]


def test_session_scoped_changelog_does_not_advance_postmortem_timestamp(
    multi: MultiGraph,
) -> None:
    make_entity(multi, "Anything", session="session-1")
    session_changelog(SessionChangelogInput.model_validate({"session": "session-1"}), multi)
    assert multi.get_store().get_metadata("lastPostmortemTimestamp") is None


def test_session_changelog_without_session_still_requires_since(multi: MultiGraph) -> None:
    with pytest.raises(LoomError) as excinfo:
        session_changelog(SessionChangelogInput.model_validate({}), multi)
    assert excinfo.value.code == "VALIDATION_ERROR"


def test_unscoped_changelog_behavior_is_unchanged(multi: MultiGraph) -> None:
    entity = make_entity(multi, "Plain")
    result = session_changelog(SessionChangelogInput.model_validate({"since": EPOCH}), multi)
    assert "session" not in result
    assert [e["id"] for e in result["entities"]["created"]] == [entity["id"]]
    assert multi.get_store().get_metadata("lastPostmortemTimestamp") is not None
