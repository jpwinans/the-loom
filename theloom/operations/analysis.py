"""The 16 traversal & analytics commands.

Every operation hydrates the working graph from list_entities (active-only by
default) + list_relations, runs the algorithm, and shapes wire output. Error
messages carry classification hooks ('not found', 'not a loop', 'Invalid ...')
so error codes are assigned consistently.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from theloom.analysis.crossdomain import map_cross_domain_concepts
from theloom.analysis.isomorphism import find_subgraph_matches, validate_pattern
from theloom.analysis.slippage import find_concept_slippages
from theloom.errors import NotFoundError, OperationError, ValidationError
from theloom.graph.analytics import (
    betweenness_centrality,
    connected_components,
    degree_centrality,
    pagerank_centrality,
    strongly_connected_components,
)
from theloom.graph.cycles import detect_loops as detect_loops_core
from theloom.graph.cycles import find_cycle_paths, has_cycle
from theloom.graph.hydrate import Doc, LoomGraph, hydrate_graph
from theloom.graph.metadata import (
    MEADOWS_LEVELS,
    parse_leverage_point_observations,
    parse_loop_observations,
)
from theloom.graph.motifs import find_frequent_subgraphs
from theloom.graph.paths import bidirectional, bounded_all_simple_paths
from theloom.graph.subgraph import (
    extract_causal_subgraph,
    extract_ego_subgraph,
    extract_typed_subgraph,
    format_subgraph_output,
)
from theloom.model import (
    CAUSAL_RELATION_TYPES,
    EntityFilter,
    EntityType,
    RelationFilter,
    RelationType,
)
from theloom.operations.common import CommandInput, UuidStr, resolve_entity_ref
from theloom.operations.notices import notice, with_notices
from theloom.store.falkor import FalkorGraphStore
from theloom.store.multigraph import MultiGraph

MAX_DEPTH_DEFAULT = 5
MAX_DEPTH_LIMIT = 10
DEFAULT_MAX_PATHS = 1000
DEFAULT_PATH_TIMEOUT_MS = 5000
_CAUSAL = {t.value for t in CAUSAL_RELATION_TYPES}


def _docs(store: FalkorGraphStore) -> tuple[list[Doc], list[Doc]]:
    entities = [e.model_dump(by_alias=True, exclude_unset=True) for e in store.list_entities()]
    relations = [r.model_dump(by_alias=True, exclude_unset=True) for r in store.list_relations()]
    return entities, relations


def _hydrated(store: FalkorGraphStore) -> tuple[list[Doc], list[Doc], LoomGraph]:
    entities, relations = _docs(store)
    return entities, relations, hydrate_graph(entities, relations)


# =============================================================================
# Input models
# =============================================================================


class GraphOnlyInput(CommandInput):
    graph: str | None = None


class DetectCyclesInput(CommandInput):
    graph: str | None = None
    include_paths: bool | None = Field(default=None, alias="includePaths")
    causal_only: bool | None = Field(default=None, alias="causalOnly")


class DetectLoopsInput(CommandInput):
    graph: str | None = None
    persist: bool | None = Field(
        default=None,
        description=(
            "Persist detected loops as loop entities (plus part_of member relations) so "
            "list-loops and loop-details can find them afterward. Defaults to false: a "
            "detect-loops call with no persist key still returns full loop data "
            "(each loop has id: null and persisted: false) but writes nothing to the "
            "graph -- a following list-loops call will NOT see these loops until you "
            're-run detect-loops with "persist": true. When results are not persisted, '
            "the response carries applied: false and a NOT_PERSISTED notice naming this "
            "flag as the fix."
        ),
    )
    min_size: int | None = Field(default=None, alias="minSize")
    max_size: int | None = Field(default=None, alias="maxSize")


class AnalyzeCentralityInput(CommandInput):
    algorithm: str | None = None
    metric: str | None = None
    limit: int | None = Field(default=None, ge=1)
    graph: str | None = None


class DetectComponentsInput(CommandInput):
    strong: bool | None = None
    graph: str | None = None


class ListLoopsInput(CommandInput):
    classification: str | None = None
    through_entity: str | None = Field(default=None, alias="throughEntity")
    min_size: int | None = Field(default=None, alias="minSize")
    max_size: int | None = Field(default=None, alias="maxSize")
    graph: str | None = None


class LoopDetailsInput(CommandInput):
    loop_id: UuidStr = Field(alias="loopId")
    include_members: bool | None = Field(default=None, alias="includeMembers")
    graph: str | None = None


class ListLeveragePointsInput(CommandInput):
    level: int | None = None
    min_level: int | None = Field(default=None, alias="minLevel")
    max_level: int | None = Field(default=None, alias="maxLevel")
    depth_category: str | None = Field(default=None, alias="depthCategory")
    target_entity: str | None = Field(default=None, alias="targetEntity")
    graph: str | None = None


class LeveragePointDetailsInput(CommandInput):
    leverage_point_id: UuidStr = Field(alias="leveragePointId")
    include_targets: bool | None = Field(default=None, alias="includeTargets")
    graph: str | None = None


class FindShortestPathInput(CommandInput):
    """Each endpoint is addressed by its id or its name — exactly one per end."""

    source: UuidStr | None = None
    target: UuidStr | None = None
    source_name: str | None = Field(default=None, alias="sourceName")
    target_name: str | None = Field(default=None, alias="targetName")
    relation_type: RelationType | None = Field(default=None, alias="relationType")
    graph: str | None = None


class FindAllPathsInput(CommandInput):
    source: UuidStr
    target: UuidStr
    max_depth: int | None = Field(default=None, ge=1, alias="maxDepth")
    max_paths: int | None = Field(default=None, ge=1, alias="maxPaths")
    timeout: int | None = Field(default=None, ge=1)
    relation_type: RelationType | None = Field(default=None, alias="relationType")
    graph: str | None = None


class ExtractSubgraphInput(CommandInput):
    mode: str
    entity_id: str | None = Field(default=None, alias="entityId")
    depth: int | None = Field(default=None, ge=1)
    entity_type: EntityType | None = Field(default=None, alias="entityType")
    relation_type: RelationType | None = Field(default=None, alias="relationType")
    output_mode: str | None = None
    graph: str | None = None


class FindFrequentSubgraphsInput(CommandInput):
    frequency_threshold: int | None = Field(default=None, ge=1, alias="frequencyThreshold")
    max_motif_size: int | None = Field(default=None, ge=1, alias="maxMotifSize")
    use_node_types: bool | None = Field(default=None, alias="useNodeTypes")
    use_edge_types: bool | None = Field(default=None, alias="useEdgeTypes")
    node_type_filter: list[str] | None = Field(default=None, alias="nodeTypeFilter")
    edge_type_filter: list[str] | None = Field(default=None, alias="edgeTypeFilter")
    timeout: int | None = Field(default=None, ge=1)
    max_instances: int | None = Field(default=None, ge=1, alias="maxInstances")
    graph: str | None = None


class FindSubgraphMatchesInput(CommandInput):
    pattern: dict[str, Any]
    node_type_weight: float | None = Field(default=None, alias="nodeTypeWeight")
    edge_type_weight: float | None = Field(default=None, alias="edgeTypeWeight")
    topology_weight: float | None = Field(default=None, alias="topologyWeight")
    min_similarity: float | None = Field(default=None, alias="minSimilarity")
    max_results: int | None = Field(default=None, ge=1, alias="maxResults")
    graph: str | None = None


class CrossDomainMappingInput(CommandInput):
    source_domain: dict[str, Any] = Field(alias="sourceDomain")
    target_domain: dict[str, Any] = Field(alias="targetDomain")
    degree_weight: float | None = Field(default=None, alias="degreeWeight")
    relation_profile_weight: float | None = Field(default=None, alias="relationProfileWeight")
    neighbor_profile_weight: float | None = Field(default=None, alias="neighborProfileWeight")
    entity_type_weight: float | None = Field(default=None, alias="entityTypeWeight")
    pair_min_similarity: float | None = Field(default=None, alias="pairMinSimilarity")
    graph: str | None = None


class ConceptSlippageInput(CommandInput):
    concept_id: UuidStr = Field(alias="conceptId")
    temperature: float | None = None
    limit: int | None = Field(default=None, ge=1)
    entity_type: EntityType | None = Field(default=None, alias="entityType")
    relation_type: RelationType | None = Field(default=None, alias="relationType")
    structural_weight: float | None = Field(default=None, alias="structuralWeight")
    proximity_weight: float | None = Field(default=None, alias="proximityWeight")
    context_weight: float | None = Field(default=None, alias="contextWeight")
    graph: str | None = None


# =============================================================================
# Analytics
# =============================================================================


def graph_stats(params: GraphOnlyInput, multi: MultiGraph) -> dict[str, Any]:
    target = params.graph or multi.default_graph
    if params.graph and not multi.has_graph(params.graph):
        raise NotFoundError(
            f"Graph '{params.graph}' not found. Use list_graphs to see available graphs."
        )
    stats = multi.get_store(target).get_stats()
    return {"graph": target, **stats}


def detect_cycles(params: DetectCyclesInput, multi: MultiGraph) -> dict[str, Any]:
    entities, relations = _docs(multi.get_store(params.graph))
    if params.causal_only:
        relations = [r for r in relations if r["relationType"] in _CAUSAL]
    graph = hydrate_graph(entities, relations)
    cyclic = has_cycle(graph)
    response: dict[str, Any] = {"hasCycle": cyclic}
    if params.include_paths and cyclic:
        response["cycles"] = find_cycle_paths(graph)
    return response


def detect_loops(params: DetectLoopsInput, multi: MultiGraph) -> dict[str, Any]:
    """Detect and classify feedback loops. Unless ``persist`` is true, nothing
    is written to the graph -- the response says so explicitly (``applied:
    false`` plus a NOT_PERSISTED notice) so a caller can't read a populated
    ``loops`` array here and then wrongly assume a later list-loops call will
    see the same loops (TL-481)."""
    store = multi.get_store(params.graph)
    entities, relations = _docs(store)
    persist = params.persist or False
    result = detect_loops_core(
        entities,
        relations,
        store,
        min_size=params.min_size,
        max_size=params.max_size,
        persist=persist,
    )
    notices: list[dict[str, Any]] = []
    if not persist and result["loopCount"] > 0:
        notices.append(
            notice(
                "NOT_PERSISTED",
                f"Found {result['loopCount']} loop(s) but did not persist them as loop "
                "entities. list-loops will not see them until they are persisted.",
                hint='Re-run detect-loops with "persist": true to materialize these loops.',
            )
        )
    return with_notices(result, notices, applied=persist)


def analyze_centrality(params: AnalyzeCentralityInput, multi: MultiGraph) -> dict[str, Any]:
    _, _, graph = _hydrated(multi.get_store(params.graph))
    algorithm = params.algorithm or params.metric or "degree"
    if algorithm not in ("degree", "betweenness", "pagerank"):
        raise ValidationError(
            f"Invalid centrality algorithm: '{algorithm}'. "
            "Must be one of: degree, betweenness, pagerank"
        )
    if algorithm == "degree":
        scores = degree_centrality(graph)
    elif algorithm == "betweenness":
        scores = betweenness_centrality(graph)
    else:
        scores = pagerank_centrality(graph)

    entries = sorted(scores.items(), key=lambda item: -item[1])
    if params.limit is not None:
        entries = entries[: params.limit]
    # Ranked [{id, name, entityType, score}] instead of a bare id->score map —
    # the hub name is what a caller almost always needs next, so ship it
    # inline instead of forcing a follow-up read per hub.
    results = [
        {
            "id": entity_id,
            "name": graph.node_docs.get(entity_id, {}).get("name", entity_id),
            "entityType": graph.node_docs.get(entity_id, {}).get("entityType", "unknown"),
            "score": score,
        }
        for entity_id, score in entries
    ]
    response: dict[str, Any] = {"algorithm": algorithm, "results": results}
    if params.limit is not None:
        response["limit"] = params.limit
    return response


def detect_components(params: DetectComponentsInput, multi: MultiGraph) -> dict[str, Any]:
    _, _, graph = _hydrated(multi.get_store(params.graph))
    components = (
        strongly_connected_components(graph) if params.strong else connected_components(graph)
    )
    return {
        "components": components,
        "summary": {
            "componentCount": len(components),
            "largestComponentSize": max((len(c) for c in components), default=0),
        },
    }


# =============================================================================
# Loops + leverage points
# =============================================================================


def list_loops(params: ListLoopsInput, multi: MultiGraph) -> dict[str, Any]:
    """List persisted loop entities. An empty result is ambiguous on its own --
    it could mean the graph truly has no feedback loops, or that detect-loops
    simply hasn't been run with ``persist`` yet (TL-481). When zero loop
    entities exist in the graph at all (before any of this command's own
    filters are applied), the response carries a NONE_PERSISTED notice that
    says so plainly instead of leaving that discrepancy for the caller to
    infer from silence."""
    store = multi.get_store(params.graph)
    loops = [
        e.model_dump(by_alias=True, exclude_unset=True)
        for e in store.list_entities(EntityFilter.model_validate({"entityType": "loop"}))
    ]
    none_persisted = len(loops) == 0
    with_metadata = [{**loop, "_metadata": parse_loop_observations(loop)} for loop in loops]
    if params.classification is not None:
        with_metadata = [
            lp for lp in with_metadata if lp["_metadata"]["classification"] == params.classification
        ]
    if params.through_entity is not None:
        part_of = store.list_relations(
            RelationFilter.model_validate(
                {"from": params.through_entity, "relationType": "part_of"}
            )
        )
        containing = {r.to for r in part_of}
        with_metadata = [lp for lp in with_metadata if lp["id"] in containing]
    if params.min_size is not None:
        with_metadata = [
            lp for lp in with_metadata if lp["_metadata"]["memberCount"] >= params.min_size
        ]
    if params.max_size is not None:
        with_metadata = [
            lp for lp in with_metadata if lp["_metadata"]["memberCount"] <= params.max_size
        ]
    result = {"count": len(with_metadata), "loops": with_metadata}
    if none_persisted:
        return with_notices(
            result,
            [
                notice(
                    "NONE_PERSISTED",
                    "No loop entities have been persisted in this graph yet. This does "
                    "not mean no feedback loops exist -- it means detect-loops has not "
                    "been run with persist, or has not found any yet.",
                    hint='Run detect-loops with "persist": true to detect and persist '
                    "loops before listing them.",
                )
            ],
        )
    return result


def loop_details(params: LoopDetailsInput, multi: MultiGraph) -> dict[str, Any]:
    store = multi.get_store(params.graph)
    entity = store.read_entity(params.loop_id)
    if entity is None:
        raise NotFoundError(
            f"Loop not found with ID: {params.loop_id}. Use list_loops to see available loops."
        )
    doc = entity.model_dump(by_alias=True, exclude_unset=True)
    if doc["entityType"] != "loop":
        raise NotFoundError(
            f"Entity with ID {params.loop_id} is not a loop (type: {doc['entityType']}). "
            "Use list_loops to find loop entities."
        )
    metadata = parse_loop_observations(doc)
    response: dict[str, Any] = {**doc, "_metadata": metadata}
    if params.include_members and metadata["memberIds"]:
        members = []
        for member_id in metadata["memberIds"]:
            member = store.read_entity(member_id)
            if member is not None:
                members.append(member.model_dump(by_alias=True, exclude_unset=True))
        response["members"] = members
    return response


def list_leverage_points(params: ListLeveragePointsInput, multi: MultiGraph) -> dict[str, Any]:
    store = multi.get_store(params.graph)
    points = [
        e.model_dump(by_alias=True, exclude_unset=True)
        for e in store.list_entities(EntityFilter.model_validate({"entityType": "leverage_point"}))
    ]
    with_metadata = [
        {**point, "_metadata": parse_leverage_point_observations(point)} for point in points
    ]
    if params.level is not None:
        with_metadata = [p for p in with_metadata if p["_metadata"]["level"] == params.level]
    if params.min_level is not None:
        with_metadata = [
            p
            for p in with_metadata
            if p["_metadata"]["level"] is not None and p["_metadata"]["level"] >= params.min_level
        ]
    if params.max_level is not None:
        with_metadata = [
            p
            for p in with_metadata
            if p["_metadata"]["level"] is not None and p["_metadata"]["level"] <= params.max_level
        ]
    if params.depth_category is not None:
        with_metadata = [
            p for p in with_metadata if p["_metadata"]["depthCategory"] == params.depth_category
        ]
    if params.target_entity is not None:
        point_ids = {p["id"] for p in with_metadata}
        part_of = store.list_relations(RelationFilter.model_validate({"relationType": "part_of"}))
        targeting = {
            r.from_ for r in part_of if r.to == params.target_entity and r.from_ in point_ids
        }
        with_metadata = [p for p in with_metadata if p["id"] in targeting]
    return {"count": len(with_metadata), "leveragePoints": with_metadata}


def leverage_point_details(params: LeveragePointDetailsInput, multi: MultiGraph) -> dict[str, Any]:
    store = multi.get_store(params.graph)
    entity = store.read_entity(params.leverage_point_id)
    if entity is None:
        raise NotFoundError(f"Leverage point not found with ID: {params.leverage_point_id}")
    doc = entity.model_dump(by_alias=True, exclude_unset=True)
    if doc["entityType"] != "leverage_point":
        raise NotFoundError(
            f"Entity with ID {params.leverage_point_id} is not a leverage_point "
            f"(type: {doc['entityType']})"
        )
    metadata = parse_leverage_point_observations(doc)
    level = metadata["level"]
    response: dict[str, Any] = {
        **doc,
        "_metadata": metadata,
        "levelReference": MEADOWS_LEVELS[level] if level is not None else None,
    }
    if params.include_targets is not False:
        part_of = store.get_relations(
            params.leverage_point_id, direction="outgoing", relation_type="part_of"
        )
        if part_of:
            targets = []
            for relation in part_of:
                target = store.read_entity(relation.to)
                if target is not None:
                    targets.append(target.model_dump(by_alias=True, exclude_unset=True))
            response["targets"] = targets
    return response


# =============================================================================
# Paths
# =============================================================================


def _path_graph(
    multi: MultiGraph, graph_name: str | None, relation_type: RelationType | None
) -> LoomGraph:
    entities, relations = _docs(multi.get_store(graph_name))
    if relation_type:
        relations = [r for r in relations if r["relationType"] == relation_type.value]
    return hydrate_graph(entities, relations)


def _require_endpoints(graph: LoomGraph, source: str, target: str) -> None:
    if not graph.has_node(source):
        raise NotFoundError(
            f"Source entity '{source}' not found in graph. "
            "Use list_entities to verify the entity exists."
        )
    if not graph.has_node(target):
        raise NotFoundError(
            f"Target entity '{target}' not found in graph. "
            "Use list_entities to verify the entity exists."
        )


def find_shortest_path(params: FindShortestPathInput, multi: MultiGraph) -> dict[str, Any]:
    store = multi.get_store(params.graph)
    source = resolve_entity_ref(
        store,
        entity_id=params.source,
        name=params.source_name,
        id_field="source",
        name_field="sourceName",
    )
    target = resolve_entity_ref(
        store,
        entity_id=params.target,
        name=params.target_name,
        id_field="target",
        name_field="targetName",
    )
    graph = _path_graph(multi, params.graph, params.relation_type)
    _require_endpoints(graph, source, target)
    return {"path": bidirectional(graph, source, target)}


def find_all_paths(params: FindAllPathsInput, multi: MultiGraph) -> dict[str, Any]:
    depth = max(1, min(params.max_depth or MAX_DEPTH_DEFAULT, MAX_DEPTH_LIMIT))
    max_paths = params.max_paths or DEFAULT_MAX_PATHS
    timeout_ms = params.timeout or DEFAULT_PATH_TIMEOUT_MS
    graph = _path_graph(multi, params.graph, params.relation_type)
    _require_endpoints(graph, params.source, params.target)
    result = bounded_all_simple_paths(
        graph, params.source, params.target, depth, max_paths, timeout_ms
    )
    response: dict[str, Any] = {
        "paths": result["paths"],
        "maxDepth": depth,
        "truncated": result["truncated"],
    }
    if result.get("truncationReason"):
        response["truncationReason"] = result["truncationReason"]
    return response


# =============================================================================
# Subgraph + patterns
# =============================================================================


def extract_subgraph(params: ExtractSubgraphInput, multi: MultiGraph) -> dict[str, Any]:
    entities, relations = _docs(multi.get_store(params.graph))
    output_mode = params.output_mode or "full"
    if params.mode == "causal":
        extracted_entities, extracted_relations = extract_causal_subgraph(entities, relations)
    elif params.mode == "ego":
        if not params.entity_id:
            raise ValidationError(
                "entityId is required for ego mode. Specify the center entity for the ego network."
            )
        depth = max(1, min(params.depth or 1, MAX_DEPTH_LIMIT))
        ego = extract_ego_subgraph(entities, relations, params.entity_id, depth)
        if ego is None:
            raise NotFoundError(f"Entity not found with ID: {params.entity_id}")
        extracted_entities, extracted_relations = ego
    elif params.mode == "typed":
        if not params.entity_type and not params.relation_type:
            # "requires" ≠ "required" for the error classifier → OPERATION_ERROR
            raise OperationError(
                "Typed mode requires at least one of entityType or relationType. "
                "Specify one or both filters."
            )
        extracted_entities, extracted_relations = extract_typed_subgraph(
            entities,
            relations,
            params.entity_type.value if params.entity_type else None,
            params.relation_type.value if params.relation_type else None,
        )
    else:
        raise ValidationError(f"Invalid extraction mode: '{params.mode}'")
    return format_subgraph_output(extracted_entities, extracted_relations, output_mode)


def find_frequent_subgraphs_op(
    params: FindFrequentSubgraphsInput, multi: MultiGraph
) -> dict[str, Any]:
    frequency_threshold = params.frequency_threshold or 2
    max_motif_size = max(2, min(params.max_motif_size or 3, 5))
    use_node_types = params.use_node_types if params.use_node_types is not None else True
    use_edge_types = params.use_edge_types if params.use_edge_types is not None else True
    entities, relations = _docs(multi.get_store(params.graph))
    if not entities:
        empty: dict[str, Any] = {
            "motifCount": 0,
            "frequencyThreshold": frequency_threshold,
            "maxMotifSize": max_motif_size,
            "useNodeTypes": use_node_types,
            "useEdgeTypes": use_edge_types,
            "motifs": [],
        }
        if params.graph is not None:  # absent keys are omitted from the output
            empty["graph"] = params.graph
        return empty
    graph = hydrate_graph(entities, relations)
    result = find_frequent_subgraphs(
        graph,
        frequency_threshold,
        max_motif_size,
        use_node_types,
        use_edge_types,
        params.node_type_filter,
        params.edge_type_filter,
        params.timeout or 30000,
        params.max_instances or 100,
    )
    response: dict[str, Any] = {
        "motifCount": len(result["motifs"]),
        "frequencyThreshold": frequency_threshold,
        "maxMotifSize": max_motif_size,
        "useNodeTypes": use_node_types,
        "useEdgeTypes": use_edge_types,
        "motifs": result["motifs"],
    }
    if params.graph is not None:  # absent keys are omitted from the output
        response["graph"] = params.graph
    if result["truncated"]:
        response["truncated"] = True
    return response


def find_subgraph_matches_op(params: FindSubgraphMatchesInput, multi: MultiGraph) -> dict[str, Any]:
    errors = validate_pattern(params.pattern)
    if errors:
        # Empty patterns are rejected by validation with a
        # message the classifier maps to OPERATION_ERROR.
        raise OperationError(f"Invalid subgraph pattern: {'; '.join(errors)}")
    entities, relations = _docs(multi.get_store(params.graph))
    options: dict[str, Any] = {}
    for field, key in (
        ("node_type_weight", "nodeTypeWeight"),
        ("edge_type_weight", "edgeTypeWeight"),
        ("topology_weight", "topologyWeight"),
        ("min_similarity", "minSimilarity"),
        ("max_results", "maxResults"),
    ):
        value = getattr(params, field)
        if value is not None:
            options[key] = value
    return find_subgraph_matches(entities, relations, params.pattern, options)


def cross_domain_mapping_op(params: CrossDomainMappingInput, multi: MultiGraph) -> dict[str, Any]:
    entities, relations = _docs(multi.get_store(params.graph))
    options: dict[str, Any] = {}
    for field, key in (
        ("degree_weight", "degreeWeight"),
        ("relation_profile_weight", "relationProfileWeight"),
        ("neighbor_profile_weight", "neighborProfileWeight"),
        ("entity_type_weight", "entityTypeWeight"),
        ("pair_min_similarity", "pairMinSimilarity"),
    ):
        value = getattr(params, field)
        if value is not None:
            options[key] = value
    try:
        return map_cross_domain_concepts(
            entities, relations, params.source_domain, params.target_domain, options
        )
    except ValueError as exc:
        raise OperationError(str(exc)) from exc


def concept_slippage_op(params: ConceptSlippageInput, multi: MultiGraph) -> dict[str, Any]:
    entities, relations = _docs(multi.get_store(params.graph))
    options: dict[str, Any] = {}
    for field, key in (
        ("temperature", "temperature"),
        ("limit", "limit"),
        ("structural_weight", "structuralWeight"),
        ("proximity_weight", "proximityWeight"),
        ("context_weight", "contextWeight"),
    ):
        value = getattr(params, field)
        if value is not None:
            options[key] = value
    if params.entity_type is not None:
        options["entityType"] = params.entity_type.value
    if params.relation_type is not None:
        options["relationType"] = params.relation_type.value
    try:
        return find_concept_slippages(entities, relations, params.concept_id, options)
    except ValueError as exc:
        raise NotFoundError(str(exc)) from exc
