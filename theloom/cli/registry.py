"""The command registry — the single source of the CLI surface (seeded here
with the Multi-Graph group).

One descriptor per command: name, category, Pydantic input model, allow-empty
flag, and a handler over the MultiGraph facade. The Typer app is generated
from this list — commands are never defined ad hoc; a single registry drives
the whole surface.

Every category block declares its commands as a list of ``_Spec`` rows fed
through ``_build`` — the single construction path. ``allow_empty`` is a
required field on ``_Spec`` (no default), so every command states its stance
explicitly; there is no "defaults to False by omission" reading.

Output shapes are fixed: list-graphs → sorted GraphInfo objects;
create/delete-graph → success strings; list-bridges → bridge docs in insertion
order; find-related-graphs → sorted names; graph-connections → pair counts
sorted by from_graph then to_graph.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import pydantic

from theloom.cli.schema import describe_validation_error
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
from theloom.composites import reflect as reflect_composite
from theloom.composites import self_improve as self_improve_composite
from theloom.composites import semantic_landscape as semantic_landscape_composite
from theloom.composites import simulate_change as simulate_change_composite
from theloom.composites import structural_survey as structural_survey_composite
from theloom.composites import verified_extract as verified_extract_composite
from theloom.operations import algebra as algebra_ops
from theloom.operations import analysis as analysis_ops
from theloom.operations import bulk as bulk_ops
from theloom.operations import consumption as consumption_ops
from theloom.operations import documents as document_ops
from theloom.operations import entity as entity_ops
from theloom.operations import epistemic as epistemic_ops
from theloom.operations import extraction as extraction_ops
from theloom.operations import inference as inference_ops
from theloom.operations import merge as merge_ops
from theloom.operations import multigraph as multigraph_ops
from theloom.operations import portability as portability_ops
from theloom.operations import reification as reification_ops
from theloom.operations import relations as relation_ops
from theloom.operations import semantic as semantic_ops
from theloom.operations import solve as solve_ops
from theloom.operations import symbolic as symbolic_ops
from theloom.operations import synthesis as synthesis_ops
from theloom.operations import verification as verification_ops
from theloom.operations import work_memory as work_memory_ops
from theloom.operations.common import CommandInput
from theloom.store.multigraph import MultiGraph
from theloom.synthesis import cegis as cegis_module
from theloom.viz import serve as viz_serve
from theloom.viz.bundle import ExportBundleInput, assemble_bundle
from theloom.viz.html import VisualizeInput, write_visualization


class GraphNameInput(CommandInput):
    name: str


class GraphInput(CommandInput):
    graph: str


class BridgeFilterInput(CommandInput):
    from_graph: str | None = None
    to_graph: str | None = None
    entity_id: str | None = None


class EmptyInput(CommandInput):
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


@dataclass(frozen=True)
class _Spec:
    """One declarative command row — the single shape every category block
    builds from. ``allow_empty`` carries no default: every row states its
    stance, so it can never be silently omitted."""

    name: str
    category: str
    summary: str
    input_model: type[pydantic.BaseModel]
    handler: Callable[[Any, MultiGraph], Any]
    allow_empty: bool


def _build(specs: Sequence[_Spec]) -> list[CommandDescriptor]:
    return [
        CommandDescriptor(
            name=spec.name,
            category=spec.category,
            summary=spec.summary,
            input_model=spec.input_model,
            handler=spec.handler,
            allow_empty=spec.allow_empty,
        )
        for spec in specs
    ]


# -- Entity/Relation wrappers needing result shaping ------------------------


def _update_entity(params: entity_ops.UpdateEntityInput, multi: MultiGraph) -> Any:
    result = entity_ops.update_entity(params, multi)
    # Backward-compatible response: wrap only when a supersedes relation exists.
    if result["supersedesRelation"] is not None:
        return result
    return result["entity"]


def _entity_commands() -> list[CommandDescriptor]:
    return _build(
        [
            _Spec(
                "create-entity",
                "Entity Management",
                "Create a new entity in the knowledge graph.",
                entity_ops.CreateEntityInput,
                entity_ops.create_entity,
                False,
            ),
            _Spec(
                "read-entity",
                "Entity Management",
                "Read an entity by its ID.",
                entity_ops.ReadEntityInput,
                entity_ops.read_entity,
                False,
            ),
            _Spec(
                "update-entity",
                "Entity Management",
                "Update an existing entity.",
                entity_ops.UpdateEntityInput,
                _update_entity,
                False,
            ),
            _Spec(
                "delete-entity",
                "Entity Management",
                "Retract an entity and its relations, preserving history "
                '(erase outright with "hard": true).',
                entity_ops.DeleteEntityInput,
                entity_ops.delete_entity,
                False,
            ),
            _Spec(
                "list-entities",
                "Entity Management",
                "List entities with optional filtering.",
                entity_ops.ListEntitiesInput,
                entity_ops.list_entities,
                True,
            ),
            _Spec(
                "read-entities-by-name",
                "Entity Management",
                "Resolve a batch of entity names to UUIDs.",
                entity_ops.ReadEntitiesByNameInput,
                entity_ops.read_entities_by_name,
                False,
            ),
            _Spec(
                "merge-entities",
                "Entity Management",
                "Merge a secondary entity into a primary one: union observations, "
                "redirect relations, supersede the secondary.",
                merge_ops.MergeEntitiesInput,
                merge_ops.merge_entities,
                False,
            ),
        ]
    ) + [
        CommandDescriptor(
            name="bulk-import",
            category="Entity Management",
            summary="Bulk import entities and relations into the knowledge graph.",
            input_model=bulk_ops.BulkImportInput,
            raw_handler=bulk_ops.bulk_import_raw,
            allow_empty=False,
        ),
    ]


def _relation_commands() -> list[CommandDescriptor]:
    return _build(
        [
            _Spec(
                "create-relation",
                "Relation Management",
                "Create a relation between two entities.",
                relation_ops.CreateRelationInput,
                relation_ops.create_relation,
                False,
            ),
            _Spec(
                "create-relations",
                "Relation Management",
                "Create multiple relations in a single invocation.",
                relation_ops.CreateRelationsInput,
                relation_ops.create_relations,
                False,
            ),
            _Spec(
                "read-relation",
                "Relation Management",
                "Read a relation by source and target entity IDs.",
                relation_ops.ReadRelationInput,
                relation_ops.read_relation,
                False,
            ),
            _Spec(
                "read-relations",
                "Relation Management",
                "Read all relations between source and target entity IDs.",
                relation_ops.ReadRelationsInput,
                relation_ops.read_relations,
                False,
            ),
            _Spec(
                "update-relation",
                "Relation Management",
                "Update an existing relation.",
                relation_ops.UpdateRelationInput,
                relation_ops.update_relation,
                False,
            ),
            _Spec(
                "delete-relation",
                "Relation Management",
                'Retract a relation, preserving history (erase outright with "hard": true).',
                relation_ops.DeleteRelationInput,
                relation_ops.delete_relation,
                False,
            ),
            _Spec(
                "list-relations",
                "Relation Management",
                "List relations with optional AND filters.",
                relation_ops.ListRelationsInput,
                relation_ops.list_relations,
                True,
            ),
            _Spec(
                "get-relations",
                "Relation Management",
                "Get all relations connected to an entity.",
                relation_ops.GetRelationsInput,
                relation_ops.get_relations,
                False,
            ),
            _Spec(
                "get-neighbors",
                "Relation Management",
                "Get all entities connected to an entity.",
                relation_ops.GetNeighborsInput,
                relation_ops.get_neighbors,
                False,
            ),
        ]
    )


def _analysis_commands() -> list[CommandDescriptor]:
    a = analysis_ops
    return _build(
        [
            _Spec(
                "graph-stats",
                "Graph Analytics",
                "Get statistics about the knowledge graph.",
                a.GraphOnlyInput,
                a.graph_stats,
                True,
            ),
            _Spec(
                "analyze-centrality",
                "Graph Analytics",
                "Analyze entity centrality.",
                a.AnalyzeCentralityInput,
                a.analyze_centrality,
                True,
            ),
            _Spec(
                "detect-components",
                "Graph Analytics",
                "Detect connected components.",
                a.DetectComponentsInput,
                a.detect_components,
                True,
            ),
            _Spec(
                "find-frequent-subgraphs",
                "Graph Analytics",
                "Find frequent subgraph motifs.",
                a.FindFrequentSubgraphsInput,
                a.find_frequent_subgraphs_op,
                True,
            ),
            _Spec(
                "detect-cycles",
                "Loop Analysis",
                "Detect cycles in the knowledge graph.",
                a.DetectCyclesInput,
                a.detect_cycles,
                True,
            ),
            _Spec(
                "detect-loops",
                "Loop Analysis",
                "Detect and classify feedback loops.",
                a.DetectLoopsInput,
                a.detect_loops,
                True,
            ),
            _Spec(
                "list-loops",
                "Loop Analysis",
                "List loop entities with metadata.",
                a.ListLoopsInput,
                a.list_loops,
                True,
            ),
            _Spec(
                "loop-details",
                "Loop Analysis",
                "Get details about a loop entity.",
                a.LoopDetailsInput,
                a.loop_details,
                False,
            ),
            _Spec(
                "list-leverage-points",
                "Leverage Points",
                "List leverage point entities.",
                a.ListLeveragePointsInput,
                a.list_leverage_points,
                True,
            ),
            _Spec(
                "leverage-point-details",
                "Leverage Points",
                "Get details about a leverage point.",
                a.LeveragePointDetailsInput,
                a.leverage_point_details,
                False,
            ),
            _Spec(
                "find-shortest-path",
                "Path Finding",
                "Find the shortest path between entities.",
                a.FindShortestPathInput,
                a.find_shortest_path,
                False,
            ),
            _Spec(
                "find-all-paths",
                "Path Finding",
                "Find all simple paths between entities.",
                a.FindAllPathsInput,
                a.find_all_paths,
                False,
            ),
            _Spec(
                "extract-subgraph",
                "Subgraph",
                "Extract a subgraph (causal/ego/typed).",
                a.ExtractSubgraphInput,
                a.extract_subgraph,
                False,
            ),
            _Spec(
                "find-subgraph-matches",
                "Subgraph",
                "Find approximate subgraph matches.",
                a.FindSubgraphMatchesInput,
                a.find_subgraph_matches_op,
                False,
            ),
            _Spec(
                "cross-domain-mapping",
                "Subgraph",
                "Map concepts between domains by structure.",
                a.CrossDomainMappingInput,
                a.cross_domain_mapping_op,
                False,
            ),
            _Spec(
                "concept-slippage",
                "Subgraph",
                "Find concept slippage candidates.",
                a.ConceptSlippageInput,
                a.concept_slippage_op,
                False,
            ),
        ]
    )


def _semantic_commands() -> list[CommandDescriptor]:
    s = semantic_ops
    return _build(
        [
            _Spec(
                "embed-entity",
                "Embeddings",
                "Embed a single entity by ID.",
                s.EmbedEntityInput,
                s.embed_entity,
                False,
            ),
            _Spec(
                "embed-entities",
                "Embeddings",
                "Embed all entities in a graph.",
                s.EmbedEntitiesInput,
                s.embed_entities,
                True,
            ),
            _Spec(
                "warm-embedder",
                "Embeddings",
                "Pre-download and warm the embedding model.",
                s.WarmEmbedderInput,
                s.warm_embedder,
                True,
            ),
            _Spec(
                "flush-pending-embeddings",
                "Embeddings",
                "Flush the pending embedding queue.",
                s.GraphArgInput,
                s.flush_pending_embeddings,
                True,
            ),
            _Spec(
                "retry-failed-embeddings",
                "Embeddings",
                "Retry dead-lettered embeddings.",
                s.GraphArgInput,
                s.retry_failed_embeddings,
                True,
            ),
            _Spec(
                "embedding-status",
                "Embeddings",
                "Embedding status counts for a graph.",
                s.GraphArgInput,
                s.embedding_status,
                True,
            ),
            _Spec(
                "list-dead-letters",
                "Embeddings",
                "List dead-letter queue entries.",
                s.GraphArgInput,
                s.list_dead_letters,
                True,
            ),
            _Spec(
                "embedding-reconcile",
                "Embeddings",
                "Reconcile entity status vs vector store.",
                s.EmbeddingReconcileInput,
                s.embedding_reconcile,
                True,
            ),
            _Spec(
                "semantic-search",
                "Search",
                "Vector-only semantic search.",
                s.SemanticSearchInput,
                s.semantic_search,
                False,
            ),
            _Spec(
                "hybrid-search",
                "Search",
                "Hybrid vector+keyword+graph search.",
                s.HybridSearchInput,
                s.hybrid_search,
                False,
            ),
            _Spec(
                "semantic-neighbors",
                "Search",
                "Similar but unconnected entities.",
                s.SemanticNeighborsInput,
                s.semantic_neighbors,
                False,
            ),
            _Spec(
                "find-clusters",
                "Embeddings",
                "Discover semantic clusters.",
                s.FindClustersInput,
                s.find_clusters,
                True,
            ),
            _Spec(
                "semantic-gaps",
                "Embeddings",
                "Similar but unconnected entity pairs.",
                s.SemanticGapsInput,
                s.semantic_gaps,
                True,
            ),
            _Spec(
                "suggest-relations",
                "Embeddings",
                "Suggest relations from patterns.",
                s.SuggestRelationsInput,
                s.suggest_relations,
                False,
            ),
            _Spec(
                "resolve-gaps",
                "Embeddings",
                "Create relations for semantic gaps.",
                s.ResolveGapsInput,
                s.resolve_gaps,
                True,
            ),
        ]
    )


def _document_commands() -> list[CommandDescriptor]:
    d = document_ops
    return _build(
        [
            _Spec(
                "ingest-document",
                "Documents",
                "Ingest a document file into the vector store.",
                d.IngestDocumentInput,
                d.ingest_document,
                False,
            ),
            _Spec(
                "ingest-directory",
                "Documents",
                "Batch-ingest all matching documents in a directory.",
                d.IngestDirectoryInput,
                d.ingest_directory,
                False,
            ),
            _Spec(
                "ingest-url",
                "Documents",
                "Fetch and ingest web content from a URL.",
                d.IngestUrlInput,
                d.ingest_url,
                False,
            ),
            _Spec(
                "ingest-content",
                "Documents",
                "Ingest string content directly.",
                d.IngestContentInput,
                d.ingest_content,
                False,
            ),
            _Spec(
                "list-documents",
                "Documents",
                "List all ingested documents with chunk counts.",
                d.ListDocumentsInput,
                d.list_documents,
                True,
            ),
            _Spec(
                "delete-document",
                "Documents",
                "Delete a document and all its chunks from the vector store.",
                d.DeleteDocumentInput,
                d.delete_document,
                False,
            ),
            _Spec(
                "reingest-document",
                "Documents",
                "Re-ingest a document, comparing content and updating changed chunks.",
                d.ReingestDocumentInput,
                d.reingest_document,
                False,
            ),
            _Spec(
                "analyze-category",
                "Documents",
                "Discover prevalent semantic themes in a document category.",
                d.AnalyzeCategoryInput,
                d.analyze_category,
                False,
            ),
        ]
    )


def _extraction_commands() -> list[CommandDescriptor]:
    x = extraction_ops
    return _build(
        [
            _Spec(
                "extract-codebase",
                "Extraction",
                "Extract a codebase into a Loom knowledge graph via tree-sitter.",
                x.ExtractCodebaseInput,
                x.extract_codebase,
                False,
            ),
            _Spec(
                "update-codebase",
                "Extraction",
                "Incrementally update an existing codebase graph from a git diff.",
                x.UpdateCodebaseInput,
                x.update_codebase,
                False,
            ),
            _Spec(
                "self-model-update",
                "Extraction",
                "Update The Loom's self-referential codebase graph.",
                x.SelfModelUpdateInput,
                x.self_model_update,
                True,
            ),
            _Spec(
                "extract-from-documents",
                "Extraction",
                "Extract entities and relations from ingested documents using the LLM.",
                x.ExtractFromDocumentsInput,
                x.extract_from_documents,
                True,
            ),
            _Spec(
                "extract-preview",
                "Extraction",
                "Preview extraction results for the first chunks (dry run).",
                x.ExtractPreviewInput,
                x.extract_preview,
                True,
            ),
            _Spec(
                "extraction-status",
                "Extraction",
                "Show the status and progress of extraction runs.",
                x.ExtractionStatusInput,
                x.extraction_status,
                True,
            ),
            _Spec(
                "extraction-rollback",
                "Extraction",
                "Roll back an extraction run by deleting its created entities and relations.",
                x.ExtractionRollbackInput,
                x.extraction_rollback,
                False,
            ),
        ]
    )


def _verification_commands() -> list[CommandDescriptor]:
    v = verification_ops
    return _build(
        [
            _Spec(
                "verify-graph",
                "Verification",
                "Verify the graph against guards and spec properties.",
                v.VerifyGraphInput,
                v.verify_graph,
                True,
            ),
            _Spec(
                "check-consistency",
                "Verification",
                "Run Tier 1 consistency checks on the graph.",
                v.GraphOnlyInput,
                v.check_consistency,
                True,
            ),
            _Spec(
                "check-invariants",
                "Verification",
                "Check specific named invariants against the graph.",
                v.CheckInvariantsInput,
                v.check_invariants,
                True,
            ),
            _Spec(
                "list-guard-violations",
                "Verification",
                "Run all guards over every entity and relation.",
                v.ListGuardViolationsInput,
                v.list_guard_violations,
                True,
            ),
            _Spec(
                "validate-spec",
                "Verification",
                "Validate the graph against property specifications.",
                v.ValidateSpecInput,
                v.validate_spec,
                False,
            ),
            _Spec(
                "check-capabilities",
                "Verification",
                "Check capability invariants against the graph.",
                v.CheckCapabilitiesInput,
                v.check_capabilities,
                True,
            ),
            _Spec(
                "propagate-constraints",
                "Verification",
                "Run constraint propagation (AC-3) on type constraints.",
                v.PropagateConstraintsInput,
                v.propagate_constraints,
                False,
            ),
            _Spec(
                "constrained-generate",
                "Verification",
                "Generate graph structure satisfying type constraints.",
                v.ConstrainedGenerateInput,
                v.constrained_generate,
                False,
            ),
            _Spec(
                "cegis-synthesize",
                "Verification",
                "Counterexample-guided synthesis of a graph satisfying property specs.",
                cegis_module.CegisSynthesizeInput,
                cegis_module.cegis_synthesize,
                False,
            ),
            _Spec(
                "validate-mutation-trace",
                "Verification",
                "Replay a mutation trace and check invariants at each step.",
                v.ValidateMutationTraceInput,
                v.validate_mutation_trace,
                False,
            ),
        ]
    )


def _inference_commands() -> list[CommandDescriptor]:
    inf = inference_ops
    return _build(
        [
            _Spec(
                "inference-rule-create",
                "Inference",
                "Create a declarative inference rule.",
                inf.InferenceRuleCreateInput,
                inf.inference_rule_create,
                False,
            ),
            _Spec(
                "inference-rule-list",
                "Inference",
                "List all inference rules stored in the graph.",
                inf.InferenceRuleListInput,
                inf.inference_rule_list,
                True,
            ),
            _Spec(
                "inference-rule-delete",
                "Inference",
                "Delete an inference rule by its entity id.",
                inf.InferenceRuleDeleteInput,
                inf.inference_rule_delete,
                False,
            ),
            _Spec(
                "run-inference",
                "Inference",
                "Run the inference engine: evaluate enabled rules.",
                inf.RunInferenceInput,
                inf.run_inference,
                True,
            ),
            _Spec(
                "inference-trace-list",
                "Inference",
                "List inference traces.",
                inf.InferenceTraceListInput,
                inf.inference_trace_list,
                True,
            ),
            _Spec(
                "inference-trace-get",
                "Inference",
                "Get full details of a specific inference trace.",
                inf.InferenceTraceGetInput,
                inf.inference_trace_get,
                False,
            ),
            _Spec(
                "inference-trace-for-fact",
                "Inference",
                "Find the inference trace that produced a relation.",
                inf.InferenceTraceForFactInput,
                inf.inference_trace_for_fact,
                False,
            ),
            _Spec(
                "explain-inference",
                "Inference",
                "Explain a derived fact by walking its inference trace.",
                inf.ExplainInferenceInput,
                inf.explain_inference,
                False,
            ),
        ]
    )


def _symbolic_commands() -> list[CommandDescriptor]:
    sym = symbolic_ops
    return _build(
        [
            _Spec(
                "symbolic-solve",
                "Symbolic Mathematics",
                "Solve an equation or system for variables using SymPy.",
                sym.SolveInput,
                sym.symbolic_solve,
                False,
            ),
            _Spec(
                "symbolic-simplify",
                "Symbolic Mathematics",
                "Simplify a mathematical expression using SymPy.",
                sym.ExpressionInput,
                sym.symbolic_simplify,
                False,
            ),
            _Spec(
                "symbolic-verify",
                "Symbolic Mathematics",
                "Verify whether a proposed solution satisfies an equation.",
                sym.VerifyInput,
                sym.symbolic_verify,
                False,
            ),
            _Spec(
                "symbolic-factor",
                "Symbolic Mathematics",
                "Factor a polynomial expression using SymPy.",
                sym.ExpressionInput,
                sym.symbolic_factor,
                False,
            ),
            _Spec(
                "symbolic-expand",
                "Symbolic Mathematics",
                "Expand a product or power expression using SymPy.",
                sym.ExpressionInput,
                sym.symbolic_expand,
                False,
            ),
            _Spec(
                "symbolic-evaluate",
                "Symbolic Mathematics",
                "Numerically evaluate an expression, optionally with substitutions.",
                sym.EvaluateInput,
                sym.symbolic_evaluate,
                False,
            ),
            _Spec(
                "symbolic-latex",
                "Symbolic Mathematics",
                "Convert a mathematical expression to LaTeX notation.",
                sym.ExpressionInput,
                sym.symbolic_latex,
                False,
            ),
            _Spec(
                "solve-problem",
                "Symbolic Mathematics",
                "Solve a natural-language math problem via classify → translate → SymPy, "
                "with LLM fallback.",
                solve_ops.SolveProblemInput,
                solve_ops.solve_problem,
                False,
            ),
        ]
    )


def _epistemic_commands() -> list[CommandDescriptor]:
    ep = epistemic_ops
    return _build(
        [
            _Spec(
                "uncertain-claims",
                "Epistemic Queries",
                "Find entities with low confidence scores.",
                ep.UncertainClaimsInput,
                ep.uncertain_claims,
                True,
            ),
            _Spec(
                "needs-evidence",
                "Epistemic Queries",
                "Find claims lacking supporting evidence.",
                ep.NeedsEvidenceInput,
                ep.needs_evidence,
                True,
            ),
            _Spec(
                "stale-beliefs",
                "Epistemic Queries",
                "Find entities not recently evaluated.",
                ep.StaleBeliefsInput,
                ep.stale_beliefs,
                True,
            ),
            _Spec(
                "provenance-chain",
                "Epistemic Queries",
                "Trace the source chain from an entity.",
                ep.ProvenanceChainInput,
                ep.provenance_chain,
                False,
            ),
            _Spec(
                "single-source-claims",
                "Epistemic Queries",
                "Find claims depending on one source.",
                ep.EpistemicQueryInput,
                ep.single_source_claims,
                True,
            ),
            _Spec(
                "most-certain",
                "Epistemic Queries",
                "Find the highest-confidence entities.",
                ep.MostCertainInput,
                ep.most_certain,
                True,
            ),
            _Spec(
                "contested-claims",
                "Epistemic Queries",
                "Find claims with conflicting evidence.",
                ep.EpistemicQueryInput,
                ep.contested_claims,
                True,
            ),
            _Spec(
                "claims-from-source",
                "Epistemic Queries",
                "Find entities sourced from a source.",
                ep.ClaimsFromSourceInput,
                ep.claims_from_source,
                False,
            ),
            _Spec(
                "inferred-claims",
                "Epistemic Queries",
                "Find inference-based entities.",
                ep.TypedEpistemicInput,
                ep.inferred_claims,
                True,
            ),
            _Spec(
                "unprovenanced",
                "Epistemic Queries",
                "Find entities without provenance.",
                ep.TypedEpistemicInput,
                ep.unprovenanced,
                True,
            ),
            _Spec(
                "open-questions",
                "Epistemic Queries",
                "Find active unanswered questions.",
                ep.EpistemicQueryInput,
                ep.open_questions,
                True,
            ),
            _Spec(
                "blocking-questions",
                "Epistemic Queries",
                "Find questions blocking other work.",
                ep.BlockingQuestionsInput,
                ep.blocking_questions,
                True,
            ),
            _Spec(
                "answered-questions",
                "Epistemic Queries",
                "Find resolved questions.",
                ep.AnsweredQuestionsInput,
                ep.answered_questions,
                True,
            ),
            _Spec(
                "session-changelog",
                "Epistemic Queries",
                "What changed since a timestamp.",
                ep.SessionChangelogInput,
                ep.session_changelog,
                False,
            ),
            _Spec(
                "postmortem-evaluate",
                "Epistemic Queries",
                "Evaluate postmortem output utility.",
                ep.PostmortemEvaluateInput,
                ep.postmortem_evaluate,
                True,
            ),
            _Spec(
                "cross-session-contradictions",
                "Epistemic Queries",
                "Contradictions across sessions.",
                ep.CrossSessionContradictionsInput,
                ep.cross_session_contradictions,
                True,
            ),
            _Spec(
                "propagate-credit",
                "Epistemic Queries",
                "Propagate confidence through epistemic chains.",
                ep.PropagateCreditInput,
                ep.propagate_credit,
                False,
            ),
        ]
    )


def _algebra_commands() -> list[CommandDescriptor]:
    al = algebra_ops
    return _build(
        [
            _Spec(
                "semiring-traverse",
                "Semiring Composition",
                "Traverse with a named semiring.",
                al.SemiringTraverseInput,
                al.semiring_traverse,
                False,
            ),
            _Spec(
                "semiring-distances",
                "Semiring Composition",
                "Single-source semiring distances.",
                al.SemiringDistancesInput,
                al.semiring_distances,
                False,
            ),
            _Spec(
                "semiring-reachable",
                "Semiring Composition",
                "Boolean reachability with path.",
                al.SourceTargetInput,
                al.semiring_reachable,
                False,
            ),
            _Spec(
                "semiring-most-confident",
                "Semiring Composition",
                "Max-product confidence path.",
                al.SourceTargetInput,
                al.semiring_most_confident,
                False,
            ),
            _Spec(
                "semiring-count-paths",
                "Semiring Composition",
                "Count acyclic paths.",
                al.CountPathsInput,
                al.semiring_count_paths,
                False,
            ),
            _Spec(
                "semiring-bottleneck",
                "Semiring Composition",
                "Widest-path bottleneck.",
                al.SourceTargetInput,
                al.semiring_bottleneck,
                False,
            ),
            _Spec(
                "transitive-closure",
                "Semiring Composition",
                "Boolean transitive closure pairs.",
                al.TransitiveClosureInput,
                al.transitive_closure,
                True,
            ),
            _Spec(
                "adaptive-traverse",
                "Adaptive Routing",
                "Traverse with an auto-routed plan.",
                al.AdaptiveTraverseInput,
                al.adaptive_traverse,
                False,
            ),
            _Spec(
                "adaptive-distances",
                "Adaptive Routing",
                "Distances with an auto-routed plan.",
                al.AdaptiveDistancesInput,
                al.adaptive_distances,
                False,
            ),
            _Spec(
                "metapath-traverse",
                "Adaptive Routing",
                "Typed step-sequence traversal.",
                al.MetapathTraverseInput,
                al.metapath_traverse,
                False,
            ),
            _Spec(
                "cross-type-query",
                "Adaptive Routing",
                "Cross-category query with morphisms.",
                al.CrossTypeQueryInput,
                al.cross_type_query,
                False,
            ),
            _Spec(
                "type-analyze",
                "Adaptive Routing",
                "Analyze a query into a routing plan.",
                al.TypeAnalyzeInput,
                al.type_analyze,
                True,
            ),
        ]
    )


def _synthesis_commands() -> list[CommandDescriptor]:
    sy = synthesis_ops
    return _build(
        [
            _Spec(
                "synthesize",
                "Graph Synthesis",
                "Synthesize a coherent text output from the knowledge graph.",
                sy.SynthesizeInput,
                sy.synthesize,
                False,
            ),
            _Spec(
                "synthesize-and-ingest",
                "Graph Synthesis",
                "Synthesize text from the knowledge graph and ingest the output as new entities.",
                sy.SynthesizeInput,
                sy.synthesize_and_ingest,
                False,
            ),
            _Spec(
                "plan-synthesis",
                "Graph Synthesis",
                "Plan a synthesis without executing.",
                sy.PlanSynthesisInput,
                sy.plan_synthesis,
                False,
            ),
            _Spec(
                "traverse-synthesis",
                "Graph Synthesis",
                "Plan and traverse a synthesis subgraph, returning evidence units and provenance.",
                sy.TraverseSynthesisInput,
                sy.traverse_synthesis,
                False,
            ),
            _Spec(
                "verify-fidelity",
                "Graph Synthesis",
                "Check structural fidelity of text against the knowledge graph.",
                sy.VerifyFidelityInput,
                sy.verify_fidelity,
                False,
            ),
            _Spec(
                "explain-path",
                "Graph Synthesis",
                "Generate a step-by-step natural language explanation of a path between "
                "two entities.",
                sy.ExplainPathInput,
                sy.explain_path,
                False,
            ),
            _Spec(
                "explain-loop",
                "Graph Synthesis",
                "Generate a natural language explanation of a feedback loop's dynamics — what "
                "reinforces or balances, entry points, and likely behavior.",
                sy.ExplainLoopInput,
                sy.explain_loop,
                False,
            ),
            _Spec(
                "explain-leverage-point",
                "Graph Synthesis",
                "Generate a natural language explanation of a leverage point — why it matters, "
                "what it affects, and its Meadows level context.",
                sy.ExplainLeveragePointInput,
                sy.explain_leverage_point,
                False,
            ),
            _Spec(
                "decompose-query",
                "Graph Synthesis",
                "Decompose a complex query into ordered sub-questions based on graph structure.",
                sy.DecomposeQueryInput,
                sy.decompose_query,
                False,
            ),
        ]
    )


def _consumption_commands() -> list[CommandDescriptor]:
    """Consumption: one-call comprehension answers, token-budgeted and honest
    about what they had to cut."""
    c = consumption_ops
    return _build(
        [
            _Spec(
                "explore",
                "Consumption",
                "Everything about one symbol in one call: definition, callers, callees, imports, "
                "containment, inheritance and the semantic layer, within a token budget.",
                c.ExploreInput,
                c.explore,
                False,
            ),
            _Spec(
                "find-callers",
                "Consumption",
                "Ranked list of the symbols that call this one, each anchored at its call site.",
                c.FindCallsInput,
                c.find_callers,
                False,
            ),
            _Spec(
                "find-callees",
                "Consumption",
                "Ranked list of the symbols this one calls, each anchored at its call site.",
                c.FindCallsInput,
                c.find_callees,
                False,
            ),
            _Spec(
                "blast-radius",
                "Consumption",
                "Reverse dependency reach of a symbol over calls/requires/instance_of, grouped by "
                "module, with hub suppression.",
                c.BlastRadiusInput,
                c.blast_radius,
                False,
            ),
        ]
    )


def _work_memory_commands() -> list[CommandDescriptor]:
    """Work Memory: the experiential layer — what was tried, how it turned out,
    and the standing lessons that fall out of it."""
    return _build(
        [
            _Spec(
                "record-outcome",
                "Work Memory",
                "Record how a piece of work turned out as usage evidence citing the entities "
                "it leaned on (supports when useful, questions when not).",
                work_memory_ops.RecordOutcomeInput,
                work_memory_ops.record_outcome,
                False,
            ),
        ]
    )


def _composite_commands() -> list[CommandDescriptor]:
    """Composites: multi-section bundles over the core operations."""
    return _build(
        [
            _Spec(
                "graph-reconnaissance",
                "Composites",
                "Comprehensive structural overview of a graph (composite).",
                graph_reconnaissance_composite.GraphReconInput,
                graph_reconnaissance_composite.graph_reconnaissance,
                True,
            ),
            _Spec(
                "entity-deep-dive",
                "Composites",
                "Comprehensive analysis of a single entity (composite).",
                entity_deep_dive_composite.EntityDeepDiveInput,
                entity_deep_dive_composite.entity_deep_dive,
                False,
            ),
            _Spec(
                "semantic-landscape",
                "Composites",
                "Semantic analysis overview of a graph (composite).",
                semantic_landscape_composite.SemanticLandscapeInput,
                semantic_landscape_composite.semantic_landscape,
                True,
            ),
            _Spec(
                "reflect",
                "Composites",
                "Distil recorded outcomes into standing lessons: time-decayed usage scores, "
                "preferred/contested/dead-end statuses, and staleness against changed files "
                "(composite).",
                reflect_composite.ReflectInput,
                reflect_composite.reflect,
                True,
            ),
            _Spec(
                "provenance-audit",
                "Composites",
                "Full provenance audit for an entity (composite).",
                provenance_audit_composite.ProvenanceAuditInput,
                provenance_audit_composite.provenance_audit,
                False,
            ),
            _Spec(
                "influence-map",
                "Composites",
                "Map an entity's influence via semiring distances and bottlenecks (composite).",
                influence_map_composite.InfluenceMapInput,
                influence_map_composite.influence_map,
                False,
            ),
            _Spec(
                "structural-survey",
                "Composites",
                "Structural analysis around an entity: ego subgraph, cycles, paths (composite).",
                structural_survey_composite.StructuralSurveyInput,
                structural_survey_composite.structural_survey,
                False,
            ),
            _Spec(
                "multi-graph-landscape",
                "Composites",
                "Ecosystem-level overview of all graphs (composite).",
                multi_graph_landscape_composite.MultiGraphLandscapeInput,
                multi_graph_landscape_composite.multi_graph_landscape,
                True,
            ),
            _Spec(
                "verified-extract",
                "Composites",
                "Extract from documents then verify graph integrity (composite).",
                verified_extract_composite.VerifiedExtractInput,
                verified_extract_composite.verified_extract,
                False,
            ),
            _Spec(
                "enrichment-crawl",
                "Composites",
                "Crawl under-described frontier nodes and propose enrichment relations "
                "(composite). Needs no LLM: candidates come from structural closure plus "
                "semantic neighbours, so CISC N-sample voting is not applied and numSamples "
                "spends nothing (reported as a boundary). WRITES when dryRun is false "
                "(default true): each surviving candidate is created via create-relation.",
                enrichment_crawl_composite.EnrichmentCrawlInput,
                enrichment_crawl_composite.enrichment_crawl,
                True,
            ),
            _Spec(
                "simulate-change",
                "Composites",
                "Simulate graph mutations and preview structural impact (composite).",
                simulate_change_composite.SimulateChangeInput,
                simulate_change_composite.simulate_change,
                False,
            ),
            _Spec(
                "creativity-loop",
                "Composites",
                "Run the autonomous creativity loop: explore, retrieve, transfer, score, "
                "accept/reject, learn (composite). Read-only and deterministic — no LLM; it "
                "stops early on consecutive empty cycles or a plateau. The analogy trigger "
                "queue is reported per cycle, never drained.",
                creativity_loop_composite.CreativityLoopInput,
                creativity_loop_composite.creativity_loop,
                True,
            ),
            _Spec(
                "propose-entities",
                "Composites",
                "Propose new entities that should exist in the knowledge graph (composite).",
                propose_entities_composite.ProposeEntitiesInput,
                propose_entities_composite.propose_entities,
                True,
            ),
            _Spec(
                "analogy-transfer",
                "Composites",
                "Generate novel entities via CWSG analogy transfer from cross-domain mappings "
                "(composite).",
                analogy_transfer_composite.AnalogyTransferInput,
                analogy_transfer_composite.analogy_transfer,
                False,
            ),
            _Spec(
                "gap-fill-cycle",
                "Composites",
                "Automated gap-filling with validation (composite). WRITES: a suggestion "
                "that clears the structural gate and the commitThreshold is created, so "
                "commitThreshold is a real mutation switch, not a report-only score.",
                gap_fill_cycle_composite.GapFillCycleInput,
                gap_fill_cycle_composite.gap_fill_cycle,
                True,
            ),
            _Spec(
                "hypothesis-engine",
                "Composites",
                "Generate and rank hypotheses from semantic gaps (composite).",
                hypothesis_engine_composite.HypothesisEngineInput,
                hypothesis_engine_composite.hypothesis_engine,
                True,
            ),
            _Spec(
                "far-analogy-retrieval",
                "Composites",
                "Run the full far-analogy retrieval pipeline: fingerprint, match, slip, transfer, "
                "score (composite).",
                far_analogy_retrieval_composite.FarAnalogyRetrievalInput,
                far_analogy_retrieval_composite.far_analogy_retrieval,
                True,
            ),
            _Spec(
                "self-improve",
                "Composites",
                "Autonomous self-improvement cycle: reconnaissance, capability check, propose, "
                "simulate, rank, apply (composite).",
                self_improve_composite.SelfImproveInput,
                self_improve_composite.self_improve,
                True,
            ),
            _Spec(
                "explore-frontier",
                "Composites",
                "Rank frontier regions by foraging signals with MVT advice and anti-pattern "
                "guards (composite).",
                explore_frontier_composite.ExploreFrontierInput,
                explore_frontier_composite.explore_frontier,
                True,
            ),
        ]
    )


def _tail_commands() -> list[CommandDescriptor]:
    """Everything not grouped by one of the category functions above: pattern
    reification/triggers, Multi-Graph, and Visualization. Same single
    construction path as every other block."""
    return _build(
        [
            _Spec(
                "reify-patterns",
                "Graph Analytics",
                "Reify recurring structural motifs as pattern entities.",
                reification_ops.ReifyPatternsInput,
                reification_ops.reify_patterns,
                True,
            ),
            _Spec(
                "trigger-status",
                "Epistemic Queries",
                "Status of the analogy trigger queue.",
                reification_ops.TriggerStatusInput,
                reification_ops.trigger_status,
                True,
            ),
            _Spec(
                "process-triggers",
                "Epistemic Queries",
                "Dequeue pending analogy trigger candidates.",
                reification_ops.ProcessTriggersInput,
                reification_ops.process_triggers,
                True,
            ),
            _Spec(
                "list-graphs",
                "Multi-Graph",
                "List all available graphs with their loaded status and stats.",
                EmptyInput,
                lambda _, multi: multi.list_graphs(),
                True,
            ),
            _Spec(
                "create-graph",
                "Multi-Graph",
                "Create a new empty graph.",
                GraphNameInput,
                _create_graph,
                False,
            ),
            _Spec(
                "delete-graph",
                "Multi-Graph",
                "Delete an existing graph.",
                GraphNameInput,
                _delete_graph,
                False,
            ),
            _Spec(
                "list-bridges",
                "Multi-Graph",
                "List cross-graph bridge relations.",
                BridgeFilterInput,
                _list_bridges,
                True,
            ),
            _Spec(
                "find-related-graphs",
                "Multi-Graph",
                "Find graphs connected to a graph via bridge relations.",
                multigraph_ops.GraphInput,
                multigraph_ops.find_related_graphs,
                False,
            ),
            _Spec(
                "graph-connections",
                "Multi-Graph",
                "Get bridge counts between all connected graph pairs.",
                multigraph_ops.EmptyInput,
                multigraph_ops.graph_connections,
                True,
            ),
            _Spec(
                "export-bundle",
                "Visualization",
                "Assemble the TapestryBundle JSON for a graph scope.",
                ExportBundleInput,
                assemble_bundle,
                True,
            ),
            _Spec(
                "export-graph",
                "Visualization",
                "Write a compact, zero-infrastructure node-link JSON export of a graph.",
                portability_ops.ExportGraphInput,
                portability_ops.export_graph,
                False,
            ),
            _Spec(
                "visualize",
                "Visualization",
                "Write a self-contained interactive HTML visualization of a graph scope.",
                VisualizeInput,
                write_visualization,
                True,
            ),
            _Spec(
                "serve",
                "Visualization",
                "Serve the interactive visualization live over a read-only REST API.",
                viz_serve.ServeInput,
                viz_serve.serve,
                True,
            ),
        ]
    )


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
    *_consumption_commands(),
    *_work_memory_commands(),
    *_composite_commands(),
    *_tail_commands(),
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
        raise describe_validation_error(descriptor.input_model, exc, command=name) from exc
    assert descriptor.handler is not None
    return descriptor.handler(params, multi)
