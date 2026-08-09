"""Document operations.

Documents are global (not graph-scoped): every command in this module accepts
a `graph` field for schema compatibility but never applies it — real scoping
only happens later, when entities are extracted from an ingested document via
extract-from-documents. Supplying `graph` here used to vanish with no trace
(TL-485); every handler now attaches a `PARAMETER_IGNORED` notice instead, and
each field's schema description says the same thing, so an agent reading
--schema/COMMANDS.md never has to discover this by trial and error. Chunks
live in FalkorDB. analyze-category uses threshold Union-Find clustering;
because embeddings come from fastembed, cluster *membership* is
embedding-ranked rather than exact — the error/empty paths are fully
deterministic.
"""

from __future__ import annotations

import math
import re
from typing import Annotated, Any

from pydantic import AfterValidator, Field, StringConstraints

from theloom.documents.ingestion import (
    DocumentIngestion,
    IngestionError,
    IngestionNotFoundError,
    IngestionValidationError,
)
from theloom.errors import NotFoundError, OperationError, ValidationError
from theloom.operations.common import CommandInput
from theloom.operations.notices import list_envelope, notice, with_notices
from theloom.semantic.embed import cosine_similarity
from theloom.store.multigraph import MultiGraph

_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")


def _validate_url(value: str) -> str:
    """URL validation: reject strings the WHATWG URL parser rejects
    (no scheme). Protocol allow-listing happens later, at fetch time."""
    if not _SCHEME_RE.match(value):
        raise ValueError("Invalid URL")
    return value


UrlStr = Annotated[str, AfterValidator(_validate_url)]

Doc = dict[str, Any]

DEFAULT_CATEGORY_TOP_K = 10
DEFAULT_CATEGORY_THRESHOLD = 0.75
DEFAULT_CATEGORY_MIN_CLUSTER_SIZE = 3
DEFAULT_CATEGORY_MAX_CHUNKS = 2000
MAX_CATEGORY_CHUNKS_LIMIT = 10000
MAX_LABEL_LENGTH = 120
MAX_TOP_SOURCES = 5
MAX_ADAPTIVE_THRESHOLD = 0.95
ADAPTIVE_THRESHOLD_START = 500
MAX_CLUSTER_FRACTION = 0.5

_GRAPH_FIELD_DESCRIPTION = (
    "Ignored: documents are global, not graph-scoped, so this has no effect "
    "on where the document is stored or which chunks are returned. Graph "
    "scoping happens later, when entities are extracted from the document "
    "via extract-from-documents. Supplying this returns a PARAMETER_IGNORED "
    "notice in the response."
)

_GRAPH_IGNORED_NOTICE_HINT = "Graph scoping happens later, at extract-from-documents time."


def _graph_ignored_notices(params: Any) -> list[Doc]:
    """A `PARAMETER_IGNORED` notice iff the caller supplied `graph` (TL-485).

    Documents are global, so `graph` is accepted for schema compatibility but
    never consulted; silently accepting it would let a caller believe the
    document was scoped to that graph when it never was. Returns `[]` (never
    added to a response — see `with_notices`) when `graph` was not supplied,
    so a clean call's response shape is unchanged.
    """
    if params.graph is None:
        return []
    return [
        notice(
            "PARAMETER_IGNORED",
            "documents are global; the graph parameter was not applied",
            hint=_GRAPH_IGNORED_NOTICE_HINT,
        )
    ]


def _engine(multi: MultiGraph) -> DocumentIngestion:
    return DocumentIngestion(multi.chunk_store())


def _chunk_options(params: Any) -> Doc:
    options: Doc = {}
    if params.chunk_strategy is not None:
        options["strategy"] = params.chunk_strategy
    if params.target_chunk_size is not None:
        options["targetSize"] = params.target_chunk_size
    if params.overlap is not None:
        options["overlapSentences"] = params.overlap
    return options


# =============================================================================
# Input models (snake_case wire fields)
# =============================================================================

ChunkStrategy = Annotated[str, StringConstraints(pattern=r"^(structural|paragraph|fixed)$")]


class IngestDocumentInput(CommandInput):
    file_path: str
    category: str | None = None
    title: str | None = None
    chunk_strategy: ChunkStrategy | None = None
    target_chunk_size: int | None = Field(default=None, gt=0)
    overlap: int | None = Field(default=None, ge=0)
    graph: str | None = Field(default=None, description=_GRAPH_FIELD_DESCRIPTION)


class IngestDirectoryInput(CommandInput):
    dir_path: str
    pattern: str | None = None
    category: str | None = None
    chunk_strategy: ChunkStrategy | None = None
    target_chunk_size: int | None = Field(default=None, gt=0)
    overlap: int | None = Field(default=None, ge=0)
    graph: str | None = Field(default=None, description=_GRAPH_FIELD_DESCRIPTION)


class IngestUrlInput(CommandInput):
    url: UrlStr
    category: str | None = None
    title: str | None = None
    chunk_strategy: ChunkStrategy | None = None
    target_chunk_size: int | None = Field(default=None, gt=0)
    overlap: int | None = Field(default=None, ge=0)
    graph: str | None = Field(default=None, description=_GRAPH_FIELD_DESCRIPTION)


class IngestContentInput(CommandInput):
    content: str
    source_id: str
    format: Annotated[str, StringConstraints(pattern=r"^(markdown|html|txt)$")]
    category: str | None = None
    title: str | None = None
    chunk_strategy: ChunkStrategy | None = None
    target_chunk_size: int | None = Field(default=None, gt=0)
    overlap: int | None = Field(default=None, ge=0)
    graph: str | None = Field(default=None, description=_GRAPH_FIELD_DESCRIPTION)


class ListDocumentsInput(CommandInput):
    category: str | None = None
    graph: str | None = Field(default=None, description=_GRAPH_FIELD_DESCRIPTION)


class DeleteDocumentInput(CommandInput):
    source_id: str
    graph: str | None = Field(default=None, description=_GRAPH_FIELD_DESCRIPTION)


class ReingestDocumentInput(CommandInput):
    source_id: str
    file_path: str | None = None
    chunk_strategy: ChunkStrategy | None = None
    target_chunk_size: int | None = Field(default=None, gt=0)
    overlap: int | None = Field(default=None, ge=0)
    graph: str | None = Field(default=None, description=_GRAPH_FIELD_DESCRIPTION)


class AnalyzeCategoryInput(CommandInput):
    category: str = Field(min_length=1)
    top_k: int | None = Field(default=None, gt=0, alias="topK")
    similarity_threshold: float | None = Field(
        default=None, ge=0, le=1, alias="similarityThreshold"
    )
    min_cluster_size: int | None = Field(default=None, gt=0, alias="minClusterSize")
    max_chunks: int | None = Field(default=None, gt=0, le=10000, alias="maxChunks")
    graph: str | None = Field(default=None, description=_GRAPH_FIELD_DESCRIPTION)


# =============================================================================
# Handlers
# =============================================================================


def _translate(exc: IngestionError) -> Exception:
    """Map an ingestion failure to its typed CLI error, by the exception's
    class — never by pattern-matching its message text."""
    message = str(exc)
    if isinstance(exc, IngestionNotFoundError):
        return NotFoundError(message)
    if isinstance(exc, IngestionValidationError):
        return ValidationError(message)
    return OperationError(message)


def ingest_document(params: IngestDocumentInput, multi: MultiGraph) -> Doc:
    options = {
        "category": params.category,
        "title": params.title,
        "chunkOptions": _chunk_options(params),
    }
    try:
        result = _engine(multi).ingest_from_file(params.file_path, options)
    except IngestionError as exc:
        raise _translate(exc) from exc
    return with_notices(result, notices=_graph_ignored_notices(params))


def ingest_directory(params: IngestDirectoryInput, multi: MultiGraph) -> Doc:
    options = {"category": params.category, "chunkOptions": _chunk_options(params)}
    try:
        results = _engine(multi).ingest_directory(params.dir_path, params.pattern, options)
    except IngestionError as exc:
        raise _translate(exc) from exc
    return list_envelope(results, notices=_graph_ignored_notices(params))


def ingest_url(params: IngestUrlInput, multi: MultiGraph) -> Doc:
    options = {
        "category": params.category,
        "title": params.title,
        "chunkOptions": _chunk_options(params),
    }
    try:
        result = _engine(multi).ingest_url(params.url, options)
    except IngestionError as exc:
        raise _translate(exc) from exc
    return with_notices(result, notices=_graph_ignored_notices(params))


def ingest_content(params: IngestContentInput, multi: MultiGraph) -> Doc:
    options = {
        "category": params.category,
        "title": params.title,
        "chunkOptions": _chunk_options(params),
    }
    try:
        result = _engine(multi).ingest_content(
            params.source_id, params.content, params.format, options
        )
    except IngestionError as exc:
        raise _translate(exc) from exc
    return with_notices(result, notices=_graph_ignored_notices(params))


def list_documents(params: ListDocumentsInput, multi: MultiGraph) -> Doc:
    results = _engine(multi).list_documents(params.category)
    return list_envelope(results, notices=_graph_ignored_notices(params))


def delete_document(params: DeleteDocumentInput, multi: MultiGraph) -> Doc:
    try:
        result = _engine(multi).delete_document(params.source_id)
    except IngestionError as exc:
        raise _translate(exc) from exc
    return with_notices(result, notices=_graph_ignored_notices(params))


def reingest_document(params: ReingestDocumentInput, multi: MultiGraph) -> Doc:
    options = {"chunkOptions": _chunk_options(params)}
    try:
        result = _engine(multi).reingest(params.source_id, options)
    except IngestionError as exc:
        raise _translate(exc) from exc
    return with_notices(result, notices=_graph_ignored_notices(params))


# =============================================================================
# analyze-category (Union-Find threshold clustering)
# =============================================================================


class _UnionFind:
    def __init__(self, elements: list[str]) -> None:
        self._parent = {e: e for e in elements}
        self._rank = {e: 0 for e in elements}

    def find(self, x: str) -> str:
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        current = x
        while current != root:
            nxt = self._parent[current]
            self._parent[current] = root
            current = nxt
        return root

    def union(self, a: str, b: str) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a == root_b:
            return
        rank_a, rank_b = self._rank[root_a], self._rank[root_b]
        if rank_a < rank_b:
            self._parent[root_a] = root_b
        elif rank_a > rank_b:
            self._parent[root_b] = root_a
        else:
            self._parent[root_b] = root_a
            self._rank[root_a] = rank_a + 1


# Chunk vectors are L2-normalized, so the shared cosine is the dot product
# these call sites always meant.
_cosine = cosine_similarity


def _derive_theme_label(metadata: Doc) -> str:
    heading = metadata.get("sectionHeading")
    if heading and heading.strip():
        return str(heading).strip()
    content = metadata.get("content")
    if content and content.strip():
        text = str(content).strip()
        if len(text) <= MAX_LABEL_LENGTH:
            return text
        return text[:MAX_LABEL_LENGTH].rstrip() + "..."
    return str(metadata.get("sourceName") or "Unknown theme")


def _stratified_sample(items: list[Any], n: int) -> list[Any]:
    if n >= len(items):
        return items
    groups: dict[str, list[Any]] = {}
    for item in items:
        key = item[0].get("sourceId") or "__unknown__"
        groups.setdefault(key, []).append(item)
    min_per_source = max(1, n // len(groups))
    sampled: list[Any] = []
    for group_items in groups.values():
        quota = min(len(group_items), min_per_source)
        step = len(group_items) / quota
        i = 0
        while i < quota and len(sampled) < n:
            sampled.append(group_items[math.floor(i * step)])
            i += 1
    if len(sampled) < n:
        sampled_set = {id(s) for s in sampled}
        for group_items in sorted(groups.values(), key=lambda g: -len(g)):
            for item in group_items:
                if id(item) not in sampled_set:
                    sampled.append(item)
                    sampled_set.add(id(item))
                    if len(sampled) >= n:
                        return sampled
    return sampled


def analyze_category(params: AnalyzeCategoryInput, multi: MultiGraph) -> Doc:
    top_k = params.top_k if params.top_k is not None else DEFAULT_CATEGORY_TOP_K
    threshold = (
        params.similarity_threshold
        if params.similarity_threshold is not None
        else DEFAULT_CATEGORY_THRESHOLD
    )
    min_cluster_size = (
        params.min_cluster_size
        if params.min_cluster_size is not None
        else DEFAULT_CATEGORY_MIN_CLUSTER_SIZE
    )
    max_chunks = min(
        params.max_chunks if params.max_chunks is not None else DEFAULT_CATEGORY_MAX_CHUNKS,
        MAX_CATEGORY_CHUNKS_LIMIT,
    )

    store = multi.chunk_store()
    all_chunks = [
        (meta, vector)
        for meta, vector in store.query_chunks_with_vectors(category=params.category, limit=100000)
        if vector is not None
    ]
    total_chunks = len(all_chunks)
    if total_chunks == 0:
        raise OperationError(
            f"No document chunks found for category '{params.category}'. "
            "Ingest documents with this category first using ingest-document."
        )

    chunks = _stratified_sample(all_chunks, max_chunks) if total_chunks > max_chunks else all_chunks
    chunks_analyzed = len(chunks)
    total_documents = len({m.get("sourceId") for m, _ in all_chunks if m.get("sourceId")})

    effective_threshold = threshold
    if chunks_analyzed > ADAPTIVE_THRESHOLD_START:
        scale = min(1.0, (chunks_analyzed - ADAPTIVE_THRESHOLD_START) / 1500)
        effective_threshold = max(
            threshold,
            min(MAX_ADAPTIVE_THRESHOLD, threshold + scale * (MAX_ADAPTIVE_THRESHOLD - threshold)),
        )

    uf = _UnionFind([str(i) for i in range(len(chunks))])
    edge_similarities: dict[str, list[float]] = {}
    for i in range(len(chunks)):
        for j in range(i + 1, len(chunks)):
            sim = _cosine(chunks[i][1], chunks[j][1])
            if sim >= effective_threshold:
                uf.union(str(i), str(j))
                edge_similarities.setdefault(f"{i}:{j}", []).append(sim)

    cluster_map: dict[str, list[int]] = {}
    for i in range(len(chunks)):
        cluster_map.setdefault(uf.find(str(i)), []).append(i)

    max_cluster_size = max(min_cluster_size * 2, math.floor(chunks_analyzed * MAX_CLUSTER_FRACTION))
    oversized = [
        (root, members) for root, members in cluster_map.items() if len(members) > max_cluster_size
    ]
    for root, member_indices in oversized:
        tighter = min(effective_threshold + 0.1, 0.98)
        sub_uf = _UnionFind([str(i) for i in range(len(member_indices))])
        for i in range(len(member_indices)):
            for j in range(i + 1, len(member_indices)):
                if _cosine(chunks[member_indices[i]][1], chunks[member_indices[j]][1]) >= tighter:
                    sub_uf.union(str(i), str(j))
        sub_map: dict[str, list[int]] = {}
        for i in range(len(member_indices)):
            sub_map.setdefault(sub_uf.find(str(i)), []).append(member_indices[i])
        del cluster_map[root]
        for sub_root, sub_members in sub_map.items():
            cluster_map[f"{root}_{sub_root}"] = sub_members

    themes: list[Doc] = []
    unclustered = 0
    for member_indices in cluster_map.values():
        if len(member_indices) < min_cluster_size:
            unclustered += len(member_indices)
            continue
        dim = len(chunks[member_indices[0]][1])
        centroid = [0.0] * dim
        for idx in member_indices:
            vec = chunks[idx][1]
            for d in range(dim):
                centroid[d] += vec[d]
        for d in range(dim):
            centroid[d] /= len(member_indices)
        best_idx, best_sim = member_indices[0], -1.0
        for idx in member_indices:
            sim = _cosine(chunks[idx][1], centroid)
            if sim > best_sim:
                best_sim, best_idx = sim, idx
        representative = chunks[best_idx][0]

        member_set = set(member_indices)
        total_sim = 0.0
        edge_count = 0
        for pair_key, scores in edge_similarities.items():
            a, b = (int(x) for x in pair_key.split(":"))
            if a in member_set and b in member_set:
                total_sim += sum(scores) / len(scores)
                edge_count += 1
        avg_similarity = total_sim / edge_count if edge_count > 0 else 0

        source_counts: dict[str, int] = {}
        for idx in member_indices:
            sn = chunks[idx][0].get("sourceName") or "Unknown"
            source_counts[sn] = source_counts.get(sn, 0) + 1
        top_sources = [
            {"sourceName": sn, "chunkCount": count}
            for sn, count in sorted(source_counts.items(), key=lambda kv: -kv[1])[:MAX_TOP_SOURCES]
        ]

        rep_chunk: Doc = {
            "content": representative.get("content") or "",
            "sourceName": representative.get("sourceName") or "Unknown",
        }
        if representative.get("sectionHeading"):
            rep_chunk["sectionHeading"] = representative["sectionHeading"]

        themes.append(
            {
                "rank": 0,
                "label": _derive_theme_label(representative),
                "chunkCount": len(member_indices),
                "percentage": (len(member_indices) / chunks_analyzed) * 100,
                "avgSimilarity": avg_similarity,
                "representativeChunk": rep_chunk,
                "topSources": top_sources,
            }
        )

    themes.sort(key=lambda t: -t["chunkCount"])
    top_themes = themes[:top_k]
    for i, theme in enumerate(top_themes):
        theme["rank"] = i + 1

    result = {
        "category": params.category,
        "totalDocuments": total_documents,
        "totalChunks": total_chunks,
        "chunksAnalyzed": chunks_analyzed,
        "themes": top_themes,
        "unclustered": unclustered,
    }
    return with_notices(result, notices=_graph_ignored_notices(params))
