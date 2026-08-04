"""Tree-sitter codebase extraction.

Uses the native py-tree-sitter bindings. Symbol/relation MAPPING: file ->
system, class/interface/struct/trait/type_alias/enum -> concept,
function/method -> procedure, variable/constant -> variable; entity name
``qualified (fileBaseName)``; symbol part_of file (+ enclosing), imports ->
requires (resolved to the target file entity, or a ``pkg:`` node for a
third-party package), inheritance -> instance_of, calls -> related_to
(intra-file directly, cross-file via ``theloom.extraction.resolution``).
Grammar-version differences can shift a symbol across tree-sitter releases; the
entity/relation semantics are the contract.

Files are traversed in SORTED order so output is deterministic (an unsorted
directory walk would be machine-dependent).
"""

from __future__ import annotations

import os
from typing import Any

from theloom.extraction import resolution

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
# Per-language extraction
# =============================================================================


def _extract_calls(node: Any, caller: str, source: bytes, calls: list[Doc]) -> None:
    if node.type in ("call", "call_expression"):
        func = _field(node, "function")
        if func is not None and func.type == "identifier":
            calls.append({"caller": caller, "callee": _text(func, source)})
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
                symbols.append(
                    {
                        "name": class_name,
                        "kind": "class",
                        "startLine": node.start_point[0],
                        "endLine": node.end_point[0],
                        "enclosingName": enclosing_class,
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
                symbols.append(
                    {
                        "name": func_name,
                        "kind": "method" if enclosing_class else "function",
                        "startLine": node.start_point[0],
                        "endLine": node.end_point[0],
                        "enclosingName": enclosing_class,
                    }
                )
                body = _field(node, "body")
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
    return {"symbols": symbols, "imports": imports, "inheritances": inheritances, "calls": calls}


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
                symbols.append(_symbol(name, "method", node, source, enclosing_class))
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
                symbols.append(_symbol(name, "function", node, source, None))
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
    return {"symbols": symbols, "imports": imports, "inheritances": inheritances, "calls": calls}


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
                symbols.append(_symbol(name, "method", node, source, enclosing_class))
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
                symbols.append(_symbol(name, "function", node, source, None))
                body = _field(node, "body")
                if body is not None:
                    _extract_calls(body, func_name, source, calls)
        for child in _named(node):
            walk(child, enclosing_class)

    walk(root, None)
    return {"symbols": symbols, "imports": imports, "inheritances": inheritances, "calls": calls}


def _symbol(name_node: Any, kind: str, node: Any, source: bytes, enclosing: str | None) -> Doc:
    return {
        "name": _text(name_node, source),
        "kind": kind,
        "startLine": node.start_point[0],
        "endLine": node.end_point[0],
        "enclosingName": enclosing,
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


def extract_from_source(source_code: str, file_path: str, lang: str) -> Doc:
    parser = _get_parser(lang)
    source = source_code.encode("utf-8")
    tree = parser.parse(source)
    extraction = extract_from_tree(tree.root_node, lang, source)

    entities: list[Doc] = []
    relations: list[Doc] = []
    entity_names: set[str] = set()

    file_entity_name = f"file:{file_path}"
    entity_names.add(file_entity_name)
    entities.append(
        {
            "name": file_entity_name,
            "entityType": "system",
            "observations": [f"File path: {file_path}", f"Language: {lang}", "Symbol kind: File"],
            "provenance": _provenance(file_path, 0),
            "confidence": _confidence(),
        }
    )

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

        entities.append(
            {
                "name": entity_name,
                "entityType": kind_to_entity_type(sym["kind"]),
                "observations": [
                    f"File path: {file_path}",
                    f"Line range: {sym['startLine'] + 1}-{sym['endLine'] + 1}",
                    f"Symbol kind: {sym['kind']}",
                ],
                "provenance": _provenance(file_path, sym["startLine"]),
                "confidence": _confidence(),
            }
        )
        relations.append(
            {
                "from": entity_name,
                "to": file_entity_name,
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
                    "relationType": "related_to",
                    "polarity": None,
                    "strength": "moderate",
                    "evidence": f"{call['caller']} calls {call['callee']}",
                }
            )
        elif caller_name:
            # Callee is not defined in this file; the cross-file pass may find it.
            unresolved_calls.append({"caller": caller_name, "callee": call["callee"]})

    return {
        "entities": entities,
        "relations": relations,
        "imports": extraction["imports"],
        "symbols": symbol_name_map,
        "unresolvedCalls": unresolved_calls,
        "unresolvedInheritances": unresolved_inheritances,
    }


def collect_source_files(project_path: str, include_tests: bool = True) -> list[Doc]:
    """Sorted list of {relativePath, content} for supported files (sorted so
    the walk is deterministic)."""
    files: list[Doc] = []
    root = os.path.abspath(project_path)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for filename in sorted(filenames):
            full = os.path.join(dirpath, filename)
            if os.path.islink(full):
                continue
            rel = os.path.relpath(full, root)
            if detect_language(rel) is None:
                continue
            if not include_tests and _is_test_file(rel):
                continue
            try:
                with open(full, encoding="utf-8") as handle:
                    content = handle.read()
            except (OSError, UnicodeDecodeError):
                continue
            files.append({"relativePath": rel, "content": content})
    return sorted(files, key=lambda f: f["relativePath"])


def _is_test_file(rel: str) -> bool:
    lowered = rel.lower()
    return any(marker in lowered for marker in (".test.", ".spec.", "__tests__/", "__test__/"))


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
    for file in files:
        lang = detect_language(file["relativePath"])
        if lang is None:
            continue
        path = resolution.normalise_path(file["relativePath"])
        result = extract_from_source(file["content"], path, lang)
        for entity in result["entities"]:
            if entity["name"] not in entity_names:
                entity_names.add(entity["name"])
                all_entities.append(entity)
        all_relations.extend(result["relations"])
        per_file.append(
            {
                "path": path,
                "imports": result["imports"],
                "symbols": result["symbols"],
                "unresolvedCalls": result["unresolvedCalls"],
                "unresolvedInheritances": result["unresolvedInheritances"],
            }
        )

    known_files = frozenset(record["path"] for record in per_file)
    imports = resolution.resolve_imports(per_file, known_files, external_entities=external_entities)
    calls = resolution.resolve_calls(per_file, known_files)
    inheritances = resolution.resolve_inheritances(per_file, known_files)
    for entity in imports["entities"]:
        if entity["name"] not in entity_names:
            entity_names.add(entity["name"])
            all_entities.append(entity)
    all_relations.extend(imports["relations"])
    all_relations.extend(calls["relations"])
    all_relations.extend(inheritances["relations"])

    return {
        "entities": all_entities,
        "relations": all_relations,
        "resolution": {**imports["stats"], **calls["stats"], **inheritances["stats"]},
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
