"""Pins reify-patterns' member->pattern lineage edges to `crystallized_from`.

CONTEXT.md's glossary (Reification entry) and theloom/model.py's
RelationType docstring both name `crystallized_from` as the reification
lineage relation. reify_patterns must emit that type, not `instance_of`
(which is a distinct, general-purpose structural relation also present in
RelationType).
"""

from __future__ import annotations

from theloom.model import EntityCreate, RelationCreate
from theloom.operations.reification import ReifyPatternsInput, reify_patterns
from theloom.store.multigraph import MultiGraph


def _seed_isolated_concepts(multi: MultiGraph, count: int, prefix: str = "C") -> None:
    store = multi.get_store()
    for i in range(count):
        store.create_entity(
            EntityCreate.model_validate(
                {"name": f"{prefix}{i}", "entityType": "concept", "observations": []}
            )
        )


def test_reify_patterns_creates_crystallized_from_edges(multi: MultiGraph) -> None:
    _seed_isolated_concepts(multi, 3)
    result = reify_patterns(
        ReifyPatternsInput.model_validate({"minOccurrences": 3, "dryRun": False}), multi
    )
    assert result["patternsCreated"] == 1
    pattern_id = result["patterns"][0]["patternEntityId"]

    store = multi.get_store()
    relations = [r.model_dump(by_alias=True, exclude_unset=True) for r in store.list_relations()]
    lineage_edges = [r for r in relations if r["to"] == pattern_id]
    assert len(lineage_edges) == 3
    assert all(r["relationType"] == "crystallized_from" for r in lineage_edges)
    assert not any(r["relationType"] == "instance_of" for r in lineage_edges)


def test_reify_patterns_idempotent_on_second_run(multi: MultiGraph) -> None:
    """Re-running reify-patterns must recognize the existing pattern via its
    fingerprint observation and skip, not duplicate — regardless of the
    lineage edge type used to link members to the pattern."""
    _seed_isolated_concepts(multi, 3)
    first = reify_patterns(
        ReifyPatternsInput.model_validate({"minOccurrences": 3, "dryRun": False}), multi
    )
    assert first["patternsCreated"] == 1

    second = reify_patterns(
        ReifyPatternsInput.model_validate({"minOccurrences": 3, "dryRun": False}), multi
    )
    assert second["patternsCreated"] == 0
    assert second["patternsSkipped"] == 1

    store = multi.get_store()
    pattern_entities = [e for e in store.list_entities() if e.entity_type.value == "pattern"]
    assert len(pattern_entities) == 1


def test_reify_patterns_recognizes_pre_fix_instance_of_lineage(multi: MultiGraph) -> None:
    """A pattern created before this change linked its members with
    `instance_of` (the old, since-fixed lineage type). Re-running reify on
    that graph must still recognize it via the `fingerprint: <hash>`
    observation marker and skip rather than duplicate it — idempotency is
    keyed on the observation, not the lineage edge type."""
    store = multi.get_store()
    _seed_isolated_concepts(multi, 3)

    dry_run = reify_patterns(
        ReifyPatternsInput.model_validate({"minOccurrences": 3, "dryRun": True}), multi
    )
    fingerprint = dry_run["patterns"][0]["fingerprint"]
    member_ids = dry_run["patterns"][0]["memberIds"]

    pattern_entity = store.create_entity(
        EntityCreate.model_validate(
            {
                "name": "Structural Motif: pre-fix",
                "entityType": "pattern",
                "observations": [f"fingerprint: {fingerprint}"],
            }
        )
    )
    for member_id in member_ids:
        store.create_relation(
            RelationCreate.model_validate(
                {
                    "from": member_id,
                    "to": pattern_entity.id,
                    "relationType": "instance_of",
                    "polarity": None,
                    "strength": "moderate",
                    "evidence": "pre-fix lineage edge",
                }
            )
        )

    result = reify_patterns(
        ReifyPatternsInput.model_validate({"minOccurrences": 3, "dryRun": False}), multi
    )
    assert result["patternsCreated"] == 0
    assert result["patternsSkipped"] == 1
    assert result["patterns"][0]["patternEntityId"] == pattern_entity.id

    pattern_entities = [e for e in store.list_entities() if e.entity_type.value == "pattern"]
    assert len(pattern_entities) == 1
