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

from theloom.operations.bulk import BulkImportInput, bulk_import, parse_jsonl
from theloom.operations.entity import CreateEntityInput, create_entity
from theloom.store.multigraph import MultiGraph


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


def test_created_ids_name_only_what_this_call_actually_wrote(multi: MultiGraph) -> None:
    """A rollback needs to erase exactly what one run created, never what it
    merely merged into — so the id lists must stay narrower than the counts,
    which also credit dry-run's would-be creates."""
    first = run(multi, entities=ENTITIES, relations=RELATIONS)
    assert set(first["createdEntityIds"]) == set(first["mapping"].values())
    assert first["createdRelationIds"] == [
        f"{first['mapping']['Alpha']}->{first['mapping']['Beta']}->supports"
    ]

    second = run(
        multi,
        entities=[{"name": "Alpha", "entityType": "concept", "observations": ["first", "third"]}],
        relations=RELATIONS,
    )
    assert second["createdEntityIds"] == []
    assert second["createdRelationIds"] == []


def test_dry_run_creates_no_ids_to_roll_back(multi: MultiGraph) -> None:
    result = run(multi, entities=ENTITIES, relations=RELATIONS, dryRun=True)
    assert result["entitiesCreated"] == 2
    assert result["createdEntityIds"] == []
    assert result["createdRelationIds"] == []


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


def test_bulk_import_rejects_polarity_on_non_causal_relation(multi: MultiGraph) -> None:
    """bulk-import enforces the same polarity partition as create-relation: a
    structural/epistemic edge must not carry polarity."""
    result = run(
        multi,
        entities=ENTITIES,
        relations=[
            {"from": "Alpha", "to": "Beta", "relationType": "calls", "polarity": "+"},
            {"from": "Beta", "to": "Alpha", "relationType": "supports", "polarity": "-"},
        ],
    )
    assert result["relationsCreated"] == 0
    assert result["relationsSkipped"] == 2
    assert [e["type"] for e in result["errors"]] == ["validation_error", "validation_error"]
    assert "must not have polarity" in result["errors"][0]["message"]
    store = multi.get_store("default")
    from_id, to_id = result["mapping"]["Alpha"], result["mapping"]["Beta"]
    assert store.read_relations(from_id, to_id) == []
    assert store.read_relations(to_id, from_id) == []


def test_bulk_import_keeps_causal_polarity(multi: MultiGraph) -> None:
    result = run(
        multi,
        entities=ENTITIES,
        relations=[{"from": "Alpha", "to": "Beta", "relationType": "causes", "polarity": "-"}],
    )
    assert result["relationsCreated"] == 1
    store = multi.get_store("default")
    causes = store.read_relations(result["mapping"]["Alpha"], result["mapping"]["Beta"], "causes")
    assert causes[0].polarity == "-"


def test_codebase_extraction_imports_every_call_edge(multi: MultiGraph) -> None:
    """A relation whose endpoint was never created is dropped with a per-item
    error, not a failure — 1,270 call edges vanished that way. Importing the
    fixture repo end to end is what catches it, and `calls` is now the type
    carrying them.
    """
    from theloom.extraction import treesitter

    extraction = treesitter.extract_codebase("tests/fixtures/repo")
    result = run(multi, entities=extraction["entities"], relations=extraction["relations"])
    assert result["errors"] == []
    assert result["relationsCreated"] == len(extraction["relations"])
    call_edges = [r for r in extraction["relations"] if r["relationType"] == "calls"]
    assert call_edges
    store = multi.get_store("default")
    for edge in call_edges:
        from_id, to_id = result["mapping"][edge["from"]], result["mapping"][edge["to"]]
        stored = store.read_relations(from_id, to_id, "calls")
        assert [r.relation_type.value for r in stored] == ["calls"]
        assert stored[0].evidence == edge["evidence"]


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
