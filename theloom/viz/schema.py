"""TapestryBundle — the versioned wire contract between the Python assembler
and the SPA. Entities/relations are the model's wire docs verbatim; sections
beyond them are optional and flag-controlled."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from theloom.model import LoomModel

SCHEMA_VERSION = 1


class Truncated(LoomModel):
    """Present on `TapestryMeta` only when `ExportBundleInput.maxEntities`
    actually capped the scope — a degree-ranked truncation to the top-N
    entities (see `theloom.viz.bundle._truncate_by_degree`). `total` is the
    pre-cap entity count, `kept` the post-cap count (== `maxEntities`), `by`
    the ranking basis (currently always `"degree"`)."""

    total: int
    kept: int
    by: str


class TapestryMeta(LoomModel):
    graph: str
    title: str | None = None
    scope: str
    generated_at: str = Field(alias="generatedAt")
    entity_count: int = Field(alias="entityCount")
    relation_count: int = Field(alias="relationCount")
    sections: list[str]
    theme: str | None = None
    as_of: str | None = Field(default=None, alias="asOf")
    truncated: Truncated | None = None


class AnalyticsSection(LoomModel):
    centrality: dict[str, dict[str, float]]
    components: list[list[str]]
    loops: list[dict[str, Any]]
    leverage_points: list[dict[str, Any]] = Field(alias="leveragePoints")
    bridges: list[dict[str, Any]]


class TemporalEvent(LoomModel):
    id: str
    at: str
    type: str
    payload: dict[str, Any]


class TemporalSection(LoomModel):
    events: list[TemporalEvent]


class SemanticCluster(LoomModel):
    id: int
    label: str
    entity_ids: list[str] = Field(alias="entityIds")
    size: int


class SemanticSection(LoomModel):
    method: str
    projection: dict[str, list[float]]
    clusters: list[SemanticCluster] | None = None


class TapestryBundle(LoomModel):
    schema_version: int = Field(alias="schemaVersion")
    meta: TapestryMeta
    entities: list[dict[str, Any]]
    relations: list[dict[str, Any]]
    analytics: AnalyticsSection | None = None
    temporal: TemporalSection | None = None
    semantic: SemanticSection | None = None


def bundle_json_schema() -> dict[str, Any]:
    """JSON Schema of the wire shape (camelCase), committed for frontend drift tests."""
    return TapestryBundle.model_json_schema(by_alias=True)


if __name__ == "__main__":  # regenerate the committed schema for the frontend
    import json
    from pathlib import Path

    out = Path(__file__).parents[2] / "tapestry" / "schema" / "bundle.schema.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(bundle_json_schema(), indent=2) + "\n")
    print(f"Wrote {out}")
