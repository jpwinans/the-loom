"""Document ingestion engine.

parse -> chunk -> embed -> upsert. sourceId is sha256(absolute path or URL or
caller id)[:32]; chunk metadata carries entryType='document_chunk'. Ingest
counts are deterministic from chunking; embeddings feed search/analyze.
Reingest diffs by contentHash at the same chunkIndex.
"""

from __future__ import annotations

import fnmatch
import hashlib
from pathlib import Path
from typing import Any

from theloom.documents.chunker import chunk_blocks
from theloom.documents.chunkstore import ChunkStore
from theloom.documents.parsers import ParseError, detect_format, parse_document
from theloom.documents.ssrf import SsrfError, fetch_url
from theloom.timeutil import iso_now

Doc = dict[str, Any]

MAX_FILE_SIZE = 50 * 1024 * 1024
DEFAULT_DIR_PATTERN = "**/*.{pdf,docx,md,markdown,html,htm,txt,json}"
_ALLOWED_CONTENT_FORMATS = ("markdown", "html", "txt")


class IngestionError(Exception):
    """Ingestion failure carrying a message that maps to a CLI error code.

    ``theloom/documents`` doesn't import the CLI's typed-error hierarchy
    (``theloom.errors``) — that translation happens at the operations
    boundary (``theloom/operations/documents.py``). These two subclasses let
    that boundary classify structurally, by ``isinstance``, instead of
    pattern-matching the message text.
    """


class IngestionNotFoundError(IngestionError):
    """The requested file, directory, or document does not exist."""


class IngestionValidationError(IngestionError):
    """The caller supplied an invalid ingestion request."""


def _source_id_from_path(file_path: str) -> str:
    resolved = str(Path(file_path).resolve())
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:32]


def _source_id_from_string(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def _embed_texts(texts: list[str]) -> list[list[float]] | None:
    """Best-effort embeddings; None if the model can't load (chunks still
    stored so ingest counts and list-documents stay correct)."""
    try:
        from theloom.semantic.embed import get_embedder

        return get_embedder().embed_documents(texts)
    except Exception:
        return None


class DocumentIngestion:
    def __init__(self, store: ChunkStore) -> None:
        self._store = store

    # -- core -------------------------------------------------------------------

    def _ingest(self, source_id: str, content: str | bytes, fmt: str, options: Doc) -> Doc:
        parsed = parse_document(content, fmt, options.get("title"))
        chunks = chunk_blocks(parsed["blocks"], options.get("chunkOptions"))
        source_name = options.get("title") or source_id
        category = options.get("category")
        now = iso_now()

        texts = [c["content"] for c in chunks]
        vectors = _embed_texts(texts) if texts else []

        chunks_created = 0
        for index, chunk in enumerate(chunks):
            metadata = {
                "id": chunk["id"],
                "entityId": chunk["id"],
                "entityType": "document_chunk",
                "entryType": "document_chunk",
                "name": source_name,
                "contentHash": chunk["contentHash"],
                "embeddedAt": now,
                "sourceId": source_id,
                "sourceName": source_name,
                "sourceFormat": fmt,
                "chunkIndex": chunk["chunkIndex"],
                "totalChunks": len(chunks),
                "content": chunk["content"],
            }
            if category is not None:
                metadata["category"] = category
            if chunk.get("sectionHeading"):
                metadata["sectionHeading"] = chunk["sectionHeading"]
            if chunk.get("pageNumber"):
                metadata["pageNumber"] = chunk["pageNumber"]
            if options.get("sourcePath"):
                metadata["sourcePath"] = options["sourcePath"]
            vector = vectors[index] if vectors is not None and index < len(vectors) else None
            self._store.upsert_chunk(chunk["id"], metadata, vector)
            chunks_created += 1

        result: Doc = {
            "sourceId": source_id,
            "sourceName": source_name,
            "sourceFormat": fmt,
            "chunksCreated": chunks_created,
            "chunksUpdated": 0,
            "chunksUnchanged": 0,
            "chunksDeleted": 0,
            "totalChunks": len(chunks),
        }
        if category is not None:
            result["category"] = category
        return result

    # -- ingest-document --------------------------------------------------------

    def ingest_from_file(self, file_path: str, options: Doc | None = None) -> Doc:
        options = dict(options or {})
        path = Path(file_path)
        try:
            stat = path.stat()
        except OSError as exc:
            raise IngestionNotFoundError(f"File not found: {file_path}") from exc
        if stat.st_size > MAX_FILE_SIZE:
            raise IngestionValidationError(
                f"File too large: {stat.st_size} bytes exceeds maximum of "
                f"{MAX_FILE_SIZE} bytes ({round(MAX_FILE_SIZE / 1024 / 1024)}MB)"
            )
        try:
            fmt = detect_format(file_path)
        except ParseError as exc:
            raise IngestionValidationError(str(exc)) from exc
        source_id = _source_id_from_path(file_path)
        content: str | bytes = (
            path.read_bytes() if fmt in ("pdf", "docx") else path.read_text(encoding="utf-8")
        )
        options["title"] = options.get("title") or path.name
        options["sourcePath"] = str(path.resolve())
        return self._ingest(source_id, content, fmt, options)

    # -- ingest-directory -------------------------------------------------------

    def ingest_directory(
        self, dir_path: str, pattern: str | None = None, options: Doc | None = None
    ) -> list[Doc]:
        options = dict(options or {})
        directory = Path(dir_path)
        if not directory.exists():
            raise IngestionNotFoundError(f"Directory not found: {dir_path}")
        if not directory.is_dir():
            raise IngestionValidationError(f"Not a directory: {dir_path}")

        files = _find_matching_files(directory, pattern or DEFAULT_DIR_PATTERN)
        if not files:
            return []

        existing_ids = {d["sourceId"] for d in self.list_documents(options.get("category"))}
        results: list[Doc] = []
        for file_path in files:
            if _source_id_from_path(str(file_path)) in existing_ids:
                continue  # crash-recovery skip: already ingested
            try:
                results.append(self.ingest_from_file(str(file_path), options))
            except Exception as exc:
                fmt = _safe_detect_format(str(file_path))
                results.append(
                    {
                        "sourceId": _source_id_from_path(str(file_path)),
                        "sourceName": file_path.name,
                        "sourceFormat": fmt or "unknown",
                        "chunksCreated": 0,
                        "chunksUpdated": 0,
                        "chunksUnchanged": 0,
                        "chunksDeleted": 0,
                        "totalChunks": 0,
                        **({"category": options["category"]} if options.get("category") else {}),
                        "_error": str(exc),
                    }
                )
        for result in results:
            result.pop("_error", None)
        return results

    # -- ingest-content ---------------------------------------------------------

    def ingest_content(
        self, source_id: str, content: str, fmt: str, options: Doc | None = None
    ) -> Doc:
        options = dict(options or {})
        if fmt not in _ALLOWED_CONTENT_FORMATS:
            raise IngestionValidationError(
                f"Format '{fmt}' requires binary content. Use ingestFromFile() instead."
            )
        options["title"] = options.get("title") or source_id
        return self._ingest(source_id, content, fmt, options)

    # -- ingest-url -------------------------------------------------------------

    def ingest_url(self, url: str, options: Doc | None = None, *, transport: Any = None) -> Doc:
        options = dict(options or {})
        try:
            final_url, body = fetch_url(url, transport=transport)
        except SsrfError as exc:
            message = str(exc)
            if message.startswith("Unsupported protocol") or message.startswith("Access denied"):
                raise IngestionValidationError(message) from exc
            raise IngestionError(f"Failed to fetch URL: {message}") from exc

        fmt = "html"
        if url.endswith(".md"):
            fmt = "markdown"
        source_id = _source_id_from_string(url)
        from urllib.parse import urlparse

        parsed_url = urlparse(url)
        title = options.get("title") or (parsed_url.hostname or "") + parsed_url.path
        options["title"] = title
        options["sourcePath"] = url
        return self._ingest(source_id, body, fmt, options)

    # -- list-documents ---------------------------------------------------------

    def list_documents(self, category: str | None = None) -> list[Doc]:
        chunks = self._store.query_chunks(category=category, limit=1000)
        grouped: dict[str, Doc] = {}
        for chunk in chunks:
            sid = chunk.get("sourceId")
            if not sid:
                continue
            if sid not in grouped:
                grouped[sid] = {
                    "sourceId": sid,
                    "sourceName": chunk.get("sourceName") or sid,
                    "sourceFormat": chunk.get("sourceFormat") or "unknown",
                    **({"category": chunk["category"]} if chunk.get("category") else {}),
                    "chunkCount": 0,
                    "lastIngestedAt": chunk.get("embeddedAt", ""),
                }
            entry = grouped[sid]
            entry["chunkCount"] += 1
            embedded_at = chunk.get("embeddedAt", "")
            if embedded_at > entry["lastIngestedAt"]:
                entry["lastIngestedAt"] = embedded_at
        return list(grouped.values())

    # -- delete-document --------------------------------------------------------

    def delete_document(self, source_id: str) -> Doc:
        deleted = self._store.delete_where_source(source_id)
        if deleted == 0:
            raise IngestionNotFoundError(f"No document found with sourceId: {source_id}")
        return {"sourceId": source_id, "deletedChunks": deleted}

    # -- reingest-document ------------------------------------------------------

    def reingest(self, source_id: str, options: Doc | None = None) -> Doc:
        options = dict(options or {})
        existing = self._store.query_chunks(source_id=source_id, limit=1000)
        if not existing:
            raise IngestionNotFoundError(f"No document found with sourceId: {source_id}")

        first = existing[0]
        source_name = first.get("sourceName") or source_id
        source_format = first.get("sourceFormat") or "txt"
        category = first.get("category")
        stored_source_path = first.get("sourcePath")

        existing_by_index: dict[int, Doc] = {}
        for chunk in existing:
            existing_by_index[chunk["chunkIndex"]] = {
                "contentHash": chunk["contentHash"],
                "entityId": chunk["id"],
            }

        content = _reread_source(stored_source_path, existing)
        parsed = parse_document(content, source_format, None)
        new_chunks = chunk_blocks(parsed["blocks"], options.get("chunkOptions"))
        now = iso_now()

        created = updated = unchanged = 0
        processed_indices: set[int] = set()
        to_embed: list[tuple[str, Doc]] = []

        for chunk in new_chunks:
            k = chunk["chunkIndex"]
            processed_indices.add(k)
            prior = existing_by_index.get(k)
            metadata = {
                "id": prior["entityId"] if prior else chunk["id"],
                "entityId": prior["entityId"] if prior else chunk["id"],
                "entityType": "document_chunk",
                "entryType": "document_chunk",
                "name": source_name,
                "contentHash": chunk["contentHash"],
                "embeddedAt": now,
                "sourceId": source_id,
                "sourceName": source_name,
                "sourceFormat": source_format,
                "chunkIndex": k,
                "totalChunks": len(new_chunks),
                "content": chunk["content"],
            }
            if category is not None:
                metadata["category"] = category
            if chunk.get("sectionHeading"):
                metadata["sectionHeading"] = chunk["sectionHeading"]
            if stored_source_path:
                metadata["sourcePath"] = stored_source_path

            if prior is None:
                created += 1
                to_embed.append((chunk["id"], metadata))
            elif prior["contentHash"] == chunk["contentHash"]:
                unchanged += 1  # unchanged chunks are not re-embedded/re-written
            else:
                updated += 1
                to_embed.append((prior["entityId"], metadata))

        if to_embed:
            vectors = _embed_texts([m["content"] for _, m in to_embed])
            for i, (chunk_id, metadata) in enumerate(to_embed):
                vector = vectors[i] if vectors is not None and i < len(vectors) else None
                self._store.upsert_chunk(chunk_id, metadata, vector)

        deleted = 0
        for index, prior in existing_by_index.items():
            if index not in processed_indices:
                self._store.delete_chunk(prior["entityId"])
                deleted += 1

        result: Doc = {
            "sourceId": source_id,
            "sourceName": source_name,
            "sourceFormat": source_format,
            "chunksCreated": created,
            "chunksUpdated": updated,
            "chunksUnchanged": unchanged,
            "chunksDeleted": deleted,
            "totalChunks": len(new_chunks),
        }
        if category is not None:
            result["category"] = category
        return result


# =============================================================================
# Helpers
# =============================================================================


def _reread_source(stored_source_path: str | None, existing: list[Doc]) -> str:
    if stored_source_path:
        if stored_source_path.startswith(("http://", "https://")):
            try:
                _, body = fetch_url(stored_source_path)
                return body
            except SsrfError:
                pass
        else:
            try:
                return Path(stored_source_path).read_text(encoding="utf-8")
            except OSError:
                pass
    ordered = sorted(existing, key=lambda c: c["chunkIndex"])
    return "\n\n".join(c.get("content", "") for c in ordered)


def _safe_detect_format(file_path: str) -> str | None:
    try:
        return detect_format(file_path)
    except ParseError:
        return None


def _expand_brace_pattern(pattern: str) -> list[str]:
    """Expand a single {a,b,c} brace group (the default dir pattern's shape)."""
    start = pattern.find("{")
    end = pattern.find("}", start)
    if start == -1 or end == -1:
        return [pattern]
    prefix, options, suffix = pattern[:start], pattern[start + 1 : end], pattern[end + 1 :]
    return [f"{prefix}{opt}{suffix}" for opt in options.split(",")]


def _find_matching_files(directory: Path, pattern: str) -> list[Path]:
    recursive = pattern.startswith("**/") or "/**/" in pattern
    globs = _expand_brace_pattern(pattern)
    matches: set[Path] = set()
    walker = directory.rglob("*") if recursive else directory.glob("*")
    for entry in walker:
        if not entry.is_file() or entry.is_symlink():
            continue
        name = entry.name
        for glob in globs:
            basename_glob = glob.rsplit("/", 1)[-1]
            if fnmatch.fnmatch(name, basename_glob):
                matches.add(entry)
                break
    return sorted(matches)
