"""Extraction operations.

Deterministic: extract-codebase (tree-sitter, deterministic against a fixed
repo), extraction-status, extraction-rollback (event-log-backed run store, so
these survive across CLI processes). update-codebase/self-model-update are the
deterministic git-diff paths. extract-from-documents/extract-preview are the
LLM pipeline: they route through the configured LLM (local or Anthropic) and
error when none is configured.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from theloom.errors import NotFoundError, OperationError, ValidationError
from theloom.extraction import treesitter
from theloom.model import RelationFilter
from theloom.operations.bulk import BulkImportInput, bulk_import
from theloom.operations.common import CommandInput
from theloom.store.falkor import FalkorGraphStore
from theloom.store.multigraph import MultiGraph
from theloom.synthesis.llm import create_synthesis_client
from theloom.timeutil import iso_now

Doc = dict[str, Any]


# =============================================================================
# Input models
# =============================================================================


_INCLUDE_DESC = (
    "Only collect files whose project-relative path matches one of these "
    'fnmatch globs (e.g. "src/*", "**/*.py"); unset or empty means no '
    "restriction."
)
_EXCLUDE_DESC = (
    "Never collect files whose project-relative path matches one of these "
    "fnmatch globs; takes priority over `include` when a path matches both."
)


class ExtractCodebaseInput(CommandInput):
    project_path: str = Field(alias="projectPath")
    graph: str | None = None
    include_tests: bool | None = Field(default=None, alias="includeTests")
    include: list[str] | None = Field(default=None, description=_INCLUDE_DESC)
    exclude: list[str] | None = Field(default=None, description=_EXCLUDE_DESC)
    dry_run: bool | None = Field(default=None, alias="dryRun")


class UpdateCodebaseInput(CommandInput):
    project_path: str = Field(alias="projectPath")
    graph_name: str = Field(alias="graphName")
    git_ref: str | None = Field(default=None, alias="gitRef")
    include_tests: bool | None = Field(default=None, alias="includeTests")
    include: list[str] | None = Field(default=None, description=_INCLUDE_DESC)
    exclude: list[str] | None = Field(default=None, description=_EXCLUDE_DESC)
    dry_run: bool | None = Field(default=None, alias="dryRun")
    # Override the shrink guard (a collapsed extraction is refused by default).
    force: bool | None = None


class SelfModelUpdateInput(CommandInput):
    project_path: str | None = Field(default=None, alias="projectPath")
    graph_name: str | None = Field(default=None, alias="graphName")
    dry_run: bool | None = Field(default=None, alias="dryRun")


class ExtractFromDocumentsInput(CommandInput):
    category: str | None = None
    document_id: str | None = Field(default=None, alias="documentId")
    query: str | None = None
    max_chunks: int | None = Field(default=None, gt=0, le=10000, alias="maxChunks")
    model: str | None = None
    focus: str | None = None
    dry_run: bool | None = Field(default=None, alias="dryRun")
    graph: str | None = None


class ExtractPreviewInput(CommandInput):
    category: str | None = None
    document_id: str | None = Field(default=None, alias="documentId")
    query: str | None = None
    max_chunks: int | None = Field(default=None, gt=0, le=10000, alias="maxChunks")
    model: str | None = None
    focus: str | None = None
    graph: str | None = None


class ExtractionStatusInput(CommandInput):
    run_id: str | None = Field(default=None, alias="runId")


class ExtractionRollbackInput(CommandInput):
    run_id: str = Field(alias="runId")
    graph: str | None = None


# =============================================================================
# Codebase extraction (deterministic)
# =============================================================================


# Every pre-``calls`` call edge spelled its verb the same way, whichever pass
# emitted it: "<caller> calls <callee>", optionally continued with ", imported
# from ..." or ", the project's only symbol of that name". Nothing else the
# extractor wrote as ``related_to`` carries that verb, and a match only counts
# on a pair a fresh ``calls`` edge already joins.
_LEGACY_CALL_VERB = " calls "


def _retire_legacy_call_edges(
    store: FalkorGraphStore,
    relations: list[Doc],
    mapping: dict[str, str],
    *,
    dry_run: bool,
) -> int:
    """Close out the ``related_to`` edges the fresh ``calls`` edges replace.

    Bulk import is strictly additive — it skips an existing edge of the same
    type and creates the rest — so on a graph extracted before call edges were
    typed, a re-extract would otherwise leave every call as two parallel edges
    (the legacy ``related_to`` plus the new ``calls``), doubling the degree that
    cycles, centrality and components are computed from. Retirement is
    bi-temporal (``invalidate_relation``): the edge leaves the live projection,
    its history stays. ``dry_run`` counts without writing.
    """
    pairs = {
        (mapping[relation["from"]], mapping[relation["to"]])
        for relation in relations
        if relation.get("relationType") == "calls"
        and relation.get("from") in mapping
        and relation.get("to") in mapping
    }
    if not pairs:
        return 0

    retired = 0
    legacy_filter = RelationFilter.model_validate({"relationType": "related_to"})
    for relation in store.list_relations(legacy_filter):
        if (relation.from_, relation.to) not in pairs:
            continue
        if not relation.evidence or _LEGACY_CALL_VERB not in relation.evidence:
            continue
        retired += 1
        if not dry_run:
            store.invalidate_relation(
                relation.from_, relation.to, "related_to", relation_id=relation.id
            )
    return retired


def extract_codebase(params: ExtractCodebaseInput, multi: MultiGraph) -> Doc:
    """Extract a codebase into a graph via tree-sitter.

    Call edges are typed ``calls`` and anchored at their call site
    (``<caller> calls <callee> at <file>:<line>``). Graphs extracted before that
    change carry their call edges as ``related_to``, indistinguishable from the
    semantic layer's grounding links; re-running this command over the project
    *is* the migration (structural re-extraction of a repo this size takes about
    two minutes). Import is additive, so the run also retires the legacy twins:
    every ``related_to`` edge whose endpoints a fresh ``calls`` edge now joins
    and whose evidence is the old extractor's call prose is closed out
    bi-temporally (``legacyCallEdgesRetired``) rather than left to double the
    call structure. After a re-extract, ``related_to`` means a semantic link and
    nothing else.
    """
    # The tool handler wraps failures as
    # "Error in codebase extraction: <msg>"; "does not exist" then classifies
    # as OPERATION_ERROR (not NOT_FOUND).
    started_at = iso_now()
    try:
        extraction = treesitter.extract_codebase(
            params.project_path,
            include_tests=params.include_tests if params.include_tests is not None else True,
            include=params.include,
            exclude=params.exclude,
        )
    except FileNotFoundError as exc:
        raise OperationError(f"Error in codebase extraction: {exc}") from exc

    result: Doc = {
        "stats": extraction["stats"],
        "indexPath": extraction["indexPath"],
        "extractionMethod": extraction["extractionMethod"],
    }
    if params.graph is not None:
        dry_run = params.dry_run or False
        import_result = bulk_import(
            BulkImportInput.model_validate(
                {
                    "entities": extraction["entities"],
                    "relations": extraction["relations"],
                    "graph": params.graph,
                    "dryRun": dry_run,
                }
            ),
            multi,
        )
        result["importResult"] = import_result
        result["legacyCallEdgesRetired"] = _retire_legacy_call_edges(
            multi.get_store(params.graph),
            extraction["relations"],
            import_result["mapping"],
            dry_run=dry_run,
        )
        result["runId"] = multi.run_store().save_codebase_run(
            started_at=started_at,
            created_entity_ids=import_result["createdEntityIds"],
            created_relation_ids=import_result["createdRelationIds"],
            dry_run=dry_run,
        )
    return result


def update_codebase(params: UpdateCodebaseInput, multi: MultiGraph) -> Doc:
    """Replay a git diff over an existing codebase graph.

    Per changed file the update replaces what that file contributed: vanished
    entities are superseded, edges it sourced are re-diffed and the stale ones
    closed out bi-temporally. A collapsed extraction (a still-present file that
    now yields nothing, or an update that would supersede more than half the
    graph) is refused with OPERATION_ERROR unless ``force`` is set.
    """
    from theloom.extraction.codebasediff import update_codebase_diff

    try:
        return update_codebase_diff(
            params.project_path,
            params.graph_name,
            git_ref=params.git_ref or "HEAD~1..HEAD",
            include_tests=params.include_tests if params.include_tests is not None else True,
            include=params.include,
            exclude=params.exclude,
            dry_run=params.dry_run or False,
            force=params.force or False,
            multi=multi,
        )
    except FileNotFoundError as exc:
        raise NotFoundError(str(exc)) from exc


def self_model_update(params: SelfModelUpdateInput, multi: MultiGraph) -> Doc:
    from theloom.extraction.selfmodel import update_self_model

    try:
        return update_self_model(
            project_path=params.project_path,
            graph_name=params.graph_name or "loom-codebase",
            dry_run=params.dry_run or False,
            multi=multi,
        )
    except FileNotFoundError as exc:
        raise NotFoundError(str(exc)) from exc


# =============================================================================
# Extraction runs (deterministic: status/rollback)
# =============================================================================


def extraction_status(params: ExtractionStatusInput, multi: MultiGraph) -> Any:
    store = multi.run_store()
    if params.run_id is not None:
        run = store.get_run(params.run_id)
        if run is None:
            raise NotFoundError(f"Extraction run '{params.run_id}' not found")
        return run
    return store.list_runs()


def extraction_rollback(params: ExtractionRollbackInput, multi: MultiGraph) -> Doc:
    store = multi.run_store()
    run = store.get_run(params.run_id)
    if run is None:
        raise NotFoundError(f"Extraction run '{params.run_id}' not found")

    graph_store = multi.get_store(params.graph)
    deleted_relations = 0
    for relation_id in run.get("createdRelationIds", []):
        parts = relation_id.split("->")
        if len(parts) >= 2:
            # The run recorded "<from>-><to>-><type>"; the type is load-bearing.
            # A pair accumulates one typed edge per run, and without the type
            # the store's untyped target rule picks the *oldest* edge — an edge
            # an earlier run created, which this rollback must not touch.
            relation_type = parts[2] if len(parts) >= 3 else None
            try:
                # A rollback undoes a run that should never have landed, so it
                # erases rather than retracts — there is no history to keep.
                graph_store.delete_relation(parts[0], parts[1], relation_type, hard=True)
                deleted_relations += 1
            except Exception:
                pass

    deleted_entities = 0
    ordered_ids = [
        *run.get("convergenceEntityIds", []),
        *run.get("synthesisEntityIds", []),
        *run.get("createdEntityIds", []),
        *run.get("sourceEntityIds", []),
    ]
    for entity_id in ordered_ids:
        try:
            graph_store.delete_entity(entity_id, hard=True)
            deleted_entities += 1
        except Exception:
            pass

    return {
        "deletedEntities": deleted_entities,
        "deletedRelations": deleted_relations,
        "deletedLinks": 0,
    }


# =============================================================================
# LLM document extraction (LLM-dependent)
# =============================================================================


def _require_extraction_llm() -> Any:
    """Route through the configured LLM (local or Anthropic); when nothing is
    configured, raise a typed error. The message names LLM config generally,
    not the Anthropic key specifically."""
    client = create_synthesis_client()
    if client is None:
        raise ValidationError(
            "No LLM configured for extraction. Set an `llm` config section "
            "(provider ollama|mlx|openai|anthropic) or ANTHROPIC_API_KEY."
        )
    return client


def extract_from_documents(params: ExtractFromDocumentsInput, multi: MultiGraph) -> Doc:
    if not params.category and not params.document_id and not params.query:
        raise ValidationError("At least one of category, documentId, or query is required")
    _require_extraction_llm()
    from theloom.extraction.pipeline import run_document_extraction

    return run_document_extraction(params, multi, dry_run=params.dry_run or False)


def extract_preview(params: ExtractPreviewInput, multi: MultiGraph) -> Doc:
    if not params.category and not params.document_id and not params.query:
        raise ValidationError("At least one of category, documentId, or query is required")
    _require_extraction_llm()
    from theloom.extraction.pipeline import run_document_extraction

    forwarded = ExtractFromDocumentsInput.model_validate(
        {
            **params.model_dump(by_alias=True, exclude_none=True),
            "maxChunks": params.max_chunks if params.max_chunks is not None else 5,
            "dryRun": True,
        }
    )
    return run_document_extraction(forwarded, multi, dry_run=True)


# OperationError is imported for symmetry with sibling ops modules.
_ = OperationError
