# 0001. Entity-to-chunk provenance pointers are soft references

## Status

Accepted

## Context

An LLM-extracted entity carries `provenance.externalRef`, a pointer to the
document chunk it was extracted from (alongside `provenance.sourceId`, which
names the in-graph "Document: X" source entity). The chunk itself lives in
`{prefix}:_chunks` — a per-prefix graph that is global, not scoped to any
knowledge graph (see `theloom.documents.chunkstore`). Synthesis resolves this
pointer through `ChunkStore.get_chunk` to quote the originating passage
(`theloom/synthesis/links.py`).

Because the chunk graph and the knowledge graph the entity lives in are two
different FalkorDB graphs, this pointer crosses a graph boundary. FalkorDB
gives no referential integrity across graphs — nothing prevents a chunk named
by `externalRef` from being deleted while entities still point at it, and
nothing enforces the pointer at write time. The architecture invariant that
graph, vectors, and chunks all live in one transactional store (CLAUDE.md
invariant 1) rules out a sidecar registry to track and enforce these
pointers.

Chunks are deleted via `ChunkStore.delete_where_source` (invoked by
`delete-document`), which removes a document's chunks as a single pinned,
event-sourced operation scoped entirely to the chunk graph. It has no
knowledge of, and no write path into, any knowledge graph that may hold
entities pointing at the chunks it just removed.

## Decision

The entity → chunk pointer is deliberately a **soft reference**. No
mechanism keeps it valid across the graph boundary:

- Deleting a document's chunks (`delete-document` / `delete_where_source`)
  does not touch, cascade to, or invalidate any entity's
  `provenance.externalRef` in any knowledge graph.
- A pointer left dangling by such a delete is not an error condition.
  `ChunkStore.get_chunk` reads back `None` for a chunk id that no longer
  exists, and `theloom/synthesis/links.py` treats a `None` lookup (like a
  missing or non-document `externalRef`) as "no passage" —
  `get_source_passages` returns `[]`, never raises. This is pinned by
  `tests/test_synthesis_source_passages.py::TestLinkLookups::test_deleted_chunk_degrades_to_nothing`.
- This is the same "degrade honestly rather than fabricate" ethic the rest
  of `links.py` already applies to entities with no provenance, a null
  `externalRef`, or a non-document `sourceType` (e.g. tree-sitter's
  `file.py:12` locators) — a dangling chunk pointer is just one more input
  that honestly yields nothing rather than a special case.

### Alternatives considered and rejected

- **Read-time hard failure on a dangling pointer.** Would turn "the source
  document was deleted" into an error surfaced through synthesis on every
  affected entity, on every subsequent read, indefinitely. Violates the
  honest-degradation ethic and would break synthesis on any graph that has
  ever had a document deleted post-extraction.
- **Cascading pointer cleanup on document delete.** Would require
  `delete_where_source` (a chunk-graph-only operation) to also locate and
  mutate entities across every knowledge graph that might reference the
  deleted chunks — cross-graph write coordination that the store's commit
  primitive deliberately does not provide (each commit is one graph, one
  MULTI/EXEC unit).
- **A referential-integrity sidecar** tracking chunk-id → entity-id
  back-references to drive cleanup or validation. Reintroduces a second
  store of record for a relationship that spans FalkorDB graphs, violating
  the one-transactional-store invariant (CLAUDE.md invariant 1).

## Consequences

- Source passages can silently vanish: an entity that once resolved to a
  quoted passage will, after its document is deleted, resolve to `[]` with
  no error and no visible signal that anything changed. Callers that expect
  passages to always be present for extracted entities must not assume
  that.
- There is no backfill path for entities extracted before this pointer
  existed — they simply have no `externalRef` and already degrade the same
  way (`test_entity_without_provenance_yields_nothing`,
  `test_null_external_ref_yields_nothing`).
- Nothing warns an operator that a delete-document call has orphaned
  pointers elsewhere; discovering this requires explicitly checking
  synthesis output or comparing entity provenance against
  `ChunkStore.get_chunk` results.
