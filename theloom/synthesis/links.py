"""Entity -> source-chunk lookups.

An entity extracted from a document carries a *pointer* to the chunk it came
from: ``provenance.externalRef`` holds the chunk id, alongside
``provenance.sourceId``, which names the in-graph "Document: X" source entity.
(Same idiom as tree-sitter extraction, where ``externalRef`` is a
``file.py:12`` locator rather than an id in the graph.)

Resolving that pointer is the caller's job — these functions take a
``chunk_lookup`` callable (in practice ``ChunkStore.get_chunk``) so they stay
pure and testable, and so this module never has to know that chunks live in
their own per-prefix graph.

An entity with no such pointer — anything created by hand, by tree-sitter, or
by an extraction run predating the pointer — resolves to nothing. So does one
whose chunks have since been deleted. Empty is the honest answer in both
cases; neither is an error.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

Doc = dict[str, Any]

ChunkLookup = Callable[[str], Doc | None]

# The only sourceType whose externalRef is a chunk id. Every other extractor
# puts a different kind of locator there (tree-sitter: "path:line"), and
# looking those up would be a category error, not a miss.
_CHUNK_SOURCE_TYPE = "document"


def _chunk_id(entity: Doc) -> str | None:
    provenance = entity.get("provenance") or {}
    if provenance.get("sourceType") != _CHUNK_SOURCE_TYPE:
        return None
    external_ref = provenance.get("externalRef")
    return external_ref if isinstance(external_ref, str) and external_ref else None


def get_links_for_entity(entity: Doc, chunk_lookup: ChunkLookup) -> list[Doc]:
    """The source chunks this entity was extracted from, as ``{chunkId,
    evidence}`` links (at most one — an entity comes from one chunk)."""
    chunk_id = _chunk_id(entity)
    if chunk_id is None:
        return []
    chunk = chunk_lookup(chunk_id)
    if chunk is None:
        return []
    content = chunk.get("content")
    if not isinstance(content, str) or not content:
        return []
    return [{"chunkId": chunk_id, "evidence": content}]


def get_source_passages(entity: Doc, chunk_lookup: ChunkLookup) -> list[str]:
    """The verbatim text of those chunks."""
    return [link["evidence"] for link in get_links_for_entity(entity, chunk_lookup)]
