"""TapestryBundle schema tests — wire shape and JSON Schema export."""

from __future__ import annotations

from theloom.viz.schema import SCHEMA_VERSION, TapestryBundle, bundle_json_schema


def _minimal_bundle() -> TapestryBundle:
    return TapestryBundle.model_validate(
        {
            "schemaVersion": SCHEMA_VERSION,
            "meta": {
                "graph": "default",
                "scope": "full",
                "generatedAt": "2026-07-11T00:00:00Z",
                "entityCount": 0,
                "relationCount": 0,
                "sections": [],
            },
            "entities": [],
            "relations": [],
        }
    )


def test_minimal_bundle_round_trips_camel_case() -> None:
    bundle = _minimal_bundle()
    doc = bundle.model_dump(by_alias=True, exclude_none=True)
    assert doc["schemaVersion"] == 1
    assert doc["meta"]["generatedAt"] == "2026-07-11T00:00:00Z"
    assert "analytics" not in doc  # optional sections omitted when absent


def test_full_bundle_sections() -> None:
    bundle = TapestryBundle.model_validate(
        {
            "schemaVersion": 1,
            "meta": {
                "graph": "g",
                "title": "T",
                "scope": "ego:abc",
                "generatedAt": "2026-07-11T00:00:00Z",
                "entityCount": 1,
                "relationCount": 0,
                "sections": ["analytics", "temporal", "semantic"],
            },
            "entities": [{"id": "e1", "name": "N", "entityType": "concept"}],
            "relations": [],
            "analytics": {
                "centrality": {"degree": {"e1": 1.0}},
                "components": [["e1"]],
                "loops": [],
                "leveragePoints": [],
                "bridges": [],
            },
            "temporal": {
                "events": [
                    {
                        "id": "1720656000000-0",
                        "at": "2026-07-11T00:00:00+00:00",
                        "type": "entity_created",
                        "payload": {"entity": {"id": "e1"}},
                    }
                ]
            },
            "semantic": {"method": "pca", "projection": {"e1": [0.0, 0.0]}},
        }
    )
    assert bundle.analytics is not None
    assert bundle.temporal is not None and bundle.temporal.events[0].type == "entity_created"
    assert bundle.semantic is not None and bundle.semantic.method == "pca"


def test_json_schema_exports() -> None:
    schema = bundle_json_schema()
    assert schema["properties"]["schemaVersion"]
    assert "meta" in schema["properties"]
