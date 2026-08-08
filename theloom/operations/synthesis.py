"""The 9 Plan-Traverse-Realize synthesis commands.

Intentional quirks: two different core-number scopes (planning
uses the selected subgraph; the realize step recomputes over the WHOLE
store); cross-graph merges serialize their entityGraphOrigin map as {};
synthesize-and-ingest rejects a raw graph array of length > 1 BEFORE
dedupe; traverse-synthesis plans without cross-graph origin metadata even
in merged mode; and none of the ops validate focus/orderingMetric beyond
silently dropping unknown values (input models enforce the allowed enums).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints

from theloom.errors import InputRequiredError, NotFoundError, OperationError
from theloom.graph.analytics import connected_components
from theloom.graph.hydrate import hydrate_graph
from theloom.graph.paths import bidirectional
from theloom.model import EntityCreate, RelationCreate
from theloom.operations.common import CommandInput, UuidStr, resolve_entity_ref_multi
from theloom.operations.notices import notice, with_notices
from theloom.semantic.embed import get_embedder
from theloom.semantic.search import SupportsQueryEmbedding, search_by_vector
from theloom.store.falkor import FalkorGraphStore
from theloom.store.multigraph import MultiGraph
from theloom.synthesis import fidelity as fidelity_mod
from theloom.synthesis import realizer
from theloom.synthesis.decomposer import decompose_query as decompose_query_core
from theloom.synthesis.links import ChunkLookup
from theloom.synthesis.llm import SynthesisLlmClient, create_synthesis_client
from theloom.synthesis.orderer import compute_core_numbers
from theloom.synthesis.planner import plan_synthesis as plan_synthesis_core
from theloom.synthesis.selector import HybridSearch, find_anchors
from theloom.synthesis.traverser import traverse_synthesis as traverse_synthesis_core

Doc = dict[str, Any]

MAX_GRAPH_ARRAY_LENGTH = 10

OutputFormat = Literal["narrative", "outline", "evidence_map", "causal_chain", "raw", "proposal"]
FocusMode = Literal["narrow", "balanced", "broad"]
TraversalMode = Literal["systematic", "adaptive"]
OrderingMetric = Literal["core-number", "degree", "pagerank", "betweenness"]
GraphParam = (
    str
    | Annotated[
        list[Annotated[str, StringConstraints(min_length=1)]],
        Field(min_length=1, max_length=MAX_GRAPH_ARRAY_LENGTH),
    ]
    | None
)


# =============================================================================
# Store views
# =============================================================================


class FalkorDocStore:
    """Wire-doc view over one FalkorGraphStore (active-only lists, the default
    listEntities/listRelations filters). Docs come back
    VERBATIM — key order matters where synthesis serializes them into text
    (the `raw` format's JSON-stringified output)."""

    def __init__(self, store: FalkorGraphStore) -> None:
        self.falkor = store

    def list_entities(self) -> list[Doc]:
        return self.falkor.list_entity_docs()

    def list_relations(self) -> list[Doc]:
        return self.falkor.list_relation_docs()

    def read_entity(self, entity_id: str) -> Doc | None:
        return self.falkor.read_entity_doc(entity_id)


class MergedDocStore:
    """Read-only merged view: entities/relations
    concatenated per graph, first occurrence wins; qualifying bridges appended."""

    def __init__(self, entities: list[Doc], relations: list[Doc]) -> None:
        self._entities = entities
        self._relations = relations
        self._by_id = {e["id"]: e for e in entities}

    def list_entities(self) -> list[Doc]:
        return self._entities

    def list_relations(self) -> list[Doc]:
        return self._relations

    def read_entity(self, entity_id: str) -> Doc | None:
        return self._by_id.get(entity_id)


def _resolve_store(multi: MultiGraph, graph: str | None) -> FalkorGraphStore:
    if graph and not multi.has_graph(graph):
        raise NotFoundError(f"Graph '{graph}' not found. Use list_graphs to see available graphs.")
    return multi.get_store(graph)


def anchor_search_for(
    stores: list[FalkorGraphStore], embedder: SupportsQueryEmbedding | None = None
) -> HybridSearch:
    """Vector anchor search across ``stores``, through the shared search core.

    Empty when no entity has an embedding, so vectorless graphs
    deterministically take the keyword fallback — decided by a LIMIT-1 probe
    per store rather than by pulling every vector, and decided *before* the
    query is embedded so a vectorless graph never pays for the model.

    The core supplies the two things this used to get wrong on its own: scores
    on the shared 1/(1+L2) scale (so an anchor score means the same thing as a
    semantic-search score), and the active-only filter (an entity that was
    superseded or deprecated keeps its embedding, and must not anchor a
    synthesis).
    """

    def search(query: str, limit: int) -> list[Doc]:
        embedded = [s for s in stores if s.has_entity_vectors()]
        if not embedded:
            return []
        resolved = embedder if embedder is not None else get_embedder()
        query_vector = resolved.embed_query(query)
        hits: list[Doc] = []
        for store in embedded:
            hits.extend(
                {"entityId": hit["id"], "score": hit["score"], "entryType": "entity"}
                for hit in search_by_vector(store, query_vector, limit)
            )
        hits.sort(key=lambda h: -h["score"])
        return hits[:limit]

    return search


def _resolve_graph_param(graph: str | list[str] | None, multi: MultiGraph) -> Doc:
    """Resolve the graph param: string/None -> single store; array ->
    dedupe, then merge with bridges between the named graphs."""
    if isinstance(graph, list):
        if len(graph) > MAX_GRAPH_ARRAY_LENGTH:
            raise OperationError(
                f"Graph array must contain at most {MAX_GRAPH_ARRAY_LENGTH} graph names"
            )
        unique_names = list(dict.fromkeys(graph))
        if not unique_names:
            raise OperationError("Graph array must contain at least one graph name")
        if len(unique_names) == 1:
            store = _resolve_store(multi, unique_names[0])
            return {
                "store": FalkorDocStore(store),
                "falkor": store,
                "falkor_stores": [store],
                "graphNames": None,
                "entityGraphOrigin": None,
            }
        stores = [_resolve_store(multi, name) for name in unique_names]

        name_set = set(unique_names)
        bridges = [
            b
            for b in multi.bridges.list_bridges()
            if b.get("from_graph") in name_set and b.get("to_graph") in name_set
        ]

        entity_map: dict[str, Doc] = {}
        relation_map: dict[str, Doc] = {}
        entity_graph_origin: dict[str, str] = {}
        for name, store in zip(unique_names, stores, strict=True):
            view = FalkorDocStore(store)
            for entity in view.list_entities():
                if entity["id"] not in entity_map:
                    entity_map[entity["id"]] = entity
                    entity_graph_origin[entity["id"]] = name
            for relation in view.list_relations():
                if relation["id"] not in relation_map:
                    relation_map[relation["id"]] = relation
        for bridge in bridges:
            if (
                bridge["id"] not in relation_map
                and bridge["from"] in entity_map
                and bridge["to"] in entity_map
            ):
                relation_map[bridge["id"]] = bridge

        merged = MergedDocStore(list(entity_map.values()), list(relation_map.values()))
        return {
            "store": merged,
            "falkor": None,
            "falkor_stores": stores,
            "graphNames": unique_names,
            "entityGraphOrigin": entity_graph_origin,
        }

    store = _resolve_store(multi, graph)
    return {
        "store": FalkorDocStore(store),
        "falkor": store,
        "falkor_stores": [store],
        "graphNames": None,
        "entityGraphOrigin": None,
    }


# =============================================================================
# Input models
# =============================================================================


QueryStr = Annotated[str, StringConstraints(min_length=1, max_length=10000)]


class SynthesizeInput(CommandInput):
    query: QueryStr
    format: OutputFormat | None = None
    focus: FocusMode | None = None
    mode: TraversalMode | None = None
    ordering_metric: OrderingMetric | None = Field(default=None, alias="orderingMetric")
    max_depth: int | None = Field(default=None, ge=1, le=10, alias="maxDepth")
    max_entities: int | None = Field(default=None, ge=1, le=1000, alias="maxEntities")
    graph: GraphParam = None


class PlanSynthesisInput(CommandInput):
    query: QueryStr
    focus: FocusMode | None = None
    max_depth: int | None = Field(default=None, ge=1, le=10, alias="maxDepth")
    max_entities: int | None = Field(default=None, ge=1, le=1000, alias="maxEntities")
    ordering_metric: OrderingMetric | None = Field(default=None, alias="orderingMetric")
    graph: GraphParam = None


class TraverseSynthesisInput(CommandInput):
    query: QueryStr
    mode: TraversalMode | None = None
    focus: FocusMode | None = None
    ordering_metric: OrderingMetric | None = Field(default=None, alias="orderingMetric")
    max_depth: int | None = Field(default=None, ge=1, le=10, alias="maxDepth")
    max_entities: int | None = Field(default=None, ge=1, le=1000, alias="maxEntities")
    graph: GraphParam = None


_VERIFY_FIDELITY_ENTITY_IDS_DESC = (
    "Which entities to check `text` against. Omitting this (or passing an "
    "empty list) does NOT grade against the whole graph — a real-sized graph "
    "makes that score meaningless (mostly-zero entity/relation coverage). "
    "Instead the command auto-scopes: it runs its own retrieval (hybrid "
    "vector search on `text`, falling back to keyword matching when "
    "entities lack embeddings) to select up to 10 relevant entities, grades "
    "against those, and reports the selection as an AUTO_SCOPED entry in "
    "the response's `notices`. If nothing in the graph matches `text` well "
    "enough to select, the command refuses (INPUT_REQUIRED) rather than "
    "silently scoring nothing. For predictable, reviewable scoping, run "
    "hybrid-search on `text` yourself first and pass the entity ids you "
    "judge relevant here."
)


class VerifyFidelityInput(CommandInput):
    text: Annotated[str, StringConstraints(min_length=1, max_length=1000000)]
    entity_ids: list[UuidStr] | None = Field(
        default=None, alias="entityIds", description=_VERIFY_FIDELITY_ENTITY_IDS_DESC
    )
    mode: Literal["structural", "narrative"] | None = None
    graph: GraphParam = None


class ExplainPathInput(CommandInput):
    """Each endpoint is addressed by its id or its name — exactly one per end."""

    source_id: UuidStr | None = Field(default=None, alias="sourceId")
    target_id: UuidStr | None = Field(default=None, alias="targetId")
    source_name: str | None = Field(default=None, alias="sourceName")
    target_name: str | None = Field(default=None, alias="targetName")
    path: list[UuidStr] | None = None
    graph: GraphParam = None


class ExplainLoopInput(CommandInput):
    loop_id: UuidStr = Field(alias="loopId")
    graph: str | None = None


class ExplainLeveragePointInput(CommandInput):
    leverage_point_id: UuidStr = Field(alias="leveragePointId")
    graph: str | None = None


class DecomposeQueryInput(CommandInput):
    query: QueryStr
    graph: GraphParam = None


# =============================================================================
# The shared pipeline
# =============================================================================


def _chunk_lookup(multi: MultiGraph) -> ChunkLookup:
    """How synthesis resolves an entity's ``provenance.externalRef`` back to
    the passage it was extracted from. Chunks are global across graphs, so
    this is the one chunk store, not a graph-scoped one."""
    return multi.chunk_store().get_chunk


def _run_pipeline(
    params: SynthesizeInput, multi: MultiGraph
) -> tuple[Doc, Doc, dict[str, int], str, SynthesisLlmClient | None, Doc]:
    resolved = _resolve_graph_param(params.graph, multi)
    llm_client = create_synthesis_client()
    format = params.format or "narrative"
    graph_names = resolved["graphNames"]

    plan = plan_synthesis_core(
        resolved["store"],
        query=params.query,
        focus=params.focus,
        max_depth=min(params.max_depth, 10) if params.max_depth is not None else None,
        max_entities=min(params.max_entities, 1000) if params.max_entities is not None else None,
        ordering_metric=params.ordering_metric,
        llm_client=llm_client,
        hybrid_search=anchor_search_for(resolved["falkor_stores"]),
        entity_graph_origin=resolved["entityGraphOrigin"],
        graph_count=len(graph_names) if graph_names else None,
    )
    traversal_output = traverse_synthesis_core(
        plan, resolved["store"], _chunk_lookup(multi), mode=params.mode
    )
    all_entities = resolved["store"].list_entities()
    all_relations = resolved["store"].list_relations()
    core_numbers = compute_core_numbers(all_entities, all_relations)
    return plan, traversal_output, core_numbers, format, llm_client, resolved


def _plan_output(plan: Doc) -> Doc:
    """Render the plan for JSON output: a cross-graph entityGraphOrigin dict
    serializes as {} (a map has no JSON array/object representation here)."""
    selection_config = plan["metadata"]["selectionConfig"]
    if "entityGraphOrigin" in selection_config:
        selection_config = {**selection_config, "entityGraphOrigin": {}}
        plan = {
            **plan,
            "metadata": {**plan["metadata"], "selectionConfig": selection_config},
        }
    return plan


# =============================================================================
# Handlers
# =============================================================================


def synthesize(params: SynthesizeInput, multi: MultiGraph) -> Doc:
    plan, traversal_output, core_numbers, format, llm_client, _ = _run_pipeline(params, multi)
    return realizer.synthesize(
        plan, traversal_output, core_numbers, format, llm_client, _chunk_lookup(multi)
    )


def synthesize_and_ingest(params: SynthesizeInput, multi: MultiGraph) -> Doc:
    if isinstance(params.graph, list) and len(params.graph) > 1:
        raise OperationError(
            "Cross-graph synthesis uses a read-only merged store and cannot ingest results. "
            "Workaround: (1) use 'synthesize' with the same parameters, (2) create insight "
            "entities in your target graph with 'create_entity', (3) link them to source "
            "entities with 'create_relation'."
        )
    plan, traversal_output, core_numbers, format, llm_client, resolved = _run_pipeline(
        params, multi
    )
    synthesis_output = realizer.synthesize(
        plan, traversal_output, core_numbers, format, llm_client, _chunk_lookup(multi)
    )
    store: FalkorGraphStore = resolved["falkor"]

    model_name = "template"
    if llm_client is not None and synthesis_output["metadata"]["llmUsage"]:
        model_name = synthesis_output["metadata"]["llmUsage"][0]["model"]
    provenance_entries = synthesis_output["provenance"]
    avg_confidence = (
        sum(p["confidence"] for p in provenance_entries) / len(provenance_entries)
        if provenance_entries
        else 0.5
    )
    from theloom.timeutil import iso_now

    now = iso_now()
    source_entity = store.create_entity(
        EntityCreate.model_validate(
            {
                "name": f"Synthesis: {plan['query']} ({now})",
                "entityType": "source",
                "observations": [
                    f"Query: {plan['query']}",
                    f"Format: {format}",
                    f"Entity count: {plan['entityCount']}",
                    f"Model: {model_name}",
                ],
            }
        )
    )
    source_id = source_entity.id
    anchor_entity_ids = plan["metadata"].get("anchorEntityIds") or []
    extraction_method = "automated" if model_name == "template" else "llm_prompted"
    content_fields = {
        "confidence": {"score": avg_confidence, "basis": "inference", "lastEvaluated": now},
        "provenance": {
            "sourceType": "synthesis",
            "sourceId": source_id,
            "externalRef": None,
            "extractionDate": now,
            "extractor": "loom-synthesis",
            "extractionMethod": extraction_method,
        },
    }

    if format == "proposal":
        proposal_output = realizer.parse_proposal_output(synthesis_output["text"])
        created_entity_ids: list[str] = [source_id]
        created_relation_ids: list[str] = []
        for proposal in proposal_output["proposals"]:
            observations = [
                f"Action: {proposal['action']}",
                f"Rationale: {proposal['rationale']}",
                f"Expected impact: {proposal['expectedImpact']}",
            ]
            if proposal.get("addressesViolation"):
                observations.append(f"Addresses violation: {proposal['addressesViolation']}")
            if proposal.get("entitySpec"):
                observations.append(f"Entity spec: {realizer.js_stringify(proposal['entitySpec'])}")
            if proposal.get("relationSpec"):
                observations.append(
                    f"Relation spec: {realizer.js_stringify(proposal['relationSpec'])}"
                )
            claim = store.create_entity(
                EntityCreate.model_validate(
                    {
                        "name": (f"Proposal: {proposal['action']} — {proposal['rationale'][:80]}"),
                        "entityType": "claim",
                        "observations": observations,
                        **content_fields,
                    }
                )
            )
            created_entity_ids.append(claim.id)
            for anchor_id in anchor_entity_ids:
                relation = store.create_relation(
                    RelationCreate.model_validate(
                        {
                            "from": claim.id,
                            "to": anchor_id,
                            "relationType": "supports",
                            "polarity": None,
                            "strength": "moderate",
                            "evidence": None,
                        }
                    )
                )
                created_relation_ids.append(relation.id)
        first_claim_id = created_entity_ids[1] if len(created_entity_ids) > 1 else source_id
        return {
            "synthesisOutput": synthesis_output,
            "createdEntityIds": created_entity_ids,
            "createdRelationIds": created_relation_ids,
            "sourceEntityId": source_id,
            "insightEntityId": first_claim_id,
        }

    insight = store.create_entity(
        EntityCreate.model_validate(
            {
                "name": f"Insight: {plan['query']}",
                "entityType": "insight",
                "observations": realizer.chunk_text(synthesis_output["text"]),
                **content_fields,
            }
        )
    )
    created_relation_ids = [
        store.create_relation(
            RelationCreate.model_validate(
                {
                    "from": insight.id,
                    "to": anchor_id,
                    "relationType": "sources",
                    "polarity": None,
                    "strength": "moderate",
                    "evidence": None,
                }
            )
        ).id
        for anchor_id in anchor_entity_ids
    ]
    return {
        "synthesisOutput": synthesis_output,
        "createdEntityIds": [source_id, insight.id],
        "createdRelationIds": created_relation_ids,
        "sourceEntityId": source_id,
        "insightEntityId": insight.id,
    }


def plan_synthesis(params: PlanSynthesisInput, multi: MultiGraph) -> Doc:
    resolved = _resolve_graph_param(params.graph, multi)
    llm_client = create_synthesis_client()
    graph_names = resolved["graphNames"]
    plan = plan_synthesis_core(
        resolved["store"],
        query=params.query,
        focus=params.focus,
        max_depth=min(params.max_depth, 10) if params.max_depth is not None else None,
        max_entities=min(params.max_entities, 1000) if params.max_entities is not None else None,
        ordering_metric=params.ordering_metric,
        llm_client=llm_client,
        hybrid_search=anchor_search_for(resolved["falkor_stores"]),
        entity_graph_origin=resolved["entityGraphOrigin"],
        graph_count=len(graph_names) if graph_names else None,
    )
    return _plan_output(plan)


def traverse_synthesis(params: TraverseSynthesisInput, multi: MultiGraph) -> Doc:
    resolved = _resolve_graph_param(params.graph, multi)
    llm_client = create_synthesis_client()
    # Planning here runs WITHOUT entityGraphOrigin/graphCount (and the
    # hybrid closure applies no graph filter) — intentional.
    plan = plan_synthesis_core(
        resolved["store"],
        query=params.query,
        focus=params.focus,
        max_depth=min(params.max_depth, 10) if params.max_depth is not None else None,
        max_entities=min(params.max_entities, 1000) if params.max_entities is not None else None,
        ordering_metric=params.ordering_metric,
        llm_client=llm_client,
        hybrid_search=anchor_search_for(resolved["falkor_stores"]),
    )
    return traverse_synthesis_core(plan, resolved["store"], _chunk_lookup(multi), mode=params.mode)


def verify_fidelity(params: VerifyFidelityInput, multi: MultiGraph) -> Doc:
    """TL-484: an unscoped call must never silently grade `text` against the
    entire graph — on a real-sized graph that produces a near-zero score
    shaped like a real verdict regardless of how well-grounded the text
    actually is (the whole-graph entity/relation denominators swamp the
    handful of entities the text is really about). So when ``entityIds`` is
    omitted (or empty — the same "no scope given" signal), this runs the
    command's own retrieval — the identical ``find_anchors`` hybrid-search
    core ``synthesize``/``plan_synthesis`` use for anchor selection, not a
    reimplementation — to pick relevant entities before scoring, and reports
    the selection via an ``AUTO_SCOPED`` notice. If retrieval finds nothing
    (no embeddings AND no keyword overlap), it refuses with INPUT_REQUIRED
    instead of scoring an empty/whole-graph scope. Explicit ``entityIds``
    (the scoped path) is untouched: same call, same scoring, no notice.
    """
    resolved = _resolve_graph_param(params.graph, multi)
    llm_client = create_synthesis_client()
    store = resolved["store"]
    entities = store.list_entities()
    relations = store.list_relations()

    entity_ids = params.entity_ids
    notices: list[Doc] = []
    if not entity_ids:
        anchor_ids = find_anchors(params.text, store, anchor_search_for(resolved["falkor_stores"]))
        if not anchor_ids:
            raise InputRequiredError(
                "verify-fidelity needs entityIds to know which entities `text` "
                "should be checked against. entityIds was omitted, and this "
                "command's own retrieval (hybrid search, falling back to "
                "keyword matching) found no entity in the graph that matches "
                "`text` well enough to auto-scope to — grading against the "
                "whole graph would produce a meaningless score, so it refuses "
                "instead. Run hybrid-search on the text to find relevant "
                "entities, then pass their ids as entityIds."
            )
        entity_ids = anchor_ids
        by_id = {e["id"]: e["name"] for e in entities}
        selected_names = [by_id.get(eid, eid) for eid in anchor_ids]
        notices.append(
            notice(
                "AUTO_SCOPED",
                f"entityIds was omitted; auto-scoped to {len(anchor_ids)} "
                f"entit{'y' if len(anchor_ids) == 1 else 'ies'} selected by this "
                f"command's own retrieval (hybrid search over the text, "
                f"falling back to keyword matching when entities lack "
                f"embeddings): {', '.join(selected_names)}.",
                hint=(
                    "Pass entityIds explicitly to control scoping yourself — "
                    "e.g. the entity ids returned by hybrid-search on the same "
                    "text."
                ),
            )
        )

    result = fidelity_mod.verify_fidelity(
        params.text,
        entities,
        relations,
        entity_ids=entity_ids,
        mode=params.mode,
        llm_client=llm_client,
    )
    return with_notices(result, notices)


def explain_path(params: ExplainPathInput, multi: MultiGraph) -> Doc:
    resolved = _resolve_graph_param(params.graph, multi)
    store = resolved["store"]
    # Names resolve against the underlying graph stores — the doc-store view
    # above cannot run the resolver's filtered read.
    source_id: str = resolve_entity_ref_multi(
        resolved["falkor_stores"],
        entity_id=params.source_id,
        name=params.source_name,
        id_field="sourceId",
        name_field="sourceName",
    )
    target_id: str = resolve_entity_ref_multi(
        resolved["falkor_stores"],
        entity_id=params.target_id,
        name=params.target_name,
        id_field="targetId",
        name_field="targetName",
    )
    entities = store.list_entities()
    relations = store.list_relations()

    path_ids = params.path
    if path_ids is None:
        graph = hydrate_graph(entities, relations)
        path_ids = bidirectional(graph, source_id, target_id)
        if not path_ids:
            raise OperationError(f"No path found from {source_id} to {target_id}")
    llm_client = create_synthesis_client()
    return realizer.explain_path(entities, relations, path_ids, llm_client)


def explain_loop(params: ExplainLoopInput, multi: MultiGraph) -> Doc:
    store = FalkorDocStore(_resolve_store(multi, params.graph))
    loop_entity = store.read_entity(params.loop_id)
    if loop_entity is None:
        raise NotFoundError(f"Loop not found with ID: {params.loop_id}")
    if loop_entity["entityType"] != "loop":
        # the error classifier special-cases 'not a loop' -> NOT_FOUND
        raise NotFoundError(
            f"Entity with ID {params.loop_id} is not a loop (type: {loop_entity['entityType']})"
        )
    llm_client = create_synthesis_client()
    return realizer.explain_loop(loop_entity, store.read_entity, store.list_relations(), llm_client)


def explain_leverage_point(params: ExplainLeveragePointInput, multi: MultiGraph) -> Doc:
    falkor = _resolve_store(multi, params.graph)
    store = FalkorDocStore(falkor)
    lp_entity = store.read_entity(params.leverage_point_id)
    if lp_entity is None:
        raise NotFoundError(f"Leverage point not found with ID: {params.leverage_point_id}")
    if lp_entity["entityType"] != "leverage_point":
        # the error classifier special-cases 'not a leverage_point' -> NOT_FOUND
        raise NotFoundError(
            f"Entity with ID {params.leverage_point_id} is not a leverage_point "
            f"(type: {lp_entity['entityType']})"
        )

    def get_part_of_targets() -> list[Doc]:
        return [
            r.model_dump(by_alias=True, exclude_unset=True)
            for r in falkor.get_relations(params.leverage_point_id, "outgoing", "part_of")
        ]

    llm_client = create_synthesis_client()
    return realizer.explain_leverage_point(
        lp_entity, store.read_entity, get_part_of_targets, llm_client
    )


def decompose_query(params: DecomposeQueryInput, multi: MultiGraph) -> Doc:
    resolved = _resolve_graph_param(params.graph, multi)
    store = resolved["store"]
    entities = store.list_entities()
    relations = store.list_relations()
    graph = hydrate_graph(entities, relations)
    clusters = connected_components(graph)
    llm_client = create_synthesis_client()
    return decompose_query_core(
        {
            "query": params.query,
            "entityCount": len(entities),
            "clusterCount": len(clusters),
            "entityNames": [e["name"] for e in entities],
        },
        llm_client,
    )
