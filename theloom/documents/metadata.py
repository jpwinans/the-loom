"""The chunk metadata doc, declared.

Every ``:_Chunk`` row carries its metadata as JSON in ``_doc``: the chunk's
identity, the document it came from, its position in that document, and its
text. That shape used to exist only as a dict literal built by hand — twice in
``ingestion`` (ingest and reingest), read back by key in ``chunkstore``, and
filtered on ``category`` by another literal key. Four places knew the schema
and none declared it, so a field added to one writer (or dropped, as
``pageNumber`` was on the reingest path) went unnoticed.

``ChunkMetadata`` is that declaration. It follows the domain model's
conventions — snake_case attributes, camelCase wire aliases, translation left
to the model — and carries the chunk conventions the writers used to repeat:
a chunk is its own entity (``entityId`` = ``id``), it is named after its
document, and its entity/entry type is ``document_chunk``.

Two things it deliberately does not do:

- **Reject unknown keys.** Chunks already in a store were written by older
  code, and a read must return what is there rather than refuse it, so extras
  are kept and round-trip verbatim (``extra="allow"`` — the one place the
  Loom's models depart from ``extra="forbid"``, because this doc is storage
  the model reads back, not input it validates).
- **Require anything.** The same reason: a doc written before a field existed
  is still a chunk. Absent fields stay absent on the way out
  (``exclude_none``), so a read returns the doc that was written.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator
from pydantic.alias_generators import to_camel

Doc = dict[str, Any]

CHUNK_ENTITY_TYPE = "document_chunk"


class ChunkMetadata(BaseModel):
    """The ``_doc`` of one document chunk."""

    # Every wire name here is the plain camelCase of its attribute, so the
    # generator states that rule once instead of 14 literal aliases.
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="allow")

    # Identity. A chunk is its own entity, so entityId mirrors id unless a
    # caller says otherwise (reingest keeps the *stored* id when a chunk's
    # content changes, so the two can differ from the chunker's fresh uuid).
    id: str | None = None
    entity_id: str | None = None
    entity_type: str = CHUNK_ENTITY_TYPE
    entry_type: str = CHUNK_ENTITY_TYPE
    name: str | None = None

    # Content and its fingerprint (reingest diffs on contentHash).
    content: str | None = None
    content_hash: str | None = None
    embedded_at: str | None = None
    embedding_error: str | None = None

    # The document this chunk came from, and where in it.
    source_id: str | None = None
    source_name: str | None = None
    source_format: str | None = None
    source_path: str | None = None
    chunk_index: int | None = None
    total_chunks: int | None = None
    section_heading: str | None = None
    page_number: int | None = None

    # How the document was filed at ingest time.
    category: str | None = None

    @model_validator(mode="after")
    def _apply_chunk_conventions(self) -> ChunkMetadata:
        if self.entity_id is None:
            self.entity_id = self.id
        if self.name is None:
            self.name = self.source_name
        return self

    @classmethod
    def coerce(cls, metadata: ChunkMetadata | Mapping[str, Any]) -> ChunkMetadata:
        """This model, whether the caller passed one or a raw wire doc."""
        if isinstance(metadata, ChunkMetadata):
            return metadata
        return cls.model_validate(dict(metadata))

    @classmethod
    def from_json(cls, stored: str) -> ChunkMetadata:
        """Parse a ``_doc`` read back off a chunk row."""
        return cls.model_validate_json(stored)

    def to_doc(self) -> Doc:
        """The wire doc: camelCase names, absent fields left out."""
        return self.model_dump(by_alias=True, exclude_none=True)
