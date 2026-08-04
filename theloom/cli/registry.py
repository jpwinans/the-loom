"""The command registry — the single source of the CLI surface (seeded here
with the Multi-Graph group).

One descriptor per command: name, category, Pydantic input model, allow-empty
flag, and a handler over the MultiGraph facade. The Typer app is generated
from this list — commands are never defined ad hoc; a single registry drives
the whole surface.

Output shapes are fixed: list-graphs → sorted GraphInfo objects;
create/delete-graph → success strings; list-bridges → bridge docs in insertion
order; find-related-graphs → sorted names; graph-connections → pair counts
sorted by from_graph then to_graph.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pydantic

from theloom.composites import analogy_transfer as analogy_transfer_composite
from theloom.composites import creativity_loop as creativity_loop_composite
from theloom.composites import enrichment_crawl as enrichment_crawl_composite
from theloom.composites import entity_deep_dive as entity_deep_dive_composite
from theloom.composites import explore_frontier as explore_frontier_composite
from theloom.composites import far_analogy_retrieval as far_analogy_retrieval_composite
from theloom.composites import gap_fill_cycle as gap_fill_cycle_composite
from theloom.composites import graph_reconnaissance as graph_reconnaissance_composite
from theloom.composites import hypothesis_engine as hypothesis_engine_composite
from theloom.composites import influence_map as influence_map_composite
from theloom.composites import multi_graph_landscape as multi_graph_landscape_composite
from theloom.composites import propose_entities as propose_entities_composite
from theloom.composites import provenance_audit as provenance_audit_composite
from theloom.composites import self_improve as self_improve_composite
from theloom.composites import semantic_landscape as semantic_landscape_composite
from theloom.composites import simulate_change as simulate_change_composite
from theloom.composites import structural_survey as structural_survey_composite
from theloom.composites import verified_extract as verified_extract_composite
from theloom.errors import NotFoundError, ValidationError
from theloom.model import LoomModel
from theloom.operations import algebra as algebra_ops
from theloom.operations import analysis as analysis_ops
from theloom.operations import bulk as bulk_ops
from theloom.operations import documents as document_ops
from theloom.operations import entity as entity_ops
from theloom.operations import epistemic as epistemic_ops
from theloom.operations import extraction as extraction_ops
from theloom.operations import inference as inference_ops
from theloom.operations import merge as merge_ops
from theloom.operations import reification as reification_ops
from theloom.operations import relations as relation_ops
from theloom.operations import semantic as semantic_ops
from theloom.operations import solve as solve_ops
from theloom.operations import symbolic as symbolic_ops
from theloom.operations import synthesis as synthesis_ops
from theloom.operations import verification as verification_ops
from theloom.store.multigraph import MultiGraph
from theloom.synthesis import cegis as cegis_module
from theloom.viz import serve as viz_serve
from theloom.viz.bundle import ExportBundleInput, assemble_bundle
from theloom.viz.html import VisualizeInput, write_visualization


class _Input(LoomModel):
    """Command input base: unknown keys are stripped, like zod object schemas."""

    model_config = pydantic.ConfigDict(populate_by_name=True, extra="ignore")


class GraphNameInput(_Input):
    name: str


class GraphInput(_Input):
    graph: str


class BridgeFilterInput(_Input):
    from_graph: str | None = None
    to_graph: str | None = None
    entity_id: str | None = None


class EmptyInput(_Input):
    pass


@dataclass(frozen=True)
class CommandDescriptor:
    """One CLI command: its name, input contract, and handler.

    ``handler`` receives the validated input model; the rare command that must
    see the raw input document (e.g. bulk-import's file/stdin modes) sets
    ``raw_handler`` instead.
    """

    name: str
    category: str
    summary: str
    input_model: type[pydantic.BaseModel]
    handler: Callable[[Any, MultiGraph], Any] | None = None
    raw_handler: Callable[[dict[str, Any], MultiGraph], Any] | None = None
    allow_empty: bool = False


# -- Multi-Graph handlers --------------------------------------------------------


def _list_graphs(_: EmptyInput, multi: MultiGraph) -> list[dict[str, Any]]:
    return multi.list_graphs()


def _create_graph(params: GraphNameInput, multi: MultiGraph) -> str:
    multi.create_graph(params.name)
    return f"Graph '{params.name}' created successfully."


def _delete_graph(params: GraphNameInput, multi: MultiGraph) -> str:
    multi.delete_graph(params.name)
    return f"Graph '{params.name}' deleted successfully."


def _list_bridges(params: BridgeFilterInput, multi: MultiGraph) -> list[dict[str, Any]]:
    filter: dict[str, str] = {}
    if params.from_graph is not None:
        filter["from_graph"] = params.from_graph
    if params.to_graph is not None:
        filter["to_graph"] = params.to_graph
    if params.entity_id is not None:
        filter["entity_id"] = params.entity_id
    return multi.bridges.list_bridges(filter or None)


def _find_related_graphs(params: GraphInput, multi: MultiGraph) -> list[str]:
    if not multi.has_graph(params.graph):
        raise NotFoundError(
            f"Graph '{params.graph}' not found. Use list_graphs to see available graphs."
        )
    related: set[str] = set()
    for bridge in multi.bridges.list_bridges():
        if bridge["from_graph"] == params.graph:
            related.add(bridge["to_graph"])
        if bridge["to_graph"] == params.graph:
            related.add(bridge["from_graph"])
    return sorted(related)


def _graph_connections(_: EmptyInput, multi: MultiGraph) -> list[dict[str, Any]]:
    counts: dict[tuple[str, str], int] = {}
    for bridge in multi.bridges.list_bridges():
        key = (bridge["from_graph"], bridge["to_graph"])
        counts[key] = counts.get(key, 0) + 1
    return [
        {"from_graph": from_graph, "to_graph": to_graph, "count": count}
        for (from_graph, to_graph), count in sorted(counts.items())
    ]


# -- Entity/Relation wrappers needing result shaping or raw input ----------------


def _update_entity(params: entity_ops.UpdateEntityInput, multi: MultiGraph) -> Any:
    result = entity_ops.update_entity(params, multi)
    # Backward-compatible response: wrap only when a supersedes relation exists.
    if result["supersedesRelation"] is not None:
        return result
    return result["entity"]


def _bulk_import_raw(input_doc: dict[str, Any], multi: MultiGraph) -> Any:
    """The bulk-import input override: file path / stdin-JSONL / inline modes."""
    if isinstance(input_doc.get("file"), str):
        file_data = json.loads(Path(input_doc["file"]).resolve().read_text(encoding="utf-8"))
        doc = {
            "entities": file_data.get("entities") or [],
            "relations": file_data.get("relations") or [],
            "graph": input_doc.get("graph"),
            "dryRun": input_doc.get("dryRun"),
        }
    elif input_doc.get("stdin") is True:
        doc = {
            "jsonlInput": sys.stdin.read(),
            "graph": input_doc.get("graph"),
            "dryRun": input_doc.get("dryRun"),
        }
    else:
        # Inline mode forwards only entities/relations — the input override
        # drops jsonlInput here (JSONL is reachable via stdin only).
        doc = {
            "entities": input_doc.get("entities") or [],
            "relations": input_doc.get("relations") or [],
            "graph": input_doc.get("graph"),
            "dryRun": input_doc.get("dryRun"),
        }
    return bulk_ops.bulk_import(bulk_ops.BulkImportInput.model_validate(doc), multi)


def _entity_commands() -> list[CommandDescriptor]:
    return [
        CommandDescriptor(
            name="create-entity",
            category="Entity Management",
            summary="Create a new entity in the knowledge graph.",
            input_model=entity_ops.CreateEntityInput,
            handler=entity_ops.create_entity,
        ),
        CommandDescriptor(
            name="read-entity",
            category="Entity Management",
            summary="Read an entity by its ID.",
            input_model=entity_ops.ReadEntityInput,
            handler=entity_ops.read_entity,
        ),
        CommandDescriptor(
            name="update-entity",
            category="Entity Management",
            summary="Update an existing entity.",
            input_model=entity_ops.UpdateEntityInput,
            handler=_update_entity,
        ),
        CommandDescriptor(
            name="delete-entity",
            category="Entity Management",
            summary="Delete an entity from the knowledge graph.",
            input_model=entity_ops.DeleteEntityInput,
            handler=entity_ops.delete_entity,
        ),
        CommandDescriptor(
            name="list-entities",
            category="Entity Management",
            summary="List entities with optional filtering.",
            input_model=entity_ops.ListEntitiesInput,
            handler=entity_ops.list_entities,
            allow_empty=True,
        ),
        CommandDescriptor(
            name="read-entities-by-name",
            category="Entity Management",
            summary="Resolve a batch of entity names to UUIDs.",
            input_model=entity_ops.ReadEntitiesByNameInput,
            handler=entity_ops.read_entities_by_name,
        ),
        CommandDescriptor(
            name="merge-entities",
            category="Entity Management",
            summary=(
                "Merge a secondary entity into a primary one: union observations, "
                "redirect relations, supersede the secondary."
            ),
            input_model=merge_ops.MergeEntitiesInput,
            handler=merge_ops.merge_entities,
        ),
        CommandDescriptor(
            name="bulk-import",
            category="Entity Management",
            summary="Bulk import entities and relations into the knowledge graph.",
            input_model=bulk_ops.BulkImportInput,
            raw_handler=_bulk_import_raw,
        ),
    ]


def _relation_commands() -> list[CommandDescriptor]:
    return [
        CommandDescriptor(
            name="create-relation",
            category="Relation Management",
            summary="Create a relation between two entities.",
            input_model=relation_ops.CreateRelationInput,
            handler=relation_ops.create_relation,
        ),
        CommandDescriptor(
            name="create-relations",
            category="Relation Management",
            summary="Create multiple relations in a single invocation.",
            input_model=relation_ops.CreateRelationsInput,
            handler=relation_ops.create_relations,
        ),
        CommandDescriptor(
            name="read-relation",
            category="Relation Management",
            summary="Read a relation by source and target entity IDs.",
            input_model=relation_ops.ReadRelationInput,
            handler=relation_ops.read_relation,
        ),
        CommandDescriptor(
            name="read-relations",
            category="Relation Management",
            summary="Read all relations between source and target entity IDs.",
            input_model=relation_ops.ReadRelationsInput,
            handler=relation_ops.read_relations,
        ),
        CommandDescriptor(
            name="update-relation",
            category="Relation Management",
            summary="Update an existing relation.",
            input_model=relation_ops.UpdateRelationInput,
            handler=relation_ops.update_relation,
        ),
        CommandDescriptor(
            name="delete-relation",
            category="Relation Management",
            summary="Delete a relation from the knowledge graph.",
            input_model=relation_ops.DeleteRelationInput,
            handler=relation_ops.delete_relation,
        ),
        CommandDescriptor(
            name="list-relations",
            category="Relation Management",
            summary="List relations with optional AND filters.",
            input_model=relation_ops.ListRelationsInput,
            handler=relation_ops.list_relations,
            allow_empty=True,
        ),
        CommandDescriptor(
            name="get-relations",
            category="Relation Management",
            summary="Get all relations connected to an entity.",
            input_model=relation_ops.GetRelationsInput,
            handler=relation_ops.get_relations,
        ),
        CommandDescriptor(
            name="get-neighbors",
            category="Relation Management",
            summary="Get all entities connected to an entity.",
            input_model=relation_ops.GetNeighborsInput,
            handler=relation_ops.get_neighbors,
        ),
    ]


def _analysis_commands() -> list[CommandDescriptor]:
    a = analysis_ops
    entries: list[tuple[str, str, str, Any, Any, bool]] = [
        # name, category, summary, input model, handler, allow_empty
        (
            "graph-stats",
            "Graph Analytics",
            "Get statistics about the knowledge graph.",
            a.GraphOnlyInput,
            a.graph_stats,
            True,
        ),
        (
            "analyze-centrality",
            "Graph Analytics",
            "Analyze entity centrality.",
            a.AnalyzeCentralityInput,
            a.analyze_centrality,
            True,
        ),
        (
            "detect-components",
            "Graph Analytics",
            "Detect connected components.",
            a.DetectComponentsInput,
            a.detect_components,
            True,
        ),
        (
            "find-frequent-subgraphs",
            "Graph Analytics",
            "Find frequent subgraph motifs.",
            a.FindFrequentSubgraphsInput,
            a.find_frequent_subgraphs_op,
            True,
        ),
        (
            "detect-cycles",
            "Loop Analysis",
            "Detect cycles in the knowledge graph.",
            a.DetectCyclesInput,
            a.detect_cycles,
            True,
        ),
        (
            "detect-loops",
            "Loop Analysis",
            "Detect and classify feedback loops.",
            a.DetectLoopsInput,
            a.detect_loops,
            True,
        ),
        (
            "list-loops",
            "Loop Analysis",
            "List loop entities with metadata.",
            a.ListLoopsInput,
            a.list_loops,
            True,
        ),
        (
            "loop-details",
            "Loop Analysis",
            "Get details about a loop entity.",
            a.LoopDetailsInput,
            a.loop_details,
            False,
        ),
        (
            "list-leverage-points",
            "Leverage Points",
            "List leverage point entities.",
            a.ListLeveragePointsInput,
            a.list_leverage_points,
            True,
        ),
        (
            "leverage-point-details",
            "Leverage Points",
            "Get details about a leverage point.",
            a.LeveragePointDetailsInput,
            a.leverage_point_details,
            False,
        ),
        (
            "find-shortest-path",
            "Path Finding",
            "Find the shortest path between entities.",
            a.FindShortestPathInput,
            a.find_shortest_path,
            False,
        ),
        (
            "find-all-paths",
            "Path Finding",
            "Find all simple paths between entities.",
            a.FindAllPathsInput,
            a.find_all_paths,
            False,
        ),
        (
            "extract-subgraph",
            "Subgraph",
            "Extract a subgraph (causal/ego/typed).",
            a.ExtractSubgraphInput,
            a.extract_subgraph,
            False,
        ),
        (
            "find-subgraph-matches",
            "Subgraph",
            "Find approximate subgraph matches.",
            a.FindSubgraphMatchesInput,
            a.find_subgraph_matches_op,
            False,
        ),
        (
            "cross-domain-mapping",
            "Subgraph",
            "Map concepts between domains by structure.",
            a.CrossDomainMappingInput,
            a.cross_domain_mapping_op,
            False,
        ),
        (
            "concept-slippage",
            "Subgraph",
            "Find concept slippage candidates.",
            a.ConceptSlippageInput,
            a.concept_slippage_op,
            False,
        ),
    ]
    return [
        CommandDescriptor(
            name=name,
            category=category,
            summary=summary,
            input_model=input_model,
            handler=handler,
            allow_empty=allow_empty,
        )
        for name, category, summary, input_model, handler, allow_empty in entries
    ]


def _semantic_commands() -> list[CommandDescriptor]:
    s = semantic_ops
    entries: list[tuple[str, str, str, Any, Any, bool]] = [
        (
            "embed-entity",
            "Embeddings",
            "Embed a single entity by ID.",
            s.EmbedEntityInput,
            s.embed_entity,
            False,
        ),
        (
            "embed-entities",
            "Embeddings",
            "Embed all entities in a graph.",
            s.EmbedEntitiesInput,
            s.embed_entities,
            True,
        ),
        (
            "warm-embedder",
            "Embeddings",
            "Pre-download and warm the embedding model.",
            s.WarmEmbedderInput,
            s.warm_embedder,
            True,
        ),
        (
            "flush-pending-embeddings",
            "Embeddings",
            "Flush the pending embedding queue.",
            s.GraphArgInput,
            s.flush_pending_embeddings,
            True,
        ),
        (
            "retry-failed-embeddings",
            "Embeddings",
            "Retry dead-lettered embeddings.",
            s.GraphArgInput,
            s.retry_failed_embeddings,
            True,
        ),
        (
            "embedding-status",
            "Embeddings",
            "Embedding status counts for a graph.",
            s.GraphArgInput,
            s.embedding_status,
            True,
        ),
        (
            "list-dead-letters",
            "Embeddings",
            "List dead-letter queue entries.",
            s.GraphArgInput,
            s.list_dead_letters,
            True,
        ),
        (
            "embedding-reconcile",
            "Embeddings",
            "Reconcile entity status vs vector store.",
            s.EmbeddingReconcileInput,
            s.embedding_reconcile,
            True,
        ),
        (
            "semantic-search",
            "Search",
            "Vector-only semantic search.",
            s.SemanticSearchInput,
            s.semantic_search,
            False,
        ),
        (
            "hybrid-search",
            "Search",
            "Hybrid vector+keyword+graph search.",
            s.HybridSearchInput,
            s.hybrid_search,
            False,
        ),
        (
            "semantic-neighbors",
            "Search",
            "Similar but unconnected entities.",
            s.SemanticNeighborsInput,
            s.semantic_neighbors,
            False,
        ),
        (
            "find-clusters",
            "Embeddings",
            "Discover semantic clusters.",
            s.FindClustersInput,
            s.find_clusters,
            True,
        ),
        (
            "semantic-gaps",
            "Embeddings",
            "Similar but unconnected entity pairs.",
            s.SemanticGapsInput,
            s.semantic_gaps,
            True,
        ),
        (
            "suggest-relations",
            "Embeddings",
            "Suggest relations from patterns.",
            s.SuggestRelationsInput,
            s.suggest_relations,
            False,
        ),
        (
            "resolve-gaps",
            "Embeddings",
            "Create relations for semantic gaps.",
            s.ResolveGapsInput,
            s.resolve_gaps,
            True,
        ),
    ]
    return [
        CommandDescriptor(
            name=name,
            category=category,
            summary=summary,
            input_model=input_model,
            handler=handler,
            allow_empty=allow_empty,
        )
        for name, category, summary, input_model, handler, allow_empty in entries
    ]


def _document_commands() -> list[CommandDescriptor]:
    d = document_ops
    entries: list[tuple[str, str, Any, Any, bool]] = [
        (
            "ingest-document",
            "Ingest a document file into the vector store.",
            d.IngestDocumentInput,
            d.ingest_document,
            False,
        ),
        (
            "ingest-directory",
            "Batch-ingest all matching documents in a directory.",
            d.IngestDirectoryInput,
            d.ingest_directory,
            False,
        ),
        (
            "ingest-url",
            "Fetch and ingest web content from a URL.",
            d.IngestUrlInput,
            d.ingest_url,
            False,
        ),
        (
            "ingest-content",
            "Ingest string content directly.",
            d.IngestContentInput,
            d.ingest_content,
            False,
        ),
        (
            "list-documents",
            "List all ingested documents with chunk counts.",
            d.ListDocumentsInput,
            d.list_documents,
            True,
        ),
        (
            "delete-document",
            "Delete a document and all its chunks from the vector store.",
            d.DeleteDocumentInput,
            d.delete_document,
            False,
        ),
        (
            "reingest-document",
            "Re-ingest a document, comparing content and updating changed chunks.",
            d.ReingestDocumentInput,
            d.reingest_document,
            False,
        ),
        (
            "analyze-category",
            "Discover prevalent semantic themes in a document category.",
            d.AnalyzeCategoryInput,
            d.analyze_category,
            False,
        ),
    ]
    return [
        CommandDescriptor(
            name=name,
            category="Documents",
            summary=summary,
            input_model=input_model,
            handler=handler,
            allow_empty=allow_empty,
        )
        for name, summary, input_model, handler, allow_empty in entries
    ]


def _extraction_commands() -> list[CommandDescriptor]:
    x = extraction_ops
    entries: list[tuple[str, str, Any, Any, bool]] = [
        (
            "extract-codebase",
            "Extract a codebase into a Loom knowledge graph via tree-sitter.",
            x.ExtractCodebaseInput,
            x.extract_codebase,
            False,
        ),
        (
            "update-codebase",
            "Incrementally update an existing codebase graph from a git diff.",
            x.UpdateCodebaseInput,
            x.update_codebase,
            False,
        ),
        (
            "self-model-update",
            "Update The Loom's self-referential codebase graph.",
            x.SelfModelUpdateInput,
            x.self_model_update,
            True,
        ),
        (
            "extract-from-documents",
            "Extract entities and relations from ingested documents using the LLM.",
            x.ExtractFromDocumentsInput,
            x.extract_from_documents,
            True,
        ),
        (
            "extract-preview",
            "Preview extraction results for the first chunks (dry run).",
            x.ExtractPreviewInput,
            x.extract_preview,
            True,
        ),
        (
            "extraction-status",
            "Show the status and progress of extraction runs.",
            x.ExtractionStatusInput,
            x.extraction_status,
            True,
        ),
        (
            "extraction-rollback",
            "Roll back an extraction run by deleting its created entities and relations.",
            x.ExtractionRollbackInput,
            x.extraction_rollback,
            False,
        ),
    ]
    return [
        CommandDescriptor(
            name=name,
            category="Extraction",
            summary=summary,
            input_model=input_model,
            handler=handler,
            allow_empty=allow_empty,
        )
        for name, summary, input_model, handler, allow_empty in entries
    ]


def _verification_commands() -> list[CommandDescriptor]:
    v = verification_ops
    entries: list[tuple[str, str, Any, Any, bool]] = [
        (
            "verify-graph",
            "Verify the graph against guards and spec properties.",
            v.VerifyGraphInput,
            v.verify_graph,
            True,
        ),
        (
            "check-consistency",
            "Run Tier 1 consistency checks on the graph.",
            v.GraphOnlyInput,
            v.check_consistency,
            True,
        ),
        (
            "check-invariants",
            "Check specific named invariants against the graph.",
            v.CheckInvariantsInput,
            v.check_invariants,
            True,
        ),
        (
            "list-guard-violations",
            "Run all guards over every entity and relation.",
            v.ListGuardViolationsInput,
            v.list_guard_violations,
            True,
        ),
        (
            "validate-spec",
            "Validate the graph against property specifications.",
            v.ValidateSpecInput,
            v.validate_spec,
            False,
        ),
        (
            "check-capabilities",
            "Check capability invariants against the graph.",
            v.CheckCapabilitiesInput,
            v.check_capabilities,
            True,
        ),
        (
            "propagate-constraints",
            "Run constraint propagation (AC-3) on type constraints.",
            v.PropagateConstraintsInput,
            v.propagate_constraints,
            False,
        ),
        (
            "constrained-generate",
            "Generate graph structure satisfying type constraints.",
            v.ConstrainedGenerateInput,
            v.constrained_generate,
            False,
        ),
        (
            "cegis-synthesize",
            "Counterexample-guided synthesis of a graph satisfying property specs.",
            cegis_module.CegisSynthesizeInput,
            cegis_module.cegis_synthesize,
            False,
        ),
        (
            "validate-mutation-trace",
            "Replay a mutation trace and check invariants at each step.",
            v.ValidateMutationTraceInput,
            v.validate_mutation_trace,
            False,
        ),
    ]
    return [
        CommandDescriptor(
            name=name,
            category="Verification",
            summary=summary,
            input_model=input_model,
            handler=handler,
            allow_empty=allow_empty,
        )
        for name, summary, input_model, handler, allow_empty in entries
    ]


def _inference_commands() -> list[CommandDescriptor]:
    inf = inference_ops
    entries: list[tuple[str, str, Any, Any, bool]] = [
        (
            "inference-rule-create",
            "Create a declarative inference rule.",
            inf.InferenceRuleCreateInput,
            inf.inference_rule_create,
            False,
        ),
        (
            "inference-rule-list",
            "List all inference rules stored in the graph.",
            inf.InferenceRuleListInput,
            inf.inference_rule_list,
            True,
        ),
        (
            "inference-rule-delete",
            "Delete an inference rule by its entity id.",
            inf.InferenceRuleDeleteInput,
            inf.inference_rule_delete,
            False,
        ),
        (
            "run-inference",
            "Run the inference engine: evaluate enabled rules.",
            inf.RunInferenceInput,
            inf.run_inference,
            True,
        ),
        (
            "inference-trace-list",
            "List inference traces.",
            inf.InferenceTraceListInput,
            inf.inference_trace_list,
            True,
        ),
        (
            "inference-trace-get",
            "Get full details of a specific inference trace.",
            inf.InferenceTraceGetInput,
            inf.inference_trace_get,
            False,
        ),
        (
            "inference-trace-for-fact",
            "Find the inference trace that produced a relation.",
            inf.InferenceTraceForFactInput,
            inf.inference_trace_for_fact,
            False,
        ),
        (
            "explain-inference",
            "Explain a derived fact by walking its inference trace.",
            inf.ExplainInferenceInput,
            inf.explain_inference,
            False,
        ),
    ]
    return [
        CommandDescriptor(
            name=name,
            category="Inference",
            summary=summary,
            input_model=input_model,
            handler=handler,
            allow_empty=allow_empty,
        )
        for name, summary, input_model, handler, allow_empty in entries
    ]


def _symbolic_commands() -> list[CommandDescriptor]:
    sym = symbolic_ops
    entries: list[tuple[str, str, Any, Any]] = [
        (
            "symbolic-solve",
            "Solve an equation or system for variables using SymPy.",
            sym.SolveInput,
            sym.symbolic_solve,
        ),
        (
            "symbolic-simplify",
            "Simplify a mathematical expression using SymPy.",
            sym.ExpressionInput,
            sym.symbolic_simplify,
        ),
        (
            "symbolic-verify",
            "Verify whether a proposed solution satisfies an equation.",
            sym.VerifyInput,
            sym.symbolic_verify,
        ),
        (
            "symbolic-factor",
            "Factor a polynomial expression using SymPy.",
            sym.ExpressionInput,
            sym.symbolic_factor,
        ),
        (
            "symbolic-expand",
            "Expand a product or power expression using SymPy.",
            sym.ExpressionInput,
            sym.symbolic_expand,
        ),
        (
            "symbolic-evaluate",
            "Numerically evaluate an expression, optionally with substitutions.",
            sym.EvaluateInput,
            sym.symbolic_evaluate,
        ),
        (
            "symbolic-latex",
            "Convert a mathematical expression to LaTeX notation.",
            sym.ExpressionInput,
            sym.symbolic_latex,
        ),
        (
            "solve-problem",
            "Solve a natural-language math problem via classify → translate → SymPy, "
            "with LLM fallback.",
            solve_ops.SolveProblemInput,
            solve_ops.solve_problem,
        ),
    ]
    return [
        CommandDescriptor(
            name=name,
            category="Symbolic Mathematics",
            summary=summary,
            input_model=input_model,
            handler=handler,
        )
        for name, summary, input_model, handler in entries
    ]


def _epistemic_commands() -> list[CommandDescriptor]:
    ep = epistemic_ops
    entries: list[tuple[str, str, Any, Any, bool]] = [
        (
            "uncertain-claims",
            "Find entities with low confidence scores.",
            ep.UncertainClaimsInput,
            ep.uncertain_claims,
            True,
        ),
        (
            "needs-evidence",
            "Find claims lacking supporting evidence.",
            ep.NeedsEvidenceInput,
            ep.needs_evidence,
            True,
        ),
        (
            "stale-beliefs",
            "Find entities not recently evaluated.",
            ep.StaleBeliefsInput,
            ep.stale_beliefs,
            True,
        ),
        (
            "provenance-chain",
            "Trace the source chain from an entity.",
            ep.ProvenanceChainInput,
            ep.provenance_chain,
            False,
        ),
        (
            "single-source-claims",
            "Find claims depending on one source.",
            ep.EpistemicQueryInput,
            ep.single_source_claims,
            True,
        ),
        (
            "most-certain",
            "Find the highest-confidence entities.",
            ep.MostCertainInput,
            ep.most_certain,
            True,
        ),
        (
            "contested-claims",
            "Find claims with conflicting evidence.",
            ep.EpistemicQueryInput,
            ep.contested_claims,
            True,
        ),
        (
            "claims-from-source",
            "Find entities sourced from a source.",
            ep.ClaimsFromSourceInput,
            ep.claims_from_source,
            False,
        ),
        (
            "inferred-claims",
            "Find inference-based entities.",
            ep.TypedEpistemicInput,
            ep.inferred_claims,
            True,
        ),
        (
            "unprovenanced",
            "Find entities without provenance.",
            ep.TypedEpistemicInput,
            ep.unprovenanced,
            True,
        ),
        (
            "open-questions",
            "Find active unanswered questions.",
            ep.EpistemicQueryInput,
            ep.open_questions,
            True,
        ),
        (
            "blocking-questions",
            "Find questions blocking other work.",
            ep.BlockingQuestionsInput,
            ep.blocking_questions,
            True,
        ),
        (
            "answered-questions",
            "Find resolved questions.",
            ep.AnsweredQuestionsInput,
            ep.answered_questions,
            True,
        ),
        (
            "session-changelog",
            "What changed since a timestamp.",
            ep.SessionChangelogInput,
            ep.session_changelog,
            False,
        ),
        (
            "postmortem-evaluate",
            "Evaluate postmortem output utility.",
            ep.PostmortemEvaluateInput,
            ep.postmortem_evaluate,
            True,
        ),
        (
            "cross-session-contradictions",
            "Contradictions across sessions.",
            ep.CrossSessionContradictionsInput,
            ep.cross_session_contradictions,
            True,
        ),
        (
            "propagate-credit",
            "Propagate confidence through epistemic chains.",
            ep.PropagateCreditInput,
            ep.propagate_credit,
            False,
        ),
    ]
    return [
        CommandDescriptor(
            name=name,
            category="Epistemic Queries",
            summary=summary,
            input_model=input_model,
            handler=handler,
            allow_empty=allow_empty,
        )
        for name, summary, input_model, handler, allow_empty in entries
    ]


def _algebra_commands() -> list[CommandDescriptor]:
    al = algebra_ops
    entries: list[tuple[str, str, str, Any, Any, bool]] = [
        (
            "semiring-traverse",
            "Semiring Composition",
            "Traverse with a named semiring.",
            al.SemiringTraverseInput,
            al.semiring_traverse,
            False,
        ),
        (
            "semiring-distances",
            "Semiring Composition",
            "Single-source semiring distances.",
            al.SemiringDistancesInput,
            al.semiring_distances,
            False,
        ),
        (
            "semiring-reachable",
            "Semiring Composition",
            "Boolean reachability with path.",
            al.SourceTargetInput,
            al.semiring_reachable,
            False,
        ),
        (
            "semiring-most-confident",
            "Semiring Composition",
            "Max-product confidence path.",
            al.SourceTargetInput,
            al.semiring_most_confident,
            False,
        ),
        (
            "semiring-count-paths",
            "Semiring Composition",
            "Count acyclic paths.",
            al.CountPathsInput,
            al.semiring_count_paths,
            False,
        ),
        (
            "semiring-bottleneck",
            "Semiring Composition",
            "Widest-path bottleneck.",
            al.SourceTargetInput,
            al.semiring_bottleneck,
            False,
        ),
        (
            "transitive-closure",
            "Semiring Composition",
            "Boolean transitive closure pairs.",
            al.TransitiveClosureInput,
            al.transitive_closure,
            True,
        ),
        (
            "adaptive-traverse",
            "Adaptive Routing",
            "Traverse with an auto-routed plan.",
            al.AdaptiveTraverseInput,
            al.adaptive_traverse,
            False,
        ),
        (
            "adaptive-distances",
            "Adaptive Routing",
            "Distances with an auto-routed plan.",
            al.AdaptiveDistancesInput,
            al.adaptive_distances,
            False,
        ),
        (
            "metapath-traverse",
            "Adaptive Routing",
            "Typed step-sequence traversal.",
            al.MetapathTraverseInput,
            al.metapath_traverse,
            False,
        ),
        (
            "cross-type-query",
            "Adaptive Routing",
            "Cross-category query with morphisms.",
            al.CrossTypeQueryInput,
            al.cross_type_query,
            False,
        ),
        (
            "type-analyze",
            "Adaptive Routing",
            "Analyze a query into a routing plan.",
            al.TypeAnalyzeInput,
            al.type_analyze,
            True,
        ),
    ]
    return [
        CommandDescriptor(
            name=name,
            category=category,
            summary=summary,
            input_model=input_model,
            handler=handler,
            allow_empty=allow_empty,
        )
        for name, category, summary, input_model, handler, allow_empty in entries
    ]


def _synthesis_commands() -> list[CommandDescriptor]:
    sy = synthesis_ops
    entries: list[tuple[str, str, Any, Any]] = [
        (
            "synthesize",
            "Synthesize a coherent text output from the knowledge graph.",
            sy.SynthesizeInput,
            sy.synthesize,
        ),
        (
            "synthesize-and-ingest",
            "Synthesize text from the knowledge graph and ingest the output as new entities.",
            sy.SynthesizeInput,
            sy.synthesize_and_ingest,
        ),
        (
            "plan-synthesis",
            "Plan a synthesis without executing.",
            sy.PlanSynthesisInput,
            sy.plan_synthesis,
        ),
        (
            "traverse-synthesis",
            "Plan and traverse a synthesis subgraph, returning evidence units and provenance.",
            sy.TraverseSynthesisInput,
            sy.traverse_synthesis,
        ),
        (
            "verify-fidelity",
            "Check structural fidelity of text against the knowledge graph.",
            sy.VerifyFidelityInput,
            sy.verify_fidelity,
        ),
        (
            "explain-path",
            "Generate a step-by-step natural language explanation of a path between two entities.",
            sy.ExplainPathInput,
            sy.explain_path,
        ),
        (
            "explain-loop",
            "Generate a natural language explanation of a feedback loop's dynamics — what "
            "reinforces or balances, entry points, and likely behavior.",
            sy.ExplainLoopInput,
            sy.explain_loop,
        ),
        (
            "explain-leverage-point",
            "Generate a natural language explanation of a leverage point — why it matters, "
            "what it affects, and its Meadows level context.",
            sy.ExplainLeveragePointInput,
            sy.explain_leverage_point,
        ),
        (
            "decompose-query",
            "Decompose a complex query into ordered sub-questions based on graph structure.",
            sy.DecomposeQueryInput,
            sy.decompose_query,
        ),
    ]
    return [
        CommandDescriptor(
            name=name,
            category="Graph Synthesis",
            summary=summary,
            input_model=input_model,
            handler=handler,
        )
        for name, summary, input_model, handler in entries
    ]


def _composite_commands() -> list[CommandDescriptor]:
    """Composites: multi-section bundles over the core operations."""
    entries: list[tuple[str, str, Any, Any, bool]] = [
        (
            "graph-reconnaissance",
            "Comprehensive structural overview of a graph (composite).",
            graph_reconnaissance_composite.GraphReconInput,
            graph_reconnaissance_composite.graph_reconnaissance,
            True,
        ),
        (
            "entity-deep-dive",
            "Comprehensive analysis of a single entity (composite).",
            entity_deep_dive_composite.EntityDeepDiveInput,
            entity_deep_dive_composite.entity_deep_dive,
            False,
        ),
        (
            "semantic-landscape",
            "Semantic analysis overview of a graph (composite).",
            semantic_landscape_composite.SemanticLandscapeInput,
            semantic_landscape_composite.semantic_landscape,
            True,
        ),
        (
            "provenance-audit",
            "Full provenance audit for an entity (composite).",
            provenance_audit_composite.ProvenanceAuditInput,
            provenance_audit_composite.provenance_audit,
            False,
        ),
        (
            "influence-map",
            "Map an entity's influence via semiring distances and bottlenecks (composite).",
            influence_map_composite.InfluenceMapInput,
            influence_map_composite.influence_map,
            False,
        ),
        (
            "structural-survey",
            "Structural analysis around an entity: ego subgraph, cycles, paths (composite).",
            structural_survey_composite.StructuralSurveyInput,
            structural_survey_composite.structural_survey,
            False,
        ),
        (
            "multi-graph-landscape",
            "Ecosystem-level overview of all graphs (composite).",
            multi_graph_landscape_composite.MultiGraphLandscapeInput,
            multi_graph_landscape_composite.multi_graph_landscape,
            True,
        ),
        (
            "verified-extract",
            "Extract from documents then verify graph integrity (composite).",
            verified_extract_composite.VerifiedExtractInput,
            verified_extract_composite.verified_extract,
            False,
        ),
        (
            "enrichment-crawl",
            "Crawl frontier nodes and propose enrichment relations (composite).",
            enrichment_crawl_composite.EnrichmentCrawlInput,
            enrichment_crawl_composite.enrichment_crawl,
            True,
        ),
        (
            "simulate-change",
            "Simulate graph mutations and preview structural impact (composite).",
            simulate_change_composite.SimulateChangeInput,
            simulate_change_composite.simulate_change,
            False,
        ),
        (
            "creativity-loop",
            "Run the autonomous creativity loop: explore, retrieve, transfer, verify, learn "
            "(composite).",
            creativity_loop_composite.CreativityLoopInput,
            creativity_loop_composite.creativity_loop,
            True,
        ),
        (
            "propose-entities",
            "Propose new entities that should exist in the knowledge graph (composite).",
            propose_entities_composite.ProposeEntitiesInput,
            propose_entities_composite.propose_entities,
            True,
        ),
        (
            "analogy-transfer",
            "Generate novel entities via CWSG analogy transfer from cross-domain mappings "
            "(composite).",
            analogy_transfer_composite.AnalogyTransferInput,
            analogy_transfer_composite.analogy_transfer,
            False,
        ),
        (
            "gap-fill-cycle",
            "Automated gap-filling with validation (composite).",
            gap_fill_cycle_composite.GapFillCycleInput,
            gap_fill_cycle_composite.gap_fill_cycle,
            True,
        ),
        (
            "hypothesis-engine",
            "Generate and rank hypotheses from semantic gaps (composite).",
            hypothesis_engine_composite.HypothesisEngineInput,
            hypothesis_engine_composite.hypothesis_engine,
            True,
        ),
        (
            "far-analogy-retrieval",
            "Run the full far-analogy retrieval pipeline: fingerprint, match, slip, transfer, "
            "score (composite).",
            far_analogy_retrieval_composite.FarAnalogyRetrievalInput,
            far_analogy_retrieval_composite.far_analogy_retrieval,
            True,
        ),
        (
            "self-improve",
            "Autonomous self-improvement cycle: reconnaissance, capability check, propose, "
            "simulate, rank, apply (composite).",
            self_improve_composite.SelfImproveInput,
            self_improve_composite.self_improve,
            True,
        ),
        (
            "explore-frontier",
            "Rank frontier regions by foraging signals with MVT advice and anti-pattern "
            "guards (composite).",
            explore_frontier_composite.ExploreFrontierInput,
            explore_frontier_composite.explore_frontier,
            True,
        ),
    ]
    return [
        CommandDescriptor(
            name=name,
            category="Composites",
            summary=summary,
            input_model=input_model,
            handler=handler,
            allow_empty=allow_empty,
        )
        for name, summary, input_model, handler, allow_empty in entries
    ]


def _serve(params: viz_serve.ServeInput, multi: MultiGraph) -> dict[str, Any]:
    """Start the read-only live server. Prints the {host, port, url, graph}
    handshake, then blocks in uvicorn until shutdown. `check: true` returns the
    handshake without building the app or binding a port — the registry-level
    test path, runnable even when the viz-serve extra is absent.

    Calls through the `viz_serve` module (not bare imported names) so tests can
    monkeypatch `theloom.viz.serve.run_uvicorn` and have it take effect here."""
    graph = params.graph or multi.default_graph
    envelope: dict[str, Any] = {
        "host": params.host,
        "port": params.port,
        "url": f"http://{params.host}:{params.port}",
        "graph": graph,
    }
    if params.check:
        return envelope
    from theloom.cli.io import output_success

    app = viz_serve.create_app(multi, default_graph=params.graph)
    output_success(envelope)  # the handshake — uvicorn.run below never returns to the CLI
    sys.stdout.flush()  # stdout is block-buffered off a TTY; run_uvicorn blocks forever below
    viz_serve.run_uvicorn(app, params.host, params.port)
    return envelope  # reached only after shutdown (Ctrl-C)


COMMANDS: list[CommandDescriptor] = [
    *_entity_commands(),
    *_relation_commands(),
    *_analysis_commands(),
    *_semantic_commands(),
    *_epistemic_commands(),
    *_algebra_commands(),
    *_synthesis_commands(),
    *_document_commands(),
    *_extraction_commands(),
    *_symbolic_commands(),
    *_inference_commands(),
    *_verification_commands(),
    *_composite_commands(),
    CommandDescriptor(
        name="reify-patterns",
        category="Graph Analytics",
        summary="Reify recurring structural motifs as pattern entities.",
        input_model=reification_ops.ReifyPatternsInput,
        handler=reification_ops.reify_patterns,
        allow_empty=True,
    ),
    CommandDescriptor(
        name="trigger-status",
        category="Epistemic Queries",
        summary="Status of the analogy trigger queue.",
        input_model=reification_ops.TriggerStatusInput,
        handler=reification_ops.trigger_status,
        allow_empty=True,
    ),
    CommandDescriptor(
        name="process-triggers",
        category="Epistemic Queries",
        summary="Dequeue pending analogy trigger candidates.",
        input_model=reification_ops.ProcessTriggersInput,
        handler=reification_ops.process_triggers,
        allow_empty=True,
    ),
    CommandDescriptor(
        name="list-graphs",
        category="Multi-Graph",
        summary="List all available graphs with their loaded status and stats.",
        input_model=EmptyInput,
        handler=_list_graphs,
        allow_empty=True,
    ),
    CommandDescriptor(
        name="create-graph",
        category="Multi-Graph",
        summary="Create a new empty graph.",
        input_model=GraphNameInput,
        handler=_create_graph,
    ),
    CommandDescriptor(
        name="delete-graph",
        category="Multi-Graph",
        summary="Delete an existing graph.",
        input_model=GraphNameInput,
        handler=_delete_graph,
    ),
    CommandDescriptor(
        name="list-bridges",
        category="Multi-Graph",
        summary="List cross-graph bridge relations.",
        input_model=BridgeFilterInput,
        handler=_list_bridges,
        allow_empty=True,
    ),
    CommandDescriptor(
        name="find-related-graphs",
        category="Multi-Graph",
        summary="Find graphs connected to a graph via bridge relations.",
        input_model=GraphInput,
        handler=_find_related_graphs,
    ),
    CommandDescriptor(
        name="graph-connections",
        category="Multi-Graph",
        summary="Get bridge counts between all connected graph pairs.",
        input_model=EmptyInput,
        handler=_graph_connections,
        allow_empty=True,
    ),
    CommandDescriptor(
        name="export-bundle",
        category="Visualization",
        summary="Assemble the TapestryBundle JSON for a graph scope.",
        input_model=ExportBundleInput,
        handler=assemble_bundle,
        allow_empty=True,
    ),
    CommandDescriptor(
        name="visualize",
        category="Visualization",
        summary="Write a self-contained interactive HTML visualization of a graph scope.",
        input_model=VisualizeInput,
        handler=write_visualization,
        allow_empty=True,
    ),
    CommandDescriptor(
        name="serve",
        category="Visualization",
        summary="Serve the interactive visualization live over a read-only REST API.",
        input_model=viz_serve.ServeInput,
        handler=_serve,
        allow_empty=True,
    ),
]

_BY_NAME = {descriptor.name: descriptor for descriptor in COMMANDS}


def get_command(name: str) -> CommandDescriptor:
    return _BY_NAME[name]


def run_handler(name: str, input_doc: dict[str, Any], multi: MultiGraph) -> Any:
    """Validate the input against the command's model and run its handler."""
    descriptor = _BY_NAME[name]
    if descriptor.raw_handler is not None:
        return descriptor.raw_handler(input_doc, multi)
    try:
        params = descriptor.input_model.model_validate(input_doc)
    except pydantic.ValidationError as exc:
        raise ValidationError(str(exc)) from exc
    assert descriptor.handler is not None
    return descriptor.handler(params, multi)
