"""Document chunker.

Three phases: strategy grouping (structural respects headings and keeps
code/list blocks atomic; paragraph splits on size only; fixed repacks
sentences), sentence-boundary splitting of oversized groups (atomic groups
survive intact even over maxSize), then overlap + undersized-merge. Chunk
contentHash is sha256 of the trimmed content INCLUDING the overlap prefix;
ids are fresh UUIDs; maxSize defaults to ceil(targetSize * 1.5).
"""

from __future__ import annotations

import hashlib
import math
import re
import uuid
from typing import Any

Doc = dict[str, Any]

DEFAULT_TARGET_SIZE = 4000
DEFAULT_MIN_SIZE = 200
DEFAULT_OVERLAP_SENTENCES = 2

_SENTENCE_RE = re.compile(r"[^.!?]*[.!?]+\s*")


def compute_chunk_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def split_sentences(text: str) -> list[str]:
    matches = _SENTENCE_RE.findall(text)
    if not matches:
        trimmed = text.strip()
        return [trimmed] if trimmed else []
    result = [s for s in (m.strip() for m in matches) if s]
    matched_length = sum(len(m) for m in matches)
    if matched_length < len(text):
        remainder = text[matched_length:].strip()
        if remainder:
            result.append(remainder)
    return result


def _new_group(
    section_heading: str | None = None,
    page_number: int | None = None,
    heading_boundary: bool = False,
) -> Doc:
    return {
        "blocks": [],
        "text": "",
        "sectionHeading": section_heading,
        "pageNumber": page_number,
        "headingBoundary": heading_boundary,
    }


def _structural_groups(blocks: list[Doc], target_size: int) -> list[Doc]:
    groups: list[Doc] = []
    current = _new_group()
    current_section_heading: str | None = None

    for block in blocks:
        if block["type"] == "heading" and len(current["text"]) > 0:
            groups.append(current)
            current_section_heading = block["text"]
            current = _new_group(current_section_heading, block.get("pageNumber"), True)
        elif block["type"] == "heading":
            current_section_heading = block["text"]
            if current["sectionHeading"] is None:
                current["sectionHeading"] = current_section_heading
                current["headingBoundary"] = True

        if (
            len(current["text"]) > 0
            and len(current["text"]) + len(block["text"]) > target_size
            and block["type"] != "code"
            and block["type"] != "list"
        ):
            groups.append(current)
            current = _new_group(current_section_heading, block.get("pageNumber"))

        current["blocks"].append(block)
        current["text"] += ("\n\n" if current["text"] else "") + block["text"]
        if current["sectionHeading"] is None:
            current["sectionHeading"] = block.get("sectionHeading") or current_section_heading
        if current["pageNumber"] is None:
            current["pageNumber"] = block.get("pageNumber")

    if len(current["text"]) > 0:
        groups.append(current)
    return groups


def _paragraph_groups(blocks: list[Doc], target_size: int) -> list[Doc]:
    groups: list[Doc] = []
    current = _new_group()
    current_section_heading: str | None = None

    for block in blocks:
        if block["type"] == "heading":
            current_section_heading = block["text"]
        block_text = block["text"]
        if len(current["text"]) > 0 and len(current["text"]) + len(block_text) > target_size:
            groups.append(current)
            current = _new_group(current_section_heading, block.get("pageNumber"))
        current["blocks"].append(block)
        current["text"] += ("\n\n" if current["text"] else "") + block_text
        if current["sectionHeading"] is None:
            current["sectionHeading"] = block.get("sectionHeading") or current_section_heading
        if current["pageNumber"] is None:
            current["pageNumber"] = block.get("pageNumber")

    if len(current["text"]) > 0:
        groups.append(current)
    return groups


def _fixed_size_groups(blocks: list[Doc], target_size: int) -> list[Doc]:
    all_text = ""
    current_section_heading: str | None = None
    first_page_number = blocks[0].get("pageNumber") if blocks else None
    for block in blocks:
        if block["type"] == "heading":
            current_section_heading = block["text"]
        all_text += ("\n\n" if all_text else "") + block["text"]

    sentences = split_sentences(all_text)
    groups: list[Doc] = []
    current_text = ""
    for sentence in sentences:
        if len(current_text) + len(sentence) > target_size and len(current_text) > 0:
            groups.append(
                {
                    "blocks": blocks,
                    "text": current_text.strip(),
                    "sectionHeading": current_section_heading,
                    "pageNumber": first_page_number,
                    "headingBoundary": False,
                }
            )
            current_text = ""
        current_text += (" " if current_text else "") + sentence
    if current_text.strip():
        groups.append(
            {
                "blocks": blocks,
                "text": current_text.strip(),
                "sectionHeading": current_section_heading,
                "pageNumber": first_page_number,
                "headingBoundary": False,
            }
        )
    return groups


def _split_oversized_groups(groups: list[Doc], max_size: int) -> list[Doc]:
    result: list[Doc] = []
    for group in groups:
        if len(group["text"]) <= max_size:
            result.append(group)
            continue
        if any(b["type"] in ("code", "list") for b in group["blocks"]):
            result.append(group)  # atomic blocks beat the size limit
            continue

        sentences = split_sentences(group["text"])
        current_text = ""
        is_first = True
        for sentence in sentences:
            if len(current_text) + len(sentence) > max_size and len(current_text) > 0:
                result.append(
                    {
                        "blocks": group["blocks"],
                        "text": current_text.strip(),
                        "sectionHeading": group["sectionHeading"],
                        "pageNumber": group["pageNumber"],
                        "headingBoundary": group["headingBoundary"] if is_first else False,
                    }
                )
                current_text = ""
                is_first = False
            current_text += (" " if current_text else "") + sentence
        if current_text.strip():
            result.append(
                {
                    "blocks": group["blocks"],
                    "text": current_text.strip(),
                    "sectionHeading": group["sectionHeading"],
                    "pageNumber": group["pageNumber"],
                    "headingBoundary": group["headingBoundary"] if is_first else False,
                }
            )
    return result


def _build_chunks_with_overlap(
    groups: list[Doc], overlap_sentences: int, min_size: int
) -> list[Doc]:
    chunks: list[Doc] = []
    for i, group in enumerate(groups):
        content = group["text"]
        if i > 0 and overlap_sentences > 0:
            prev_sentences = split_sentences(groups[i - 1]["text"])
            overlap = " ".join(prev_sentences[-overlap_sentences:])
            if overlap:
                content = overlap + "\n\n" + content

        if (
            len(content.strip()) < min_size
            and len(groups) > 1
            and not group["headingBoundary"]
            and chunks
        ):
            prev = chunks[-1]
            prev["content"] += "\n\n" + content.strip()
            prev["contentHash"] = compute_chunk_hash(prev["content"])
            continue

        chunks.append(
            {
                "id": str(uuid.uuid4()),
                "content": content.strip(),
                "chunkIndex": len(chunks),
                "pageNumber": group["pageNumber"],
                "sectionHeading": group["sectionHeading"],
                "contentHash": compute_chunk_hash(content.strip()),
            }
        )

    for i, chunk in enumerate(chunks):
        chunk["chunkIndex"] = i
    return chunks


def chunk_blocks(blocks: list[Doc], options: Doc | None = None) -> list[Doc]:
    options = options or {}
    target_size = options.get("targetSize") or DEFAULT_TARGET_SIZE
    max_size = options.get("maxSize") or math.ceil(target_size * 1.5)
    min_size = int(options["minSize"]) if options.get("minSize") is not None else DEFAULT_MIN_SIZE
    overlap_sentences = (
        int(options["overlapSentences"])
        if options.get("overlapSentences") is not None
        else DEFAULT_OVERLAP_SENTENCES
    )
    strategy = options.get("strategy") or "structural"

    if not blocks:
        return []

    if strategy == "fixed":
        groups = _fixed_size_groups(blocks, target_size)
    elif strategy == "paragraph":
        groups = _paragraph_groups(blocks, target_size)
    else:
        groups = _structural_groups(blocks, target_size)

    split = _split_oversized_groups(groups, max_size)
    return _build_chunks_with_overlap(split, overlap_sentences, min_size)
