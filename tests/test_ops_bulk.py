"""bulk-import tests.

Key semantics: entities are created via the STORE directly (no ops-layer
revision fields), keyed for idempotent dedup by name::entityType (merging
observations), relations reference entities by NAME and resolve against the
import batch plus existing graph entities (active preferred), existing
relations of the same type are skipped (other types between the same pair
import normally), dryRun creates nothing but reports counts, and JSONL
input parses line-by-line with per-line errors.
"""

from __future__ import annotations

import pytest
from falkordb import FalkorDB
from redis import Redis

from theloom.operations.bulk import BulkImportInput, bulk_import, parse_jsonl
from theloom.operations.entity import CreateEntityInput, create_entity
from theloom.store.multigraph import MultiGraph


@pytest.fixture()
def multi(db: FalkorDB, redis_client: Redis, namespace: str) -> MultiGraph:
    return MultiGraph(db, redis_client, default_graph="default", key_prefix=namespace)


def run(multi: MultiGraph, **doc: object) -> dict:
    return bulk_import(BulkImportInput.model_validate(doc), multi)


ENTITIES = [
    {"name": "Alpha", "entityType": "concept", "observations": ["first"]},
    {"name": "Beta", "entityType": "claim", "observations": ["second"]},
]
RELATIONS = [{"from": "Alpha", "to": "Beta", "relationType": "supports"}]


# =============================================================================
# parse_jsonl
# =============================================================================


def test_parse_jsonl_entities_relations_and_errors() -> None:
    text = "\n".join(
        [
            '{"type": "entity", "name": "X", "entityType": "concept", "observations": []}',
            "",
            '{"type": "relation", "from": "X", "to": "Y", "relationType": "supports"}',
            "{broken",
            '{"type": "mystery"}',
        ]
    )
    result = parse_jsonl(text)
    assert len(result["data"]["entities"]) == 1
    assert len(result["data"]["relations"]) == 1
    assert len(result["errors"]) == 2
    assert "Line 4: invalid JSON" in result["errors"][0]["message"]
    assert "Line 5: unknown type 'mystery'" in result["errors"][1]["message"]


# =============================================================================
# bulk_import
# =============================================================================


def test_import_creates_entities_and_relations_by_name(multi: MultiGraph) -> None:
    result = run(multi, entities=ENTITIES, relations=RELATIONS)
    assert result["entitiesCreated"] == 2
    assert result["entitiesMerged"] == 0
    assert result["relationsCreated"] == 1
    assert result["relationsSkipped"] == 0
    assert result["errors"] == []
    assert set(result["mapping"]) == {"Alpha", "Beta"}
    # bulk-created entities go through the store directly: no revision fields
    store = multi.get_store("default")
    entity = store.read_entity(result["mapping"]["Alpha"])
    assert entity is not None
    doc = entity.model_dump(by_alias=True, exclude_unset=True)
    assert "version" not in doc
    assert "changeType" not in doc


def test_reimport_is_idempotent_merge_and_skip(multi: MultiGraph) -> None:
    run(multi, entities=ENTITIES, relations=RELATIONS)
    result = run(
        multi,
        entities=[{"name": "Alpha", "entityType": "concept", "observations": ["first", "third"]}],
        relations=RELATIONS,
    )
    assert result["entitiesCreated"] == 0
    assert result["entitiesMerged"] == 1
    assert result["relationsCreated"] == 0
    assert result["relationsSkipped"] == 1  # relation already exists
    store = multi.get_store("default")
    merged = store.read_entity(result["mapping"]["Alpha"])
    assert merged is not None
    assert merged.observations == ["first", "third"]  # dedup, append new only


def test_relation_dedup_is_per_type_not_per_pair(multi: MultiGraph) -> None:
    """Parallel typed edges between one pair are native to the model; an
    existing relation of one type must not block importing another type."""
    run(multi, entities=ENTITIES, relations=RELATIONS)  # Alpha -supports-> Beta
    result = run(
        multi,
        entities=[],
        relations=[
            {"from": "Alpha", "to": "Beta", "relationType": "supports"},  # duplicate
            {"from": "Alpha", "to": "Beta", "relationType": "related_to"},  # new type
        ],
    )
    assert result["relationsSkipped"] == 1
    assert result["relationsCreated"] == 1
    store = multi.get_store("default")
    from_id, to_id = result["mapping"]["Alpha"], result["mapping"]["Beta"]
    created_types = {r.relation_type.value for r in store.read_relations(from_id, to_id)}
    assert created_types == {"supports", "related_to"}


def test_code_relation_types_round_trip(multi: MultiGraph) -> None:
    """Structural code relations import like any other type, polarity null."""
    result = run(
        multi,
        entities=ENTITIES,
        relations=[
            {"from": "Alpha", "to": "Beta", "relationType": "calls"},
            {"from": "Beta", "to": "Alpha", "relationType": "references"},
        ],
    )
    assert result["relationsCreated"] == 2
    assert result["relationsSkipped"] == 0
    store = multi.get_store("default")
    from_id, to_id = result["mapping"]["Alpha"], result["mapping"]["Beta"]
    calls = store.read_relations(from_id, to_id, "calls")
    assert [r.relation_type.value for r in calls] == ["calls"]
    assert calls[0].polarity is None
    references = store.read_relations(to_id, from_id, "references")
    assert [r.relation_type.value for r in references] == ["references"]
    assert references[0].polarity is None


def test_relations_resolve_against_existing_graph_entities(multi: MultiGraph) -> None:
    existing = create_entity(
        CreateEntityInput.model_validate(
            {"name": "Preexisting", "entityType": "concept", "observations": ["x"]}
        ),
        multi,
    )
    result = run(
        multi,
        entities=[{"name": "Newcomer", "entityType": "claim", "observations": ["y"]}],
        relations=[{"from": "Newcomer", "to": "Preexisting", "relationType": "sources"}],
    )
    assert result["relationsCreated"] == 1
    assert result["mapping"]["Preexisting"] == existing["id"]


def test_unresolvable_relation_reports_error(multi: MultiGraph) -> None:
    result = run(multi, entities=[], relations=RELATIONS)
    assert result["relationsCreated"] == 0
    assert result["relationsSkipped"] == 1
    assert result["errors"][0]["type"] == "unresolvable_relation"


def test_validation_errors_reported_per_item(multi: MultiGraph) -> None:
    result = run(
        multi,
        entities=[
            {"name": "", "entityType": "concept", "observations": []},
            {"name": "Ok", "entityType": "wrongtype", "observations": []},
        ],
        relations=[{"from": "A", "to": "B", "relationType": "loves"}],
    )
    assert result["entitiesCreated"] == 0
    types = [e["type"] for e in result["errors"]]
    assert types == ["validation_error", "validation_error", "validation_error"]


def test_dry_run_creates_nothing_but_counts(multi: MultiGraph) -> None:
    result = run(multi, entities=ENTITIES, relations=RELATIONS, dryRun=True)
    assert result["entitiesCreated"] == 2
    assert result["relationsCreated"] == 1
    assert multi.get_store("default").get_stats()["entityCount"] == 0


def test_jsonl_input_merges_with_arrays(multi: MultiGraph) -> None:
    jsonl = '{"type": "entity", "name": "FromJsonl", "entityType": "concept", "observations": []}'
    result = run(multi, jsonlInput=jsonl, entities=[ENTITIES[0]], relations=[])
    assert result["entitiesCreated"] == 2
    assert set(result["mapping"]) == {"FromJsonl", "Alpha"}
