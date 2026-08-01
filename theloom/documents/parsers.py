"""Format parsers → DocumentBlock lists.

Text formats (md/txt/html/json) are handled here; binary formats (pdf/docx)
route through docling. Block shape is {text, type, headingLevel?,
sectionHeading?, pageNumber?}.

Markdown flattens inline formatting to text: soft line breaks stay as '\\n'
inside a paragraph, code fences are re-wrapped with ``` markers, list items
join with '\\n'. Frontmatter and other node types are dropped.
"""

from __future__ import annotations

import json as json_module
import re
from typing import Any

Doc = dict[str, Any]

SUPPORTED_EXTENSIONS = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".md": "markdown",
    ".markdown": "markdown",
    ".html": "html",
    ".htm": "html",
    ".txt": "txt",
    ".json": "json",
}


class ParseError(Exception):
    """Unsupported format / mismatched content type."""


def detect_format(file_path: str) -> str:
    from pathlib import Path

    ext = Path(file_path).suffix.lower()
    fmt = SUPPORTED_EXTENSIONS.get(ext)
    if fmt is None:
        raise ParseError(
            f"Unsupported file extension: {ext}. Supported: "
            ".pdf, .docx, .md, .markdown, .html, .htm, .txt, .json"
        )
    return fmt


# =============================================================================
# TXT
# =============================================================================


def parse_txt(content: str) -> Doc:
    blocks = [
        {"text": segment.strip(), "type": "paragraph"}
        for segment in re.split(r"\n\n+", content)
        if segment.strip()
    ]
    return {"blocks": blocks, "title": "", "format": "txt"}


# =============================================================================
# Markdown
# =============================================================================


def _inline_text(token: Any) -> str:
    """Flatten an inline token's children to text (remark extractMdastText):
    text/code_inline values concatenated, soft/hard breaks -> newline."""
    if token.children is None:
        return str(token.content)
    parts: list[str] = []
    for child in token.children:
        if child.type in ("text", "code_inline"):
            parts.append(child.content)
        elif child.type in ("softbreak", "hardbreak"):
            parts.append("\n")
    return "".join(parts)


def parse_markdown(content: str, title: str | None = None) -> Doc:
    from markdown_it import MarkdownIt

    md = MarkdownIt("commonmark")
    tokens = md.parse(content)
    blocks: list[Doc] = []
    section_heading: str | None = None
    extracted_title = ""

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.type == "heading_open":
            level = int(token.tag[1])
            text = _inline_text(tokens[i + 1]) if i + 1 < len(tokens) else ""
            section_heading = text
            if level == 1 and not extracted_title:
                extracted_title = text
            blocks.append(
                {
                    "text": text,
                    "type": "heading",
                    "headingLevel": level,
                    "sectionHeading": text,
                }
            )
            i += 3
            continue
        if token.type == "paragraph_open":
            text = _inline_text(tokens[i + 1]) if i + 1 < len(tokens) else ""
            block: Doc = {"text": text, "type": "paragraph"}
            if section_heading is not None:
                block["sectionHeading"] = section_heading
            blocks.append(block)
            i += 3
            continue
        if token.type == "fence" or token.type == "code_block":
            lang = (token.info or "").strip()
            fence = (
                f"```{lang}\n{token.content.rstrip(chr(10))}\n```"
                if lang
                else (f"```\n{token.content.rstrip(chr(10))}\n```")
            )
            block = {"text": fence, "type": "code"}
            if section_heading is not None:
                block["sectionHeading"] = section_heading
            blocks.append(block)
            i += 1
            continue
        if token.type == "bullet_list_open" or token.type == "ordered_list_open":
            close = (
                "bullet_list_close" if token.type == "bullet_list_open" else "ordered_list_close"
            )
            items: list[str] = []
            j = i + 1
            depth = 1
            while j < len(tokens) and depth > 0:
                if tokens[j].type == token.type:
                    depth += 1
                elif tokens[j].type == close:
                    depth -= 1
                elif tokens[j].type == "inline" and depth == 1:
                    items.append(_inline_text(tokens[j]))
                j += 1
            block = {"text": "\n".join(items), "type": "list"}
            if section_heading is not None:
                block["sectionHeading"] = section_heading
            blocks.append(block)
            i = j
            continue
        if token.type == "blockquote_open":
            j = i + 1
            depth = 1
            parts: list[str] = []
            while j < len(tokens) and depth > 0:
                if tokens[j].type == "blockquote_open":
                    depth += 1
                elif tokens[j].type == "blockquote_close":
                    depth -= 1
                elif tokens[j].type == "inline" and depth == 1:
                    parts.append(_inline_text(tokens[j]))
                j += 1
            block = {"text": "\n".join(parts), "type": "blockquote"}
            if section_heading is not None:
                block["sectionHeading"] = section_heading
            blocks.append(block)
            i = j
            continue
        i += 1

    final_title = title if title else extracted_title
    return {"blocks": blocks, "title": final_title, "format": "markdown"}


# =============================================================================
# HTML
# =============================================================================

_BLOCK_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "pre", "ul", "ol", "blockquote"}


def _html_to_blocks(root: Any) -> list[Doc]:
    blocks: list[Doc] = []
    section_heading: str | None = None

    def walk(node: Any) -> None:
        nonlocal section_heading
        for child in node.children:
            if child.name is None:
                continue
            tag = child.name.lower()
            if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                text = child.get_text().strip()
                section_heading = text
                blocks.append(
                    {
                        "text": text,
                        "type": "heading",
                        "headingLevel": int(tag[1]),
                        "sectionHeading": text,
                    }
                )
            elif tag == "p":
                block: Doc = {"text": child.get_text().strip(), "type": "paragraph"}
                if section_heading is not None:
                    block["sectionHeading"] = section_heading
                blocks.append(block)
            elif tag == "pre":
                block = {"text": child.get_text(), "type": "code"}
                if section_heading is not None:
                    block["sectionHeading"] = section_heading
                blocks.append(block)
            elif tag in ("ul", "ol"):
                items = [li.get_text().strip() for li in child.find_all("li", recursive=False)]
                block = {"text": "\n".join(items), "type": "list"}
                if section_heading is not None:
                    block["sectionHeading"] = section_heading
                blocks.append(block)
            elif tag == "blockquote":
                block = {"text": child.get_text().strip(), "type": "blockquote"}
                if section_heading is not None:
                    block["sectionHeading"] = section_heading
                blocks.append(block)
            elif tag in ("div", "section", "article", "main", "body"):
                walk(child)

    walk(root)
    return blocks


def parse_html(content: str, title: str | None = None) -> Doc:
    if not content.strip():
        return {"blocks": [], "title": title or "", "format": "html"}
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(content, "html.parser")
    extracted_title = ""
    title_tag = soup.find("title")
    if title_tag is not None:
        extracted_title = title_tag.get_text().strip()
    body = soup.find("body") or soup
    blocks = _html_to_blocks(body)
    return {"blocks": blocks, "title": title or extracted_title, "format": "html"}


# =============================================================================
# JSON
# =============================================================================

_SKIP_KEYS = {"id", "created", "updated", "version", "contentHash", "embeddedAt", "embeddingStatus"}
_CAMEL_RE = re.compile(r"([A-Z])")


def _humanize_key(key: str) -> str:
    human = _CAMEL_RE.sub(r" \1", key)
    human = re.sub(r"[_-]", " ", human).strip()
    return human[:1].upper() + human[1:] if human else human


def _generic_json_to_blocks(obj: Any, json_path: list[str], depth: int) -> list[Doc]:
    blocks: list[Doc] = []
    if depth > 6:
        return blocks

    heading_ctx = " > ".join(json_path) if json_path else None
    if isinstance(obj, str):
        if len(obj) > 20:
            block: Doc = {"text": obj, "type": "paragraph"}
            if heading_ctx is not None:
                block["sectionHeading"] = heading_ctx
            blocks.append(block)
    elif isinstance(obj, list):
        string_items = [item for item in obj if isinstance(item, str) and len(item) > 10]
        if string_items:
            block = {"text": "\n".join(f"- {s}" for s in string_items), "type": "list"}
            if heading_ctx is not None:
                block["sectionHeading"] = heading_ctx
            blocks.append(block)
        # The index counts only the object/list items that survive the filter.
        object_items = [item for item in obj if isinstance(item, dict | list)]
        for i, item in enumerate(object_items):
            blocks.extend(_generic_json_to_blocks(item, [*json_path, f"[{i}]"], depth + 1))
    elif isinstance(obj, dict):
        for key, value in obj.items():
            if key in _SKIP_KEYS:
                continue
            heading_level = min(depth + 1, 6)
            display_key = _humanize_key(key)
            if isinstance(value, dict | list):
                blocks.append(
                    {
                        "text": display_key,
                        "type": "heading",
                        "headingLevel": heading_level,
                        "sectionHeading": display_key,
                    }
                )
                blocks.extend(_generic_json_to_blocks(value, [*json_path, key], depth + 1))
            elif isinstance(value, str) and len(value) > 10:
                block = {"text": f"{display_key}: {value}", "type": "paragraph"}
                if heading_ctx is not None:
                    block["sectionHeading"] = heading_ctx
                blocks.append(block)
    return blocks


def parse_json(content: str, title: str | None = None) -> Doc:
    try:
        data = json_module.loads(content)
    except (json_module.JSONDecodeError, ValueError):
        return parse_txt(content)  # not valid JSON -> plain text
    if not isinstance(data, dict):
        # Non-object top level (arrays/scalars) hits the generic walk.
        blocks = _generic_json_to_blocks(data, [], 0)
        return {"blocks": blocks, "title": title or "JSON Document", "format": "json"}

    # NOTE: documents with a well-known schema could get bespoke converters;
    # that is deferred until a fixture needs one. The generic path below covers
    # every JSON document, which is what ingest-* exercises here.
    extracted_title = (
        title or data.get("title") or data.get("name") or data.get("document") or "JSON Document"
    )
    blocks = _generic_json_to_blocks(data, [], 0)
    return {"blocks": blocks, "title": extracted_title, "format": "json"}


# =============================================================================
# Binary formats via docling
# =============================================================================


def parse_binary(content: bytes, fmt: str, title: str | None = None) -> Doc:
    """PDF/DOCX via docling. Emits paragraph blocks from the exported markdown
    (docling's exported structure is not byte-stable, so binary chunk output
    can shift across docling versions)."""
    import tempfile
    from pathlib import Path

    from docling.document_converter import DocumentConverter

    suffix = ".pdf" if fmt == "pdf" else ".docx"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        handle.write(content)
        temp_path = handle.name
    try:
        converter = DocumentConverter()
        result = converter.convert(temp_path)
        markdown = result.document.export_to_markdown()
    finally:
        Path(temp_path).unlink(missing_ok=True)
    parsed = parse_markdown(markdown, title)
    parsed["format"] = fmt
    return parsed


# =============================================================================
# Dispatcher
# =============================================================================


def parse_document(content: str | bytes, fmt: str, title: str | None = None) -> Doc:
    if fmt == "pdf":
        if not isinstance(content, bytes):
            raise ParseError("PDF content must be a Buffer")
        return parse_binary(content, "pdf", title)
    if fmt == "docx":
        if not isinstance(content, bytes):
            raise ParseError("DOCX content must be a Buffer")
        return parse_binary(content, "docx", title)

    text = content.decode("utf-8") if isinstance(content, bytes) else content
    if fmt == "markdown":
        result = parse_markdown(text, title)
    elif fmt == "html":
        result = parse_html(text, title)
    elif fmt == "txt":
        result = parse_txt(text)
    elif fmt == "json":
        result = parse_json(text, title)
    else:
        raise ParseError(
            f"Unsupported format: {fmt}. Supported: pdf, docx, markdown, html, txt, json"
        )
    if title:
        result["title"] = title
    return result
