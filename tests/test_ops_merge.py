"""merge-entities operation tests.

The contract: one atomic transaction that unions the secondary's observations
into the primary (dedup), merges confidence (keep stronger) and provenance
(primary wins, secondary fills the gap), redirects every relation on the
secondary to the primary (both directions, skipping would-be self-loops and
duplicates), supersedes the secondary (status + supersedes relation — never a
hard delete), and emits one `entities_merged` event. Output is exactly
{primaryId, secondaryId, observationsMerged, relationsRedirected, dryRun}.
"""

from __future__ import annotations

from typing import Any

import pytest
from falkordb import FalkorDB
from redis import Redis

from theloom.cli.registry import COMMANDS
from theloom.errors import LoomError
from theloom.operations.entity import CreateEntityInput, create_entity
from theloom.operations.merge import MergeEntitiesInput, merge_entities
from theloom.operations.relations import CreateRelationInput, create_relation
from theloom.store.events import EventLog
from theloom.store.multigraph import MultiGraph

MISSING = "00000000-0000-4000-8000-000000000000"


@pytest.fixture()
def multi(db: FalkorDB, redis_client: Redis, namespace: str) -> MultiGraph:
    return MultiGraph(db, redis_client, default_graph="default", key_prefix=namespace)


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
    multi: MultiGraph, from_id: str, to_id: str, relation_type: str = "supports"
) -> dict[str, Any]:
    return create_relation(
        CreateRelationInput.model_validate(
            {
                "from": from_id,
                "to": to_id,
                "relationType": relation_type,
                "polarity": None,
                "strength": "moderate",
                "evidence": None,
            }
        ),
        multi,
    )


def merge(multi: MultiGraph, primary: str, secondary: str, **overrides: object) -> dict[str, Any]:
    base: dict[str, object] = {"primary": primary, "secondary": secondary}
    base.update(overrides)
    return merge_entities(MergeEntitiesInput.model_validate(base), multi)


# =============================================================================
# registration + output shape
# =============================================================================


def test_merge_entities_is_registered() -> None:
    descriptor = next(c for c in COMMANDS if c.name == "merge-entities")
    assert descriptor.category == "Entity Management"
    assert descriptor.input_model is MergeEntitiesInput


def test_output_shape_is_exactly_the_contract(multi: MultiGraph) -> None:
    primary = make_entity(multi, "Primary")
    secondary = make_entity(multi, "Secondary")
    result = merge(multi, primary["id"], secondary["id"])
    assert set(result) == {
        "primaryId",
        "secondaryId",
        "observationsMerged",
        "relationsRedirected",
        "dryRun",
    }
    assert result["primaryId"] == primary["id"]
    assert result["secondaryId"] == secondary["id"]
    assert result["dryRun"] is False


# =============================================================================
# observation union + confidence/provenance merge
# =============================================================================


def test_unions_observations_deduplicating_identical(multi: MultiGraph) -> None:
    primary = make_entity(multi, "Primary", observations=["shared", "primary-only"])
    secondary = make_entity(
        multi, "Secondary", observations=["shared", "secondary-only", "secondary-only"]
    )
    result = merge(multi, primary["id"], secondary["id"])
    assert result["observationsMerged"] == 1
    store = multi.get_store()
    merged = store.read_entity_doc(primary["id"])
    assert merged is not None
    assert merged["observations"] == ["shared", "primary-only", "secondary-only"]


def test_confidence_keeps_the_stronger_score(multi: MultiGraph) -> None:
    primary = make_entity(multi, "Primary", confidence={"score": 0.4, "basis": "single_source"})
    secondary = make_entity(
        multi, "Secondary", confidence={"score": 0.9, "basis": "multiple_sources"}
    )
    merge(multi, primary["id"], secondary["id"])
    merged = multi.get_store().read_entity_doc(primary["id"])
    assert merged is not None
    assert merged["confidence"]["score"] == 0.9
    assert merged["confidence"]["basis"] == "multiple_sources"


def test_confidence_kept_when_primary_is_stronger(multi: MultiGraph) -> None:
    primary = make_entity(multi, "Primary", confidence={"score": 0.9, "basis": "peer_reviewed"})
    secondary = make_entity(multi, "Secondary", confidence={"score": 0.3, "basis": "speculation"})
    merge(multi, primary["id"], secondary["id"])
    merged = multi.get_store().read_entity_doc(primary["id"])
    assert merged is not None
    assert merged["confidence"]["score"] == 0.9
    assert merged["confidence"]["basis"] == "peer_reviewed"


def test_confidence_adopted_when_primary_has_none(multi: MultiGraph) -> None:
    primary = make_entity(multi, "Primary")
    secondary = make_entity(multi, "Secondary", confidence={"score": 0.7, "basis": "inference"})
    merge(multi, primary["id"], secondary["id"])
    merged = multi.get_store().read_entity_doc(primary["id"])
    assert merged is not None
    assert merged["confidence"]["score"] == 0.7


def test_provenance_adopted_from_secondary_when_primary_has_none(multi: MultiGraph) -> None:
    provenance = {
        "sourceType": "document",
        "sourceId": None,
        "externalRef": "doi:10.1/xyz",
        "extractor": "test",
        "extractionMethod": "manual",
    }
    primary = make_entity(multi, "Primary")
    secondary = make_entity(multi, "Secondary", provenance=provenance)
    merge(multi, primary["id"], secondary["id"])
    merged = multi.get_store().read_entity_doc(primary["id"])
    assert merged is not None
    assert merged["provenance"]["externalRef"] == "doi:10.1/xyz"


def test_provenance_primary_wins_when_both_present(multi: MultiGraph) -> None:
    def prov(ref: str) -> dict[str, Any]:
        return {
            "sourceType": "document",
            "sourceId": None,
            "externalRef": ref,
            "extractor": "test",
            "extractionMethod": "manual",
        }

    primary = make_entity(multi, "Primary", provenance=prov("primary-ref"))
    secondary = make_entity(multi, "Secondary", provenance=prov("secondary-ref"))
    merge(multi, primary["id"], secondary["id"])
    merged = multi.get_store().read_entity_doc(primary["id"])
    assert merged is not None
    assert merged["provenance"]["externalRef"] == "primary-ref"


# =============================================================================
# relation redirection
# =============================================================================


def test_redirects_outgoing_and_incoming_relations(multi: MultiGraph) -> None:
    primary = make_entity(multi, "Primary")
    secondary = make_entity(multi, "Secondary")
    other_a = make_entity(multi, "Other A")
    other_b = make_entity(multi, "Other B")
    outgoing = make_relation(multi, secondary["id"], other_a["id"], "supports")
    incoming = make_relation(multi, other_b["id"], secondary["id"], "causes")

    result = merge(multi, primary["id"], secondary["id"])
    assert result["relationsRedirected"] == 2

    store = multi.get_store()
    redirected_out = store.read_relations(primary["id"], other_a["id"], "supports")
    assert len(redirected_out) == 1
    assert redirected_out[0].id == outgoing["id"]  # identity preserved
    redirected_in = store.read_relations(other_b["id"], primary["id"], "causes")
    assert len(redirected_in) == 1
    assert redirected_in[0].id == incoming["id"]
    # Nothing but the supersedes edge remains on the secondary.
    remaining = store.get_relations(secondary["id"], "both")
    assert [r.relation_type.value for r in remaining] == ["supersedes"]


def test_skips_relations_that_would_become_self_loops(multi: MultiGraph) -> None:
    primary = make_entity(multi, "Primary")
    secondary = make_entity(multi, "Secondary")
    between = make_relation(multi, secondary["id"], primary["id"], "related_to")

    result = merge(multi, primary["id"], secondary["id"])
    assert result["relationsRedirected"] == 0
    store = multi.get_store()
    # The would-be self-loop stays in place on the superseded secondary.
    kept = store.read_relations(secondary["id"], primary["id"], "related_to")
    assert [r.id for r in kept] == [between["id"]]
    assert store.read_relations(primary["id"], primary["id"]) == []


def test_skips_duplicates_already_present_on_primary(multi: MultiGraph) -> None:
    primary = make_entity(multi, "Primary")
    secondary = make_entity(multi, "Secondary")
    other = make_entity(multi, "Other")
    make_relation(multi, other["id"], primary["id"], "supports")
    duplicate = make_relation(multi, other["id"], secondary["id"], "supports")
    distinct = make_relation(multi, other["id"], secondary["id"], "contradicts")

    result = merge(multi, primary["id"], secondary["id"])
    assert result["relationsRedirected"] == 1

    store = multi.get_store()
    supports = store.read_relations(other["id"], primary["id"], "supports")
    assert len(supports) == 1  # no parallel duplicate created
    contradicts = store.read_relations(other["id"], primary["id"], "contradicts")
    assert [r.id for r in contradicts] == [distinct["id"]]
    # The skipped duplicate stays on the secondary.
    kept = store.read_relations(other["id"], secondary["id"], "supports")
    assert [r.id for r in kept] == [duplicate["id"]]


# =============================================================================
# supersession (never hard-delete)
# =============================================================================


def test_supersedes_secondary_with_relation_and_status(multi: MultiGraph) -> None:
    primary = make_entity(multi, "Primary")
    secondary = make_entity(multi, "Secondary")
    merge(multi, primary["id"], secondary["id"])

    store = multi.get_store()
    superseded = store.read_entity_doc(secondary["id"])
    assert superseded is not None  # never hard-deleted
    assert superseded["status"] == "superseded"
    assert superseded["statusReason"] == "duplicate"
    assert superseded["statusChangedAt"].endswith("Z")
    assert superseded["changeType"] == "merged"
    relations = store.read_relations(primary["id"], secondary["id"], "supersedes")
    assert len(relations) == 1
    assert relations[0].strength is not None and relations[0].strength.value == "strong"


def test_merge_bumps_versions_and_snapshots_history(multi: MultiGraph) -> None:
    primary = make_entity(multi, "Primary")
    secondary = make_entity(multi, "Secondary", observations=["extra detail"])
    merge(multi, primary["id"], secondary["id"])

    store = multi.get_store()
    merged = store.read_entity_doc(primary["id"])
    assert merged is not None
    assert merged["version"] == 2
    assert merged["changeType"] == "merged"
    # Bi-temporal: the pre-merge incarnation is still readable as of creation.
    as_of = store.read_entity_as_of(primary["id"], primary["updated_at"])
    assert as_of is not None
    assert as_of.observations == primary["observations"]


def test_merge_emits_one_entities_merged_event(
    multi: MultiGraph, redis_client: Redis, namespace: str
) -> None:
    primary = make_entity(multi, "Primary")
    secondary = make_entity(multi, "Secondary")
    other = make_entity(multi, "Other")
    make_relation(multi, secondary["id"], other["id"], "supports")

    merge(multi, primary["id"], secondary["id"])
    log = EventLog(redis_client, graph_name="default", key_prefix=namespace)
    events = [e for e in log.read_all() if e.type == "entities_merged"]
    assert len(events) == 1
    payload = events[0].payload
    assert payload["primary"]["id"] == primary["id"]
    assert payload["secondary"]["status"] == "superseded"
    assert len(payload["redirectedRelations"]) == 1
    assert payload["supersedesRelation"]["relationType"] == "supersedes"


# =============================================================================
# idempotency + dryRun
# =============================================================================


def test_merge_is_idempotent(multi: MultiGraph) -> None:
    primary = make_entity(multi, "Primary")
    secondary = make_entity(multi, "Secondary", observations=["from secondary"])
    other = make_entity(multi, "Other")
    make_relation(multi, secondary["id"], other["id"], "supports")

    first = merge(multi, primary["id"], secondary["id"])
    assert first["observationsMerged"] == 1
    assert first["relationsRedirected"] == 1

    second = merge(multi, primary["id"], secondary["id"])
    assert second["observationsMerged"] == 0
    assert second["relationsRedirected"] == 0

    store = multi.get_store()
    assert len(store.read_relations(primary["id"], secondary["id"], "supersedes")) == 1
    merged = store.read_entity_doc(primary["id"])
    assert merged is not None
    assert merged["observations"].count("from secondary") == 1
    assert merged["version"] == 2  # the no-op re-merge did not write


def test_dry_run_previews_without_mutating(
    multi: MultiGraph, redis_client: Redis, namespace: str
) -> None:
    primary = make_entity(multi, "Primary")
    secondary = make_entity(multi, "Secondary", observations=["preview me"])
    other = make_entity(multi, "Other")
    make_relation(multi, other["id"], secondary["id"], "enables")

    result = merge(multi, primary["id"], secondary["id"], dryRun=True)
    assert result == {
        "primaryId": primary["id"],
        "secondaryId": secondary["id"],
        "observationsMerged": 1,
        "relationsRedirected": 1,
        "dryRun": True,
    }

    store = multi.get_store()
    untouched_primary = store.read_entity_doc(primary["id"])
    assert untouched_primary is not None
    assert untouched_primary["observations"] == primary["observations"]
    untouched_secondary = store.read_entity_doc(secondary["id"])
    assert untouched_secondary is not None
    assert "status" not in untouched_secondary
    assert store.read_relations(primary["id"], secondary["id"], "supersedes") == []
    log = EventLog(redis_client, graph_name="default", key_prefix=namespace)
    assert all(e.type != "entities_merged" for e in log.read_all())


# =============================================================================
# validation + errors + graph scoping
# =============================================================================


def test_merging_entity_into_itself_is_a_validation_error(multi: MultiGraph) -> None:
    entity = make_entity(multi, "Only")
    with pytest.raises(LoomError) as excinfo:
        merge(multi, entity["id"], entity["id"])
    assert excinfo.value.code == "VALIDATION_ERROR"


def test_missing_primary_and_secondary_are_not_found(multi: MultiGraph) -> None:
    entity = make_entity(multi, "Exists")
    with pytest.raises(LoomError) as excinfo:
        merge(multi, MISSING, entity["id"])
    assert excinfo.value.code == "NOT_FOUND"
    with pytest.raises(LoomError) as excinfo:
        merge(multi, entity["id"], MISSING)
    assert excinfo.value.code == "NOT_FOUND"


def test_retracted_secondary_cannot_be_merged(multi: MultiGraph) -> None:
    primary = make_entity(multi, "Primary")
    secondary = make_entity(multi, "Secondary")
    multi.get_store().update_entity(secondary["id"], {"status": "retracted"})
    with pytest.raises(LoomError) as excinfo:
        merge(multi, primary["id"], secondary["id"])
    assert excinfo.value.code == "VALIDATION_ERROR"


def test_merge_scoped_to_a_named_graph(multi: MultiGraph) -> None:
    multi.create_graph("research")
    primary = make_entity(multi, "Primary", graph="research")
    secondary = make_entity(multi, "Secondary", graph="research")
    result = merge(multi, primary["id"], secondary["id"], graph="research")
    assert result["dryRun"] is False
    superseded = multi.get_store("research").read_entity_doc(secondary["id"])
    assert superseded is not None
    assert superseded["status"] == "superseded"
