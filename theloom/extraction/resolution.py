"""Cross-file edge resolution for codebase extraction.

Tree-sitter parses one file at a time, so two kinds of edge are left dangling
by the per-file pass:

* an **import** lands on the raw module string the source wrote
  (``theloom.cli.registry``, ``../../lib/BundleContext``) rather than on the
  file entity that module denotes;
* a **call to an imported symbol** has no resolvable target, because the callee
  is defined in a file the per-file pass never saw.

Both are joins that can only be made once every file is known. This module is
that second pass. It is deterministic and LLM-free: an edge is emitted only
when the import evidence names the target, or when exactly one candidate in the
whole project carries the callee's name.

Resolution confidence is carried in the Loom's own vocabulary rather than a
parallel label scheme: an edge proven by an import statement is
``direct_observation``; one resolved by a unique project-wide name match is
``inference``. Ambiguous names resolve to nothing at all — a wrong edge is
worse than a missing one, because every downstream analysis (cycles,
centrality, components) treats edges as fact.
"""

from __future__ import annotations

import os
import posixpath
from typing import Any

Doc = dict[str, Any]

# Suffix probes for a module specifier that omits its extension, in the order a
# bundler would try them. ``/index`` forms come last so ``./x.ts`` wins over
# ``./x/index.ts`` when both exist.
_JS_SUFFIXES = (
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    "/index.ts",
    "/index.tsx",
    "/index.js",
    "/index.jsx",
)
_PY_SUFFIXES = (".py", "/__init__.py")

EXTERNAL_PREFIX = "pkg:"

# Kinds a call or a base-class reference can legitimately land on.
_CALLABLE_KINDS = frozenset({"procedure", "concept"})


# Names that belong to a language runtime, not to any project symbol. A bare
# call to one of these is never a dependency on a file in this repository —
# and because such a name may coincide with a single project symbol, the
# unique-name rule would otherwise weld hundreds of callers to it. (Observed:
# 288 Python ``len()`` calls resolving to a lone TypeScript ``len`` constant,
# making it the most-connected node in the graph.)
_BUILTINS = frozenset(
    {
        # Python
        "len",
        "print",
        "str",
        "int",
        "float",
        "bool",
        "list",
        "dict",
        "set",
        "tuple",
        "type",
        "range",
        "enumerate",
        "zip",
        "map",
        "filter",
        "sorted",
        "sum",
        "min",
        "max",
        "abs",
        "round",
        "open",
        "isinstance",
        "issubclass",
        "getattr",
        "setattr",
        "hasattr",
        "repr",
        "hash",
        "id",
        "iter",
        "next",
        "any",
        "all",
        "format",
        "super",
        "property",
        "staticmethod",
        "classmethod",
        "bytes",
        "frozenset",
        "reversed",
        "slice",
        "vars",
        "dir",
        "callable",
        # JS/TS
        "require",
        "fetch",
        "parseInt",
        "parseFloat",
        "encodeURIComponent",
        "decodeURIComponent",
        "setTimeout",
        "setInterval",
        "clearTimeout",
        "clearInterval",
        "structuredClone",
        "queueMicrotask",
    }
)


def file_entity_name(path: str) -> str:
    """The entity name the extractor gives a file."""
    return f"file:{path}"


def external_entity_name(package: str) -> str:
    """The entity name for a third-party package."""
    return f"{EXTERNAL_PREFIX}{package}"


def external_package(module: str) -> str:
    """The distributable package a bare module specifier belongs to.

    ``theloom.cli.registry`` -> ``theloom``; ``@scope/pkg/sub`` -> ``@scope/pkg``
    (npm scoped packages carry their scope); ``sigma/rendering`` -> ``sigma``.
    """
    module = module.strip()
    if module.startswith("@"):
        parts = module.split("/")
        return "/".join(parts[:2]) if len(parts) >= 2 else module
    if "." in module:
        return module.split(".", 1)[0]
    return module.split("/", 1)[0]


def _probe(base: str, suffixes: tuple[str, ...], known_files: frozenset[str]) -> str | None:
    for suffix in suffixes:
        candidate = f"{base}{suffix}"
        if candidate in known_files:
            return candidate
    return None


def _resolve_relative_js(module: str, importer: str, known_files: frozenset[str]) -> str | None:
    base = posixpath.normpath(posixpath.join(posixpath.dirname(importer), module))
    if base.startswith(".."):  # escaped the project root
        return None
    if base in known_files:
        return base
    return _probe(base, _JS_SUFFIXES, known_files)


def _resolve_relative_python(module: str, importer: str, known_files: frozenset[str]) -> str | None:
    """``from ..pkg import x`` — leading dots count levels up from the importer."""
    dots = len(module) - len(module.lstrip("."))
    remainder = module[dots:]
    # One dot means "this package" (the importer's own directory), so the first
    # dot costs no level; each dot after it climbs one.
    package_dir = posixpath.dirname(importer)
    for _ in range(dots - 1):
        package_dir = posixpath.dirname(package_dir)
        if not package_dir:
            break
    base = posixpath.join(package_dir, remainder.replace(".", "/")) if remainder else package_dir
    return _probe(base.lstrip("/"), _PY_SUFFIXES, known_files)


def resolve_module(module: str, importer: str, known_files: frozenset[str]) -> str | None:
    """The project file a module specifier denotes, or None if it is external.

    ``importer`` is the project-relative path of the file doing the importing;
    relative specifiers are resolved against its directory.
    """
    module = module.strip()
    if not module:
        return None
    if module.startswith("."):
        if module.startswith("./") or module.startswith("../"):
            return _resolve_relative_js(module, importer, known_files)
        return _resolve_relative_python(module, importer, known_files)
    # Absolute: a dotted Python path, or a bare/scoped JS package name that may
    # still name a project file when the project uses path aliases.
    dotted = _probe(module.replace(".", "/"), _PY_SUFFIXES, known_files)
    if dotted is not None:
        return dotted
    return _probe(module, _JS_SUFFIXES, known_files)


def _relation(
    from_name: str,
    to_name: str,
    relation_type: str,
    evidence: str,
    *,
    proven: bool,
    strength: str = "moderate",
) -> Doc:
    """A resolved edge, tagged with how it was established.

    ``proven`` distinguishes an edge the source states outright from one this
    module deduced, so a reader (and `provenance-audit`) can tell them apart.
    """
    return {
        "from": from_name,
        "to": to_name,
        "relationType": relation_type,
        "polarity": None,
        "strength": strength,
        "evidence": evidence,
        "confidence": {
            "score": 0.95 if proven else 0.7,
            "basis": "direct_observation" if proven else "inference",
        },
    }


def resolve_imports(
    per_file: list[Doc],
    known_files: frozenset[str],
    *,
    external_entities: bool = True,
) -> Doc:
    """Join each file's imports to the file or package they denote.

    Returns ``{entities, relations, stats}``. ``entities`` holds one node per
    distinct external package (empty when ``external_entities`` is False).
    """
    entities: list[Doc] = []
    relations: list[Doc] = []
    seen_external: set[str] = set()
    seen_edges: set[tuple[str, str]] = set()
    internal = external = unresolved = 0

    for record in per_file:
        importer = str(record["path"])
        source_name = file_entity_name(importer)
        for imp in record.get("imports", []):
            module = str(imp.get("module", "")).strip()
            if not module:
                continue
            target_path = resolve_module(module, importer, known_files)
            if target_path is not None:
                if target_path == importer:  # a module importing itself
                    continue
                target_name = file_entity_name(target_path)
                internal += 1
                evidence = f"{importer} imports {module} -> {target_path}"
                proven = True
            elif external_entities:
                package = external_package(module)
                if not package:
                    continue
                target_name = external_entity_name(package)
                if target_name not in seen_external:
                    seen_external.add(target_name)
                    entities.append(
                        {
                            "name": target_name,
                            "entityType": "system",
                            "observations": [
                                f"Package: {package}",
                                "Symbol kind: ExternalPackage",
                                "Dependency: third-party (not a file in this project)",
                            ],
                            "provenance": {
                                "sourceType": "observation",
                                "sourceId": None,
                                "externalRef": None,
                                "extractor": "tree-sitter",
                                "extractionMethod": "tree-sitter",
                            },
                            "confidence": {"score": 0.95, "basis": "direct_observation"},
                        }
                    )
                external += 1
                evidence = f"{importer} imports the third-party package {package}"
                proven = True
            else:
                unresolved += 1
                continue

            edge = (source_name, target_name)
            if edge in seen_edges:
                continue
            seen_edges.add(edge)
            relations.append(
                _relation(source_name, target_name, "requires", evidence, proven=proven)
            )

    return {
        "entities": entities,
        "relations": relations,
        "stats": {
            "internalImports": internal,
            "externalImports": external,
            "unresolvedImports": unresolved,
        },
    }


def _imported_symbol_origin(
    callee: str, record: Doc, importer: str, known_files: frozenset[str]
) -> str | None:
    """The project file an imported name came from, per this file's imports."""
    for imp in record.get("imports", []):
        names = imp.get("names") or []
        if callee not in names:
            continue
        module = str(imp.get("module", "")).strip()
        if not module:
            continue
        target = resolve_module(module, importer, known_files)
        if target is not None and target != importer:
            return target
    return None


def _resolve_symbol_edges(
    per_file: list[Doc],
    known_files: frozenset[str],
    *,
    field: str,
    relation_type: str,
    verb: str,
) -> Doc:
    """Join edges whose target symbol is defined in another file.

    Two resolvers, strongest first. **Import-guided**: the source file imports
    the target's name from a module that resolves to a project file which
    defines it — the source states the link, so the edge is proven. **Unique
    name**: exactly one symbol in the whole project bears the name; the edge is
    a deduction. A name defined more than once resolves to nothing, because
    picking one would be a guess presented as structure.

    Calls and base classes differ only in the edge they produce, so both run
    through this one resolver.
    """
    # (file, bare symbol name) -> entity name; and, for the unique-name rule,
    # bare name -> every candidate with the language and kind needed to judge it.
    by_file: dict[tuple[str, str], str] = {}
    by_name: dict[str, set[tuple[str, str, str]]] = {}
    for record in per_file:
        path = str(record["path"])
        language = str(record.get("language", ""))
        kinds = record.get("symbolKinds", {})
        for bare, entity_name in record.get("symbols", {}).items():
            by_file.setdefault((path, bare), entity_name)
            by_name.setdefault(bare, set()).add((entity_name, language, str(kinds.get(bare, ""))))

    relations: list[Doc] = []
    seen: set[tuple[str, str]] = set()
    proven_count = inferred_count = ambiguous = 0

    for record in per_file:
        importer = str(record["path"])
        language = str(record.get("language", ""))
        for call in record.get(field, []):
            caller_name = str(call.get("caller", ""))
            callee = str(call.get("callee", ""))
            if not caller_name or not callee:
                continue

            origin = _imported_symbol_origin(callee, record, importer, known_files)
            target = by_file.get((origin, callee)) if origin else None
            proven = target is not None

            if target is None:
                if callee in _BUILTINS:
                    # A language builtin, not a project symbol.
                    ambiguous += 1
                    continue
                # Only candidates the caller could actually reach: same
                # language (a Python file cannot call a TypeScript symbol) and
                # an entity kind that is callable or constructible. A local
                # constant that merely shares the name is not a call target.
                candidates = {
                    name
                    for name, lang, kind in by_name.get(callee, set())
                    if lang == language and kind in _CALLABLE_KINDS
                }
                # Same-file candidates were already resolved by the per-file
                # pass; anything left here is genuinely cross-file.
                if len(candidates) == 1:
                    target = next(iter(candidates))
                elif len(candidates) > 1:
                    ambiguous += 1
                    continue

            if target is None or target == caller_name:
                continue
            edge = (caller_name, target)
            if edge in seen:
                continue
            seen.add(edge)
            if proven:
                proven_count += 1
                evidence = f"{caller_name} {verb} {callee}, imported from {origin}"
            else:
                inferred_count += 1
                evidence = f"{caller_name} {verb} {callee}, the project's only symbol of that name"
            relations.append(_relation(caller_name, target, relation_type, evidence, proven=proven))

    return {
        "relations": relations,
        "stats": {"proven": proven_count, "inferred": inferred_count, "ambiguous": ambiguous},
    }


def resolve_calls(per_file: list[Doc], known_files: frozenset[str]) -> Doc:
    """Cross-file calls: ``related_to``, matching the per-file pass's convention."""
    out = _resolve_symbol_edges(
        per_file,
        known_files,
        field="unresolvedCalls",
        relation_type="related_to",
        verb="calls",
    )
    stats = out["stats"]
    return {
        "relations": out["relations"],
        "stats": {
            "importGuidedCalls": stats["proven"],
            "uniqueNameCalls": stats["inferred"],
            "ambiguousCallsSkipped": stats["ambiguous"],
        },
    }


def resolve_inheritances(per_file: list[Doc], known_files: frozenset[str]) -> Doc:
    """Base classes defined in another file: ``instance_of``.

    Naming an imported base class as though it lived in the subclass's file
    invents an entity that is never created, so the edge is dropped on import —
    the same defect that lost every import edge.
    """
    out = _resolve_symbol_edges(
        per_file,
        known_files,
        field="unresolvedInheritances",
        relation_type="instance_of",
        verb="extends",
    )
    stats = out["stats"]
    return {
        "relations": out["relations"],
        "stats": {
            "importGuidedInheritances": stats["proven"],
            "uniqueNameInheritances": stats["inferred"],
            "ambiguousInheritancesSkipped": stats["ambiguous"],
        },
    }


def normalise_path(path: str) -> str:
    """Project-relative path with forward slashes, for cross-platform keys."""
    return path.replace(os.sep, "/")
