"""Tree-sitter codebase extraction.

Uses the native py-tree-sitter bindings. Symbol/relation MAPPING: file ->
system, class/interface/struct/trait/type_alias/enum -> concept,
function/method -> procedure, variable/constant -> variable; entity name
``qualified (fileBaseName)``; symbol part_of file (+ enclosing), imports ->
requires (resolved to the target file entity, or a ``pkg:`` node for a
third-party package), inheritance -> instance_of, calls -> calls (intra-file
directly, cross-file via ``theloom.extraction.resolution``).
Grammar-version differences can shift a symbol across tree-sitter releases; the
entity/relation semantics are the contract.

A call edge is anchored at its **call site**: the evidence reads
``<caller> calls <callee> at <file>:<line>`` where the line is where the call is
written, not where the callee is defined — a reader following an edge is
reading the caller. The format is fixed so it can be parsed. ``related_to`` is
never emitted here; it belongs to the semantic enrichment layer.

A symbol entity carries more than its coordinates: the **signature** and the
**docstring** are already in the parse tree, and a rationale comment
(``NOTE:``/``WHY:``/``HACK:``/``IMPORTANT:``/``TODO:``/``FIXME:``) is the only
record of why a line exists. Both are attached to the symbol they describe
rather than becoming nodes of their own — a separate docstring node inflates
the graph without making anything reachable that the symbol did not already
reach, and leaves the symbol with nothing to embed. Rationale attaches to the
innermost enclosing symbol, or to the file entity at module level; ADR/RFC
identifiers mentioned in a comment are recorded separately as ``cites:``.

Non-code text files (stylesheets, config, docs — see ``TEXT_EXTENSIONS``) get a
file entity too, so an invariant anchored in e.g. a design-token stylesheet has
something to point at. Documentation additionally gets edges: a Markdown file
is scanned for the paths and symbols it names, and each unambiguous mention
becomes a ``references`` edge into the code (see
``theloom.extraction.doclinks``), so docs are no longer an island the graph
cannot reach.

What counts as part of the codebase is decided by **git**, not by the
filesystem. Inside a work tree, an ignored path never becomes an entity (it is
private by the author's explicit instruction, and a machine-local notes file or
a generated data dump has no business in a shared graph); a non-code file must
additionally be tracked. Outside a work tree there is nothing to consult, so
the whole directory is walked.

Files are traversed in SORTED order so output is deterministic (an unsorted
directory walk would be machine-dependent).
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable
from typing import Any

from theloom.extraction import doclinks, encoding, resolution

Doc = dict[str, Any]

EXTENSION_MAP = {
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".py": "python",
    ".go": "go",
    ".rs": "rust",
}

# Non-code files that still carry meaning a graph should be able to point at.
TEXT_EXTENSIONS = {"css", "json", "yaml", "yml", "toml", "md", "lock", "cfg", "ini"}

# A non-code file past this size is data, not documentation; parsing it buys
# nothing and reading it is the only cost extraction can't amortise.
MAX_TEXT_FILE_BYTES = 1024 * 1024

DOCSTRING_MAX_CHARS = 300
RATIONALE_MAX_CHARS = 200

# Node types that hold a string literal in the supported grammars (Python and
# TypeScript/JavaScript both name it ``string``; ``string_literal`` is there so
# a grammar that names it that way needs no second pass).
_STRING_NODE_TYPES = frozenset({"string", "string_literal"})

# Below this a value is a word, not a term worth reserving.
MIN_VOCABULARY_CHARS = 3

SKIP_DIRS = {
    "node_modules",
    "dist",
    ".git",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    "target",
    "build",
    "vendor",
    ".next",
    ".cache",
}

_CONCEPT_KINDS = {"class", "interface", "struct", "trait", "type_alias", "enum"}
_PROCEDURE_KINDS = {"function", "method"}
_VARIABLE_KINDS = {"variable", "constant"}


def detect_language(relative_path: str) -> str | None:
    _, ext = os.path.splitext(relative_path.lower())
    return EXTENSION_MAP.get(ext)


def detect_text_kind(relative_path: str) -> str | None:
    """The recognised non-code text kind of a path (``css``, ``toml``, ...).

    Deliberately separate from ``detect_language``: these files are never
    parsed, they only become root file entities.
    """
    _, ext = os.path.splitext(relative_path.lower())
    kind = ext[1:] if ext else ""
    return kind if kind in TEXT_EXTENSIONS else None


def kind_to_entity_type(kind: str) -> str:
    if kind in _CONCEPT_KINDS:
        return "concept"
    if kind in _PROCEDURE_KINDS:
        return "procedure"
    if kind in _VARIABLE_KINDS:
        return "variable"
    return "concept"


def _build_entity_name(display_name: str, file_path: str, enclosing_name: str | None = None) -> str:
    base = os.path.splitext(os.path.basename(file_path))[0]
    qualified = f"{enclosing_name}.{display_name}" if enclosing_name else display_name
    return f"{qualified} ({base})"


# =============================================================================
# Tree-sitter parsing
# =============================================================================


def _language(lang: str) -> Any:
    if lang == "python":
        import tree_sitter_python

        return tree_sitter_python.language()
    if lang == "typescript":
        import tree_sitter_typescript

        return tree_sitter_typescript.language_typescript()
    if lang == "javascript":
        import tree_sitter_javascript

        return tree_sitter_javascript.language()
    if lang == "go":
        import tree_sitter_go

        return tree_sitter_go.language()
    if lang == "rust":
        import tree_sitter_rust

        return tree_sitter_rust.language()
    raise ValueError(f"Unsupported language: {lang}")


def _get_parser(lang: str) -> Any:
    from tree_sitter import Language, Parser

    return Parser(Language(_language(lang)))


def _text(node: Any, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _field(node: Any, name: str) -> Any:
    return node.child_by_field_name(name)


def _named(node: Any) -> list[Any]:
    return [c for c in node.children if c.is_named]


# =============================================================================
# Signatures, docstrings and rationale comments
# =============================================================================

_STRING_PREFIX_RE = re.compile(r"^[rubfRUBF]{0,3}('''|\"\"\"|'|\")")
# The leading identifier of a string literal, when the literal *is* that
# identifier or is keyed by it (``"usage_status: "``, ``"basis=..."``). Those
# are the terms a project writes as values rather than as code.
_VOCABULARY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(?:[:=]|$)")
_COMMENT_MARKER_RE = re.compile(r"^\s*(?:#+|//+|/\*+|\*+)\s?")
_COMMENT_TAIL_RE = re.compile(r"\s*\*+/\s*$")
_RATIONALE_RE = re.compile(r"^(NOTE|HACK|WHY|IMPORTANT|TODO|FIXME)\s*:\s*(.*)$")
_CITATION_RE = re.compile(r"\b(?:ADR|RFC)-\d+\b")


def _collapse(text: str) -> str:
    """One line, single-spaced — an observation is a line, not a paragraph."""
    return " ".join(text.split())


def _one_line(text: str, limit: int) -> str | None:
    collapsed = _collapse(text)[:limit]
    return collapsed or None


def _strip_string_literal(raw: str) -> str | None:
    """The text inside a Python string literal, quotes and prefix removed."""
    match = _STRING_PREFIX_RE.match(raw)
    if match is None:
        return None
    quote = match.group(1)
    body = raw[match.end() :]
    if body.endswith(quote):
        body = body[: -len(quote)]
    return body


def _clean_python_docstring(raw: str) -> str | None:
    body = _strip_string_literal(raw)
    if body is None:
        return None
    return _one_line(body, DOCSTRING_MAX_CHARS)


def _clean_block_comment(raw: str) -> str | None:
    body = raw
    if body.startswith("/*"):
        body = body[2:]
    if body.endswith("*/"):
        body = body[:-2]
    lines = [line.strip().lstrip("*").strip() for line in body.splitlines()]
    return _one_line(" ".join(lines), DOCSTRING_MAX_CHARS)


def _python_docstring(container: Any, source: bytes) -> str | None:
    """The leading string literal of a module, class body or function body."""
    children = _named(container)
    if not children:
        return None
    first = children[0]
    if first.type != "expression_statement":
        return None
    inner = _named(first)
    if not inner:
        return None
    literal = inner[0]
    if literal.type == "string":
        return _clean_python_docstring(_text(literal, source))
    if literal.type == "concatenated_string":
        # ``"""a""" """b"""`` is one docstring to Python but two literals to the
        # parser; joining the parts is what keeps it from being dropped.
        parts = [
            stripped
            for part in _named(literal)
            if part.type == "string" and (stripped := _strip_string_literal(_text(part, source)))
        ]
        return _one_line("".join(parts), DOCSTRING_MAX_CHARS) if parts else None
    return None


def _python_signature(node: Any, name: str, source: bytes) -> str | None:
    params = _field(node, "parameters")
    if params is None:
        return None
    signature = f"{name}{_collapse(_text(params, source))}"
    returns = _field(node, "return_type")
    if returns is not None:
        signature += f" -> {_collapse(_text(returns, source))}"
    return signature


def _ts_signature(node: Any, name: str, source: bytes) -> str | None:
    params = _field(node, "parameters")
    if params is None:
        return None
    signature = f"{name}{_collapse(_text(params, source))}"
    returns = _field(node, "return_type")
    if returns is not None:
        annotation = _collapse(_text(returns, source))
        signature += annotation if annotation.startswith(":") else f": {annotation}"
    return signature


def _leading_block_comment(node: Any, source: bytes) -> str | None:
    """The ``/** ... */`` immediately above a declaration.

    An exported declaration is wrapped in ``export_statement``, so the comment
    is the wrapper's sibling, not the declaration's.
    """
    current = node
    while current.parent is not None and current.parent.type == "export_statement":
        current = current.parent
    previous = current.prev_named_sibling
    if previous is None or not previous.type.endswith("comment"):
        return None
    raw = _text(previous, source)
    return _clean_block_comment(raw) if raw.startswith("/*") else None


def _leading_module_comment(root: Any, source: bytes) -> str | None:
    """A file header comment — but only when it documents the file.

    A block comment sitting directly above the first declaration documents
    *that declaration*; taking it as the module's too would duplicate it.
    """
    children = root.children
    if not children:
        return None
    first = children[0]
    raw = _text(first, source)
    if not first.type.endswith("comment") or not raw.startswith("/*"):
        return None
    if len(children) > 1 and children[1].start_point[0] <= first.end_point[0] + 1:
        return None
    return _clean_block_comment(raw)


def _string_literal_vocabulary(node: Any, source: bytes, found: set[str]) -> None:
    """The identifier-shaped terms this file writes as string *values*.

    An enum value, a status token, a keyed prefix constant — the project's own
    domain language. The doc linker needs it to tell a term from a symbol:
    ``single_source`` is a ``ConfidenceBasis`` value long before it is the
    semiring function of the same name, and a doc quoting the term is not
    referencing the function.
    """
    if node.type in _STRING_NODE_TYPES:
        body = _strip_string_literal(_text(node, source))
        match = _VOCABULARY_RE.match(body) if body else None
        if match is not None and len(match.group(1)) >= MIN_VOCABULARY_CHARS:
            found.add(match.group(1))
        return
    for child in node.children:
        _string_literal_vocabulary(child, source, found)


def _comment_notes(node: Any, source: bytes, notes: list[Doc]) -> None:
    """Rationale tags and ADR/RFC citations, each anchored at its own line."""
    if node.type.endswith("comment"):
        raw = _text(node, source)
        for offset, line in enumerate(raw.splitlines()):
            line_index = node.start_point[0] + offset
            stripped = _COMMENT_TAIL_RE.sub("", _COMMENT_MARKER_RE.sub("", line)).strip()
            if not stripped:
                continue
            match = _RATIONALE_RE.match(stripped)
            if match is not None:
                body = _one_line(match.group(2), RATIONALE_MAX_CHARS)
                if body is not None:
                    notes.append(
                        {
                            "line": line_index,
                            "observation": (
                                f"rationale: [{match.group(1)}] {body} (line {line_index + 1})"
                            ),
                        }
                    )
            seen: set[str] = set()
            for ref in _CITATION_RE.findall(stripped):
                if ref in seen:
                    continue
                seen.add(ref)
                notes.append(
                    {"line": line_index, "observation": f"cites: {ref} (line {line_index + 1})"}
                )
        return
    for child in node.children:
        _comment_notes(child, source, notes)


# =============================================================================
# Per-language extraction
# =============================================================================


def _extract_calls(node: Any, caller: str, source: bytes, calls: list[Doc]) -> None:
    if node.type in ("call", "call_expression"):
        func = _field(node, "function")
        if func is not None and func.type == "identifier":
            # ``line`` is the 0-based call site, matching the symbol convention;
            # it becomes the anchor in the edge's evidence.
            calls.append(
                {
                    "caller": caller,
                    "callee": _text(func, source),
                    "line": node.start_point[0],
                }
            )
    for child in _named(node):
        _extract_calls(child, caller, source, calls)


def _python_imported_names(node: Any, module_node: Any, source: bytes) -> list[str]:
    """The bound names of a ``from X import a, b as c`` — ``a`` and ``c``.

    The alias is what the calling code writes, so the alias is what a call
    resolver must match on.
    """
    names: list[str] = []
    for child in _named(node):
        if child.id == module_node.id:
            continue
        if child.type == "aliased_import":
            alias = _field(child, "alias")
            if alias is not None:
                names.append(_text(alias, source))
        elif child.type in ("dotted_name", "identifier"):
            names.append(_text(child, source).split(".")[0])
    return names


def _extract_python(root: Any, source: bytes) -> Doc:
    symbols: list[Doc] = []
    imports: list[Doc] = []
    inheritances: list[Doc] = []
    calls: list[Doc] = []

    def walk(node: Any, enclosing_class: str | None, enclosing_func: str | None) -> None:
        if node.type == "import_statement":
            for child in _named(node):
                if child.type == "aliased_import":
                    # ``import numpy as np``: the module is the dotted name, and
                    # ``np`` is the name calling code writes.
                    module_node = _field(child, "name")
                    alias = _field(child, "alias")
                    if module_node is not None:
                        imports.append(
                            {
                                "module": _text(module_node, source),
                                "names": [_text(alias, source)] if alias is not None else [],
                            }
                        )
                elif child.type == "dotted_name":
                    imports.append({"module": _text(child, source), "names": []})
        elif node.type == "import_from_statement":
            module = _field(node, "module_name")
            if module is not None:
                imports.append(
                    {
                        "module": _text(module, source),
                        "names": _python_imported_names(node, module, source),
                    }
                )
        elif node.type == "class_definition":
            name = _field(node, "name")
            if name is not None:
                class_name = _text(name, source)
                class_body = _field(node, "body")
                class_doc = None
                if class_body is not None:
                    class_doc = _python_docstring(class_body, source)
                symbols.append(
                    {
                        "name": class_name,
                        "kind": "class",
                        "startLine": node.start_point[0],
                        "endLine": node.end_point[0],
                        "enclosingName": enclosing_class,
                        "signature": None,
                        "docstring": class_doc,
                    }
                )
                superclasses = _field(node, "superclasses")
                if superclasses is not None:
                    for child in _named(superclasses):
                        if child.type == "identifier":
                            inheritances.append(
                                {"child": class_name, "parent": _text(child, source)}
                            )
                body = _field(node, "body")
                if body is not None:
                    for child in _named(body):
                        walk(child, class_name, None)
                return
        elif node.type == "function_definition":
            name = _field(node, "name")
            if name is not None:
                func_name = _text(name, source)
                body = _field(node, "body")
                symbols.append(
                    {
                        "name": func_name,
                        "kind": "method" if enclosing_class else "function",
                        "startLine": node.start_point[0],
                        "endLine": node.end_point[0],
                        "enclosingName": enclosing_class,
                        "signature": _python_signature(node, func_name, source),
                        "docstring": (
                            _python_docstring(body, source) if body is not None else None
                        ),
                    }
                )
                if body is not None:
                    caller_key = f"{enclosing_class}.{func_name}" if enclosing_class else func_name
                    _extract_calls(body, caller_key, source, calls)
                return
        elif node.type == "expression_statement":
            children = _named(node)
            child = children[0] if children else None
            if child is not None and child.type == "assignment":
                left = _field(child, "left")
                if (
                    left is not None
                    and left.type == "identifier"
                    and not enclosing_class
                    and not enclosing_func
                ):
                    text = _text(left, source)
                    if text == text.upper() and len(text) > 1:
                        symbols.append(
                            {
                                "name": text,
                                "kind": "constant",
                                "startLine": node.start_point[0],
                                "endLine": node.end_point[0],
                                "enclosingName": None,
                            }
                        )
        for child in _named(node):
            walk(child, enclosing_class, enclosing_func)

    walk(root, None, None)
    return {
        "symbols": symbols,
        "imports": imports,
        "inheritances": inheritances,
        "calls": calls,
        "moduleDocstring": _python_docstring(root, source),
    }


def _find_identifier(node: Any, source: bytes) -> str | None:
    if node.type in ("identifier", "type_identifier"):
        return _text(node, source)
    for child in _named(node):
        found = _find_identifier(child, source)
        if found:
            return found
    return None


def _ts_heritage(class_node: Any, class_name: str, source: bytes, inheritances: list[Doc]) -> None:
    for child in _named(class_node):
        if child.type == "class_heritage":
            for clause in _named(child):
                if clause.type in ("extends_clause", "implements_clause"):
                    for type_node in _named(clause):
                        ident = _find_identifier(type_node, source)
                        if ident:
                            inheritances.append({"child": class_name, "parent": ident})


def _js_imported_names(node: Any, source: bytes) -> list[str]:
    """Bound names of an ES import — ``{a, b as c}`` gives ``a`` and ``c``,
    and a default or namespace import gives its local name."""
    names: list[str] = []

    def walk(n: Any) -> None:
        if n.type == "import_specifier":
            alias = _field(n, "alias")
            name = _field(n, "name")
            chosen = alias if alias is not None else name
            if chosen is not None:
                names.append(_text(chosen, source))
            return
        if n.type in ("namespace_import", "identifier") and n.parent is not None:  # noqa: SIM102
            if n.parent.type in ("import_clause", "namespace_import"):
                text = _text(n, source).replace("* as ", "").strip()
                if text and text not in ("type",):
                    names.append(text)
                return
        for child in _named(n):
            walk(child)

    walk(node)
    return names


def _extract_typescript(root: Any, source: bytes) -> Doc:
    symbols: list[Doc] = []
    imports: list[Doc] = []
    inheritances: list[Doc] = []
    calls: list[Doc] = []

    def walk(node: Any, enclosing_class: str | None) -> None:
        t = node.type
        if t == "import_statement":
            src = _field(node, "source")
            if src is not None:
                imports.append(
                    {
                        "module": _text(src, source).strip("'\""),
                        "names": _js_imported_names(node, source),
                    }
                )
        elif t == "interface_declaration":
            name = _field(node, "name")
            if name is not None:
                symbols.append(_symbol(name, "interface", node, source, None))
        elif t == "type_alias_declaration":
            name = _field(node, "name")
            if name is not None:
                symbols.append(_symbol(name, "type_alias", node, source, None))
        elif t == "class_declaration":
            name = _field(node, "name")
            if name is not None:
                class_name = _text(name, source)
                symbols.append(_symbol(name, "class", node, source, None))
                _ts_heritage(node, class_name, source, inheritances)
                body = _field(node, "body")
                if body is not None:
                    for child in _named(body):
                        walk(child, class_name)
                return
        elif t == "method_definition":
            name = _field(node, "name")
            if name is not None and enclosing_class:
                method_name = _text(name, source)
                symbols.append(
                    _symbol(
                        name,
                        "method",
                        node,
                        source,
                        enclosing_class,
                        signature=_ts_signature(node, method_name, source),
                    )
                )
                body = _field(node, "body")
                if body is not None:
                    caller_key = (
                        f"{enclosing_class}.{method_name}" if enclosing_class else method_name
                    )
                    _extract_calls(body, caller_key, source, calls)
        elif t == "function_declaration":
            name = _field(node, "name")
            if name is not None:
                func_name = _text(name, source)
                symbols.append(
                    _symbol(
                        name,
                        "function",
                        node,
                        source,
                        None,
                        signature=_ts_signature(node, func_name, source),
                    )
                )
                body = _field(node, "body")
                if body is not None:
                    _extract_calls(body, func_name, source, calls)
        elif t == "export_statement":
            for child in _named(node):
                walk(child, enclosing_class)
            return
        elif t == "lexical_declaration":
            if not enclosing_class:
                is_const = _text(node, source).startswith("const")
                for decl in _named(node):
                    if decl.type == "variable_declarator":
                        name = _field(decl, "name")
                        if name is not None and name.type == "identifier":
                            symbols.append(
                                _symbol(
                                    name, "constant" if is_const else "variable", node, source, None
                                )
                            )
        elif t == "enum_declaration":
            name = _field(node, "name")
            if name is not None:
                symbols.append(_symbol(name, "enum", node, source, None))
        for child in _named(node):
            walk(child, enclosing_class)

    walk(root, None)
    return {
        "symbols": symbols,
        "imports": imports,
        "inheritances": inheritances,
        "calls": calls,
        "moduleDocstring": _leading_module_comment(root, source),
    }


def _extract_require_calls(node: Any, source: bytes, imports: list[Doc]) -> None:
    if node.type == "call_expression":
        func = _field(node, "function")
        if func is not None and _text(func, source) == "require":
            args = _field(node, "arguments")
            if args is not None:
                first = _named(args)[0] if _named(args) else None
                if first is not None and first.type == "string":
                    imports.append({"module": _text(first, source).strip("'\""), "names": []})
    for child in _named(node):
        _extract_require_calls(child, source, imports)


def _extract_javascript(root: Any, source: bytes) -> Doc:
    symbols: list[Doc] = []
    imports: list[Doc] = []
    inheritances: list[Doc] = []
    calls: list[Doc] = []

    def walk(node: Any, enclosing_class: str | None) -> None:
        t = node.type
        if t == "import_statement":
            src = _field(node, "source")
            if src is not None:
                imports.append(
                    {
                        "module": _text(src, source).strip("'\""),
                        "names": _js_imported_names(node, source),
                    }
                )
        elif t == "expression_statement":
            children = _named(node)
            if children:
                _extract_require_calls(children[0], source, imports)
        elif t in ("lexical_declaration", "variable_declaration"):
            is_const = _text(node, source).startswith("const")
            for decl in _named(node):
                if decl.type == "variable_declarator":
                    value = _field(decl, "value")
                    if value is not None:
                        _extract_require_calls(value, source, imports)
                    if not enclosing_class:
                        name = _field(decl, "name")
                        if name is not None and name.type == "identifier":
                            symbols.append(
                                _symbol(
                                    name, "constant" if is_const else "variable", node, source, None
                                )
                            )
        elif t == "class_declaration":
            name = _field(node, "name")
            if name is not None:
                class_name = _text(name, source)
                symbols.append(_symbol(name, "class", node, source, None))
                _ts_heritage(node, class_name, source, inheritances)
                body = _field(node, "body")
                if body is not None:
                    for child in _named(body):
                        walk(child, class_name)
                return
        elif t == "method_definition":
            name = _field(node, "name")
            if name is not None and enclosing_class:
                method_name = _text(name, source)
                symbols.append(
                    _symbol(
                        name,
                        "method",
                        node,
                        source,
                        enclosing_class,
                        signature=_ts_signature(node, method_name, source),
                    )
                )
                body = _field(node, "body")
                if body is not None:
                    caller_key = (
                        f"{enclosing_class}.{method_name}" if enclosing_class else method_name
                    )
                    _extract_calls(body, caller_key, source, calls)
        elif t == "function_declaration":
            name = _field(node, "name")
            if name is not None:
                func_name = _text(name, source)
                symbols.append(
                    _symbol(
                        name,
                        "function",
                        node,
                        source,
                        None,
                        signature=_ts_signature(node, func_name, source),
                    )
                )
                body = _field(node, "body")
                if body is not None:
                    _extract_calls(body, func_name, source, calls)
        for child in _named(node):
            walk(child, enclosing_class)

    walk(root, None)
    return {
        "symbols": symbols,
        "imports": imports,
        "inheritances": inheritances,
        "calls": calls,
        "moduleDocstring": _leading_module_comment(root, source),
    }


def _symbol(
    name_node: Any,
    kind: str,
    node: Any,
    source: bytes,
    enclosing: str | None,
    *,
    signature: str | None = None,
) -> Doc:
    return {
        "name": _text(name_node, source),
        "kind": kind,
        "startLine": node.start_point[0],
        "endLine": node.end_point[0],
        "enclosingName": enclosing,
        "signature": signature,
        "docstring": _leading_block_comment(node, source),
    }


_EXTRACTORS = {
    "python": _extract_python,
    "typescript": _extract_typescript,
    "javascript": _extract_javascript,
}


def extract_from_tree(root: Any, lang: str, source: bytes) -> Doc:
    extractor = _EXTRACTORS.get(lang)
    if extractor is None:
        # Go/Rust extraction lands with those fixtures; empty is safe (the file
        # entity is still created by extract_from_source).
        return {"symbols": [], "imports": [], "inheritances": [], "calls": []}
    return extractor(root, source)


# =============================================================================
# Public API
# =============================================================================


def _provenance(file_path: str, start_line: int) -> Doc:
    return {
        "sourceType": "observation",
        "sourceId": None,
        "externalRef": f"{file_path}:{start_line + 1}",
        "extractor": "tree-sitter",
        "extractionMethod": "tree-sitter",
    }


def _confidence() -> Doc:
    return {"score": 1.0, "basis": "direct_observation"}


def _attach_notes(
    root: Any,
    source: bytes,
    file_entity: Doc,
    symbol_spans: list[tuple[int, int, Doc]],
) -> None:
    """Give every rationale/citation note to the innermost symbol around it.

    A note on a line inside a method belongs to the method, not to its class
    and not to the file; only a note outside every symbol is the file's.
    """
    notes: list[Doc] = []
    _comment_notes(root, source, notes)
    for note in sorted(notes, key=lambda n: n["line"]):
        target = file_entity
        best_span: int | None = None
        for start, end, entity in symbol_spans:
            if start <= note["line"] <= end and (best_span is None or end - start < best_span):
                best_span = end - start
                target = entity
        if note["observation"] not in target["observations"]:
            target["observations"].append(note["observation"])


def extract_from_source(source_code: str, file_path: str, lang: str) -> Doc:
    parser = _get_parser(lang)
    source = source_code.encode("utf-8")
    tree = parser.parse(source)
    extraction = extract_from_tree(tree.root_node, lang, source)

    entities: list[Doc] = []
    relations: list[Doc] = []
    entity_names: set[str] = set()

    file_entity_key = encoding.file_entity_name(file_path)
    entity_names.add(file_entity_key)
    file_observations = [
        encoding.file_path_observation(file_path),
        f"Language: {lang}",
        encoding.symbol_kind_observation("File"),
    ]
    module_docstring = extraction.get("moduleDocstring")
    if module_docstring:
        file_observations.append(f"docstring: {module_docstring}")
    file_entity = {
        "name": file_entity_key,
        "entityType": "system",
        "observations": file_observations,
        "provenance": _provenance(file_path, 0),
        "confidence": _confidence(),
    }
    entities.append(file_entity)

    # (startLine, endLine, entity) per symbol, for attaching rationale comments
    # to the innermost symbol that encloses them.
    symbol_spans: list[tuple[int, int, Doc]] = []

    symbol_name_map: dict[str, str] = {}
    for sym in extraction["symbols"]:
        entity_name = _build_entity_name(sym["name"], file_path, sym["enclosingName"])
        if entity_name in entity_names:
            continue
        entity_names.add(entity_name)
        if sym["enclosingName"]:
            symbol_name_map[f"{sym['enclosingName']}.{sym['name']}"] = entity_name
        else:
            symbol_name_map[sym["name"]] = entity_name

        observations = [
            encoding.file_path_observation(file_path),
            encoding.line_range_observation(sym["startLine"], sym["endLine"]),
            encoding.symbol_kind_observation(sym["kind"]),
        ]
        if sym.get("signature"):
            observations.append(f"signature: {sym['signature']}")
        if sym.get("docstring"):
            observations.append(f"docstring: {sym['docstring']}")
        symbol_entity = {
            "name": entity_name,
            "entityType": kind_to_entity_type(sym["kind"]),
            "observations": observations,
            "provenance": _provenance(file_path, sym["startLine"]),
            "confidence": _confidence(),
        }
        entities.append(symbol_entity)
        symbol_spans.append((sym["startLine"], sym["endLine"], symbol_entity))
        relations.append(
            {
                "from": entity_name,
                "to": file_entity_key,
                "relationType": "part_of",
                "polarity": None,
                "strength": "strong",
                "evidence": f"Defined in {file_path} at line {sym['startLine'] + 1}",
            }
        )
        if sym["enclosingName"]:
            enclosing_entity_name = _build_entity_name(sym["enclosingName"], file_path)
            if enclosing_entity_name in entity_names:
                relations.append(
                    {
                        "from": entity_name,
                        "to": enclosing_entity_name,
                        "relationType": "part_of",
                        "polarity": None,
                        "strength": "strong",
                        "evidence": f"Enclosed by {sym['enclosingName']}",
                    }
                )

    unresolved_inheritances: list[Doc] = []
    for inh in extraction["inheritances"]:
        child_name = symbol_name_map.get(inh["child"], _build_entity_name(inh["child"], file_path))
        parent_local = symbol_name_map.get(inh["parent"])
        if parent_local is None:
            # The base class is imported; naming it as though it lived in this
            # file invents an entity that never exists, so the edge is dropped
            # at import. Defer it to the cross-file pass instead.
            unresolved_inheritances.append({"caller": child_name, "callee": inh["parent"]})
            continue
        parent_name = parent_local
        relations.append(
            {
                "from": child_name,
                "to": parent_name,
                "relationType": "instance_of",
                "polarity": None,
                "strength": "strong",
                "evidence": f"{inh['child']} extends/implements {inh['parent']}",
            }
        )

    unresolved_calls: list[Doc] = []
    for call in extraction["calls"]:
        caller_name = symbol_name_map.get(call["caller"])
        callee_name = symbol_name_map.get(call["callee"])
        if caller_name and callee_name:
            relations.append(
                {
                    "from": caller_name,
                    "to": callee_name,
                    "relationType": "calls",
                    "polarity": None,
                    "strength": "moderate",
                    "evidence": encoding.call_evidence(
                        caller_name, call["callee"], file_path, call["line"]
                    ),
                }
            )
        elif caller_name:
            # Callee is not defined in this file; the cross-file pass may find
            # it, and needs the call site to anchor the edge it emits.
            unresolved_calls.append(
                {"caller": caller_name, "callee": call["callee"], "line": call["line"]}
            )

    _attach_notes(tree.root_node, source, file_entity, symbol_spans)

    vocabulary: set[str] = set()
    _string_literal_vocabulary(tree.root_node, source, vocabulary)

    return {
        "entities": entities,
        "relations": relations,
        "language": lang,
        "imports": extraction["imports"],
        "symbols": symbol_name_map,
        "stringLiterals": sorted(vocabulary),
        "symbolKinds": {
            key: kind_to_entity_type(sym["kind"])
            for sym in extraction["symbols"]
            for key in (
                [f"{sym['enclosingName']}.{sym['name']}"] if sym["enclosingName"] else [sym["name"]]
            )
        },
        "unresolvedCalls": unresolved_calls,
        "unresolvedInheritances": unresolved_inheritances,
    }


def _git_paths(root: str, *args: str) -> set[str] | None:
    """Slash-separated paths under ``root`` listed by ``git ls-files``, or
    ``None`` when git can't answer (not a work tree, git missing, git broken).
    """
    try:
        done = subprocess.run(
            ["git", "ls-files", "-z", *args],
            cwd=root,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return {p for p in done.stdout.decode("utf-8", "surrogateescape").split("\0") if p}


def _git_visibility(root: str) -> tuple[set[str], set[str]] | None:
    """``(tracked, visible)`` paths for a work tree, else ``None``.

    ``visible`` adds the untracked-but-not-ignored files: code that is merely
    new is what someone mapping a working tree most wants to see, while a
    non-code file is held to the stricter ``tracked`` set.
    """
    tracked = _git_paths(root)
    if tracked is None:
        return None
    untracked = _git_paths(root, "--others", "--exclude-standard") or set()
    return tracked, tracked | untracked


def _skips_a_component(rel_path: str) -> bool:
    """True when a directory component of ``rel_path`` is out of scope.

    Applied to every component but the last (the filename itself is judged by
    extension/text-kind, not by this)."""
    parts = rel_path.replace(os.sep, "/").split("/")
    return any(part in SKIP_DIRS or part.startswith(".") for part in parts[:-1])


def _static_scope(
    rel: str, visibility: tuple[set[str], set[str]] | None, include_tests: bool
) -> bool:
    """The part of the collection rule decidable from the path and git alone —
    no stat, no read. Directory/dotfile skipping, extension/text-kind
    recognition, git tracking visibility, and the test-path exclusion.

    Deliberately excludes symlink-ness, ``MAX_TEXT_FILE_BYTES``, and
    decodability: those are properties of the file's *current bytes*, which
    can regress out from under a previously-collected file — and catching
    exactly that regression is what the incremental-update shrink guard
    exists for (see ``codebasediff``). Folding them in here would let a
    corrupted-in-place file quietly disappear from the diff instead of
    tripping the guard.
    """
    if _skips_a_component(rel):
        return False
    lang = detect_language(rel)
    if lang is None and detect_text_kind(rel) is None:
        return False
    if visibility is not None:
        tracked, visible = visibility
        allowed = visible if lang is not None else tracked
        if rel.replace(os.sep, "/") not in allowed:
            return False
    return not (not include_tests and _is_test_file(rel))


def _collect_content(
    root: str,
    rel: str,
    visibility: tuple[set[str], set[str]] | None,
    include_tests: bool,
) -> str | None:
    """The file's text if ``collect_source_files`` would collect it, else
    ``None`` — the single membership rule, usable per-path as well as during
    the directory walk."""
    if not _static_scope(rel, visibility, include_tests):
        return None
    full = os.path.join(root, rel)
    if os.path.islink(full):
        return None
    if detect_language(rel) is None:
        try:
            if os.path.getsize(full) > MAX_TEXT_FILE_BYTES:
                return None
        except OSError:
            return None
    try:
        with open(full, encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return None


def extractable_paths(project_path: str, *, include_tests: bool = True) -> Callable[[str], bool]:
    """A membership predicate for the *static* scope of ``collect_source_files``
    — path shape, extension/text-kind, git visibility, and ``include_tests``
    — resolved once per project root rather than once per path. Built for the
    incremental-update diff planner: it decides which git-diff-named paths are
    candidates for reconciliation at all (so an ignored, untracked, or
    test-excluded path never becomes one), while leaving whether a candidate
    still actually collects right now (not a symlink, under the size cap,
    decodable) to the real extraction pass and the shrink guard that watches
    it — see ``_static_scope`` for why that split matters.
    """
    root = os.path.abspath(project_path)
    visibility = _git_visibility(root)

    def in_static_scope(rel_path: str) -> bool:
        return _static_scope(rel_path, visibility, include_tests)

    return in_static_scope


def collect_source_files(project_path: str, include_tests: bool = True) -> list[Doc]:
    """Sorted list of {relativePath, content} for supported files (sorted so
    the walk is deterministic).

    Recognised non-code text files come along too — they become root file
    entities. A binary (which fails to decode) or an oversized data file is
    skipped, and inside a git work tree so is anything git ignores (see the
    module docstring).
    """
    files: list[Doc] = []
    root = os.path.abspath(project_path)
    visibility = _git_visibility(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for filename in sorted(filenames):
            full = os.path.join(dirpath, filename)
            rel = os.path.relpath(full, root)
            content = _collect_content(root, rel, visibility, include_tests)
            if content is not None:
                files.append({"relativePath": rel, "content": content})
    return sorted(files, key=lambda f: f["relativePath"])


def _text_file_entity(path: str, kind: str) -> Doc:
    """A root entity for a non-code file — no parse, so no symbols and no edges.

    Its provenance says ``automated``/``file-scan`` rather than ``tree-sitter``:
    nothing parsed it, and claiming otherwise would misreport how the record
    was obtained.
    """
    return {
        "name": encoding.file_entity_name(path),
        "entityType": "system",
        "observations": [
            encoding.file_path_observation(path),
            f"Language: {kind}",
            encoding.symbol_kind_observation("File"),
        ],
        "provenance": {
            "sourceType": "observation",
            "sourceId": None,
            "externalRef": f"{path}:1",
            "extractor": "file-scan",
            "extractionMethod": "automated",
        },
        "confidence": _confidence(),
    }


def _is_test_file(rel: str) -> bool:
    return resolution.is_test_path(rel)


def extract_from_files(files: list[Doc], *, external_entities: bool = True) -> Doc:
    """Parse every file, then join the edges that span files.

    The per-file pass cannot see past its own file, so imports and calls to
    imported symbols are resolved afterwards against the full file set (see
    ``theloom.extraction.resolution``).
    """
    all_entities: list[Doc] = []
    all_relations: list[Doc] = []
    entity_names: set[str] = set()
    per_file: list[Doc] = []
    doc_files: list[Doc] = []
    file_paths: set[str] = set()
    for file in files:
        lang = detect_language(file["relativePath"])
        path = resolution.normalise_path(file["relativePath"])
        if lang is None:
            kind = detect_text_kind(file["relativePath"])
            if kind is None:
                continue
            entity = _text_file_entity(path, kind)
            if entity["name"] not in entity_names:
                entity_names.add(entity["name"])
                all_entities.append(entity)
                file_paths.add(path)
                if kind in doclinks.DOC_EXTENSIONS:
                    doc_files.append({"path": path, "content": file["content"]})
            continue
        file_paths.add(path)
        result = extract_from_source(file["content"], path, lang)
        for entity in result["entities"]:
            if entity["name"] not in entity_names:
                entity_names.add(entity["name"])
                all_entities.append(entity)
        all_relations.extend(result["relations"])
        per_file.append(
            {
                "path": path,
                "language": result["language"],
                "symbolKinds": result["symbolKinds"],
                "imports": result["imports"],
                "symbols": result["symbols"],
                "stringLiterals": result["stringLiterals"],
                "unresolvedCalls": result["unresolvedCalls"],
                "unresolvedInheritances": result["unresolvedInheritances"],
            }
        )

    known_files = frozenset(record["path"] for record in per_file)
    imports = resolution.resolve_imports(per_file, known_files, external_entities=external_entities)
    calls = resolution.resolve_calls(per_file, known_files)
    inheritances = resolution.resolve_inheritances(per_file, known_files)
    docs = doclinks.resolve_doc_links(doc_files, frozenset(file_paths), per_file)
    for entity in imports["entities"]:
        if entity["name"] not in entity_names:
            entity_names.add(entity["name"])
            all_entities.append(entity)
    all_relations.extend(imports["relations"])
    all_relations.extend(calls["relations"])
    all_relations.extend(inheritances["relations"])
    all_relations.extend(docs["relations"])

    return {
        "entities": all_entities,
        "relations": all_relations,
        "resolution": {
            **imports["stats"],
            **calls["stats"],
            **inheritances["stats"],
            **docs["stats"],
        },
    }


def extract_codebase(
    project_path: str,
    *,
    include_tests: bool = True,
) -> Doc:
    if not os.path.exists(project_path):
        raise FileNotFoundError(f"Project path does not exist: {project_path}")
    files = collect_source_files(project_path, include_tests)
    result = extract_from_files(files)
    entities = result["entities"]
    relations = result["relations"]
    resolution_stats = result["resolution"]

    entity_breakdown: dict[str, int] = {}
    for entity in entities:
        entity_breakdown[entity["entityType"]] = entity_breakdown.get(entity["entityType"], 0) + 1
    relation_breakdown: dict[str, int] = {}
    for relation in relations:
        rt = relation["relationType"]
        relation_breakdown[rt] = relation_breakdown.get(rt, 0) + 1

    total_symbols = sum(1 for e in entities if e["entityType"] != "system")
    return {
        "entities": entities,
        "relations": relations,
        "stats": {
            "totalFiles": len(files),
            "totalSymbols": total_symbols,
            "totalEntities": len(entities),
            "totalRelations": len(relations),
            "entityBreakdown": entity_breakdown,
            "relationBreakdown": relation_breakdown,
        },
        "resolution": resolution_stats,
        "extractionMethod": "tree-sitter",
        "indexPath": "",
    }
