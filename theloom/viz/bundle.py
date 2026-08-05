"""assemble_bundle — the single assembler behind export-bundle, visualize, and
(phase 4) the live server."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from theloom.errors import NotFoundError, ValidationError
from theloom.operations.common import CommandInput
from theloom.store.multigraph import MultiGraph
from theloom.timeutil import iso_now
from theloom.viz.analytics import assemble_analytics
from theloom.viz.schema import SCHEMA_VERSION, TapestryBundle, TapestryMeta, Truncated
from theloom.viz.scope import ScopeInput, resolve_scope
from theloom.viz.semantic import assemble_semantic
from theloom.viz.temporal import assemble_temporal

Doc = dict[str, Any]


class IncludeInput(CommandInput):
    analytics: bool = True
    temporal: bool = True
    semantic: bool = True


class ExportBundleInput(CommandInput):
    graph: str | None = None
    scope: ScopeInput = Field(default_factory=ScopeInput)
    include: IncludeInput = Field(default_factory=IncludeInput)
    title: str | None = None
    as_of: str | None = Field(default=None, alias="asOf")
    # A cap on the *input* model only — never on the wire bundle. When the
    # resolved scope has more entities than this, `_truncate_by_degree` keeps
    # the top-`max_entities` core and `TapestryMeta.truncated` records it.
    # `None` (the default) means "no cap" — every existing caller/test that
    # never sets this is byte-for-byte unaffected.
    max_entities: int | None = Field(default=None, alias="maxEntities", ge=1)


def _truncate_by_degree(
    entities: list[Doc], relations: list[Doc], max_entities: int
) -> tuple[list[Doc], list[Doc], Truncated | None]:
    """Cap `entities` to its top-`max_entities` core by cheap O(E) degree —
    no centrality call, so this stays fast even where the analytics
    guardrails must bail on a real centrality computation. Reproducible: ties
    break on `id` so two runs over the same graph keep the same core (stable
    deep links, stable screenshots), mirroring the frontend's own
    `initialPosition`/`hashSeed` determinism. A no-op (returns `entities`,
    `relations`, `None`) when already at or under the cap.
    """
    total = len(entities)
    if total <= max_entities:
        return entities, relations, None

    degree: dict[str, int] = {e["id"]: 0 for e in entities}
    for relation in relations:
        if relation["from"] in degree:
            degree[relation["from"]] += 1
        if relation["to"] in degree:
            degree[relation["to"]] += 1

    ranked = sorted(entities, key=lambda e: (-degree[e["id"]], e["id"]))
    kept_ids = {e["id"] for e in ranked[:max_entities]}
    # Filter (not resort) the original list, so entity order stays whatever
    # the store returned it in — only membership changed.
    kept_entities = [e for e in entities if e["id"] in kept_ids]
    kept_relations = [r for r in relations if r["from"] in kept_ids and r["to"] in kept_ids]
    return (
        kept_entities,
        kept_relations,
        Truncated(total=total, kept=len(kept_entities), by="degree"),
    )


def assemble_bundle(params: ExportBundleInput, multi: MultiGraph) -> dict[str, Any]:
    """Assemble a TapestryBundle for a graph.

    ``as_of`` is a system-time bound: when set, entities and relations come
    from the store's ``read_graph_as_of`` — the incarnation of each entity that
    was current then, and every edge whose system-time interval was open then,
    including ones retired since — and the shipped event log truncates to
    ``as_of``. Analytics and semantic sections stay whole-graph/current (they
    are already scope-independent) — ``as_of`` bounds entities, relations, and
    the event log only. One gap remains, and it is inherent: ``"hard": true``
    erases rather than invalidates, and nothing can reconstruct what was
    destroyed.
    """
    target = params.graph or multi.default_graph
    if params.graph and not multi.has_graph(params.graph):
        raise NotFoundError(
            f"Graph '{params.graph}' not found. Use list_graphs to see available graphs."
        )

    as_of = params.as_of
    if as_of is not None:
        try:
            datetime.fromisoformat(as_of.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValidationError(
                f"Invalid asOf timestamp: '{as_of}'. Expected ISO 8601 (e.g. 2026-07-01T00:00:00Z)."
            ) from exc

    entities, relations, scope_label = resolve_scope(
        params.scope, multi.get_store(target), as_of=as_of
    )
    truncated: Truncated | None = None
    if params.max_entities is not None:
        entities, relations, truncated = _truncate_by_degree(
            entities, relations, params.max_entities
        )
    # analytics/semantic stay whole-graph/current (scope-independent in phase 1);
    # asOf bounds entities, relations, and the event log only. maxEntities bounds
    # only the entities/relations shipped in THIS bundle, not the analytics
    # section — analytics has its own guardrails (theloom.viz.analytics).
    analytics = assemble_analytics(target, multi) if params.include.analytics else None
    temporal = assemble_temporal(target, multi, as_of=as_of) if params.include.temporal else None
    semantic = assemble_semantic(target, multi) if params.include.semantic else None

    sections = [
        name
        for name, value in (
            ("analytics", analytics),
            ("temporal", temporal),
            ("semantic", semantic),
        )
        if value is not None
    ]
    bundle = TapestryBundle(
        schemaVersion=SCHEMA_VERSION,
        meta=TapestryMeta(
            graph=target,
            title=params.title,
            scope=scope_label,
            generatedAt=iso_now(),
            entityCount=len(entities),
            relationCount=len(relations),
            sections=sections,
            asOf=as_of,
            truncated=truncated,
        ),
        entities=entities,
        relations=relations,
        analytics=analytics,
        temporal=temporal,
        semantic=semantic,
    )
    return bundle.model_dump(by_alias=True, exclude_none=True)
