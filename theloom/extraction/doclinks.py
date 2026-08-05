"""Documentation-to-code linking for codebase extraction.

Markdown files already become file entities, but the per-file pass gives them
no edges, so documentation sits as its own island: nothing connects a doc to
the code it specifies, and drift between the two is invisible to every graph
query. This pass is the join, and like ``theloom.extraction.resolution`` it is
deterministic and LLM-free — a mention becomes an edge only when the doc names
the target unambiguously:

* a **repo-relative path** written in the text that is a file the extraction
  actually produced (``theloom/store/falkor.py``) — the doc states the target,
  so the edge is ``direct_observation``;
* a **backtick-quoted symbol name** that is *written the way code is written*
  and is the project's only callable symbol of that name — a deduction, so
  ``inference``.

Everything else links to nothing. A bare word in prose is never a link (docs
are full of English words that happen to be symbol names), a name defined more
than once is never a link (picking one would be a guess presented as
structure), a language builtin is never a link, and a name too short to be
distinctive is never a link.

Backticks by themselves prove nothing, either: docs backtick branch names,
CLI flags, env vars, config keys and JSON fields, and any of those may
collide with the project's only symbol of that name. (``Keep `main` green``
is about the git branch; the repo's only ``main`` is the CLI entry point.) So
a mention counts only when the *name itself* is code-shaped — qualified
(``Reporter.summarize``), snake_case, camel/PascalCase, or an explicit call
form (``main()``) — and only when it lands on a kind of symbol a doc can
reference at all: the same callable kinds the resolver allows, never a
variable that merely shares a config key's name. A lone lowercase word links
to nothing, even when the project defines it exactly once.

Nor does being code-shaped make a term code. A project's own **domain
vocabulary** is written the way code is and quoted the way code is: docs list
``single_source`` among the confidence bases and ``usage_status`` among the
standing observations. Those are values, not callables — but a repo big
enough to define the vocabulary is big enough to also define one function of
the same name, and then the uniqueness rule welds six documents to a semiring
shortest-path routine they never mention. So a name the project itself writes
as a string *value* — an enum value, a status token, a keyed prefix constant
(``"usage_status: "``) — is vocabulary, and vocabulary is never a symbol
link. The vocabulary is read from the product source only: a test file's
``describe("buildCausalGraph")`` quotes a symbol name rather than coining a
term, and counting it would suppress exactly the links docs legitimately make.

These are the guards the unique-name call resolver needed: a wrong edge is
worse than a missing one, because every downstream analysis treats edges as
fact.

Out-degree is capped per document. One index page listing every file in the
repo would otherwise become the most-connected node in the graph while saying
nothing; the drop is reported in the extraction stats rather than hidden.
"""

from __future__ import annotations

import re
from typing import Any

from theloom.extraction.resolution import (
    BUILTIN_NAMES,
    CALLABLE_KINDS,
    file_entity_name,
    is_test_path,
)

Doc = dict[str, Any]

# Text kinds this pass reads. Prose is where a path or a symbol name is a
# deliberate reference; a lockfile mentioning one is coincidence.
DOC_EXTENSIONS = frozenset({"md"})

MAX_LINKS_PER_DOC = 50

# Below this, a name is a word before it is a symbol (``id``, ``ok``, ``at``).
MIN_SYMBOL_CHARS = 3

# A path-shaped token: at least one dotted extension, no whitespace. Whether it
# is real is decided by membership in the extracted file set, never by the shape.
_PATH_RE = re.compile(r"[A-Za-z0-9_./-]*[A-Za-z0-9_-]+\.[A-Za-z][A-Za-z0-9]*")
_BACKTICK_RE = re.compile(r"`([^`\n]+)`")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")


def _symbol_index(per_file: list[Doc]) -> dict[str, set[str]]:
    """Every way a symbol can be written -> the entity names it could mean.

    A method is keyed both qualified (``Reporter.summarize``) and bare
    (``summarize``), so a doc may write either; the uniqueness guard is what
    keeps the bare form honest.

    Only the kinds the resolver treats as reachable targets are indexed. A
    doc referencing a *variable* by name is far more often a config key, an
    env var or a JSON field that happens to collide with one, so those never
    enter the index and can never be the project's "only" symbol of a name.
    """
    index: dict[str, set[str]] = {}
    for record in per_file:
        kinds = record.get("symbolKinds", {})
        for key, entity_name in record.get("symbols", {}).items():
            if str(kinds.get(key, "")) not in CALLABLE_KINDS:
                continue
            index.setdefault(key, set()).add(entity_name)
            bare = key.rsplit(".", 1)[-1]
            if bare != key:
                index.setdefault(bare, set()).add(entity_name)
    return index


def _vocabulary(per_file: list[Doc]) -> frozenset[str]:
    """Every term the project's own source writes as a string value.

    Read from product source only. A test file quotes symbol names constantly
    (``describe("buildCausalGraph")``, ``"open_account"`` in a fixture) without
    coining a single term, so its literals would poison names docs really do
    reference.
    """
    terms: set[str] = set()
    for record in per_file:
        if is_test_path(str(record.get("path", ""))):
            continue
        terms.update(str(term) for term in record.get("stringLiterals", []))
    return frozenset(terms)


def _is_code_shaped(text: str) -> bool:
    """True when the name is written the way code is and prose is not.

    Qualified, snake_case, or carrying a capital (camelCase, PascalCase,
    SCREAMING_CASE). A lone lowercase word — ``main``, ``run``, ``allows`` —
    is a word first and a symbol second, whatever the backticks suggest.
    """
    return "." in text or "_" in text or any(char.isupper() for char in text)


def _path_mentions(line: str, doc_path: str, file_paths: frozenset[str]) -> list[tuple[str, str]]:
    """``(mention text, target entity)`` for each real file path on the line."""
    hits: list[tuple[str, str]] = []
    for match in _PATH_RE.finditer(line):
        text = match.group(0)
        if text == doc_path or text not in file_paths:
            continue
        hits.append((text, file_entity_name(text)))
    return hits


def _symbol_mentions(
    line: str, index: dict[str, set[str]], vocabulary: frozenset[str]
) -> tuple[list[tuple[str, str]], int, int]:
    """``(mentions, ambiguous count, vocabulary count)`` for the line's names."""
    hits: list[tuple[str, str]] = []
    ambiguous = 0
    vocabulary_hits = 0
    for match in _BACKTICK_RE.finditer(line):
        text = match.group(1).strip()
        # An explicit call form is code syntax, so it stands in for the shape
        # test a lone lowercase name would otherwise fail.
        call_form = text.endswith("()")
        if call_form:
            text = text[:-2]
        if _IDENTIFIER_RE.match(text) is None or len(text) < MIN_SYMBOL_CHARS:
            continue
        if not call_form and not _is_code_shaped(text):
            continue
        if text in BUILTIN_NAMES or text.rsplit(".", 1)[-1] in BUILTIN_NAMES:
            continue
        candidates = index.get(text)
        if not candidates:
            continue
        if text in vocabulary:
            # The project writes this term as a value; the doc is quoting the
            # term, not the callable that happens to share its spelling.
            vocabulary_hits += 1
            continue
        if len(candidates) > 1:
            # Counted per occurrence: how often the docs say something the
            # project defines twice is worth knowing.
            ambiguous += 1
            continue
        hits.append((text, next(iter(candidates))))
    return hits, ambiguous, vocabulary_hits


def _relation(
    source: str, target: str, mention: str, doc_path: str, line: int, *, proven: bool
) -> Doc:
    return {
        "from": source,
        "to": target,
        "relationType": "references",
        "polarity": None,
        "strength": "moderate",
        "evidence": f"mentions {mention} at {doc_path}:{line + 1}",
        "confidence": {
            "score": 0.95 if proven else 0.7,
            "basis": "direct_observation" if proven else "inference",
        },
    }


def resolve_doc_links(
    docs: list[Doc],
    file_paths: frozenset[str],
    per_file: list[Doc],
    *,
    max_links: int = MAX_LINKS_PER_DOC,
) -> Doc:
    """Join each doc to the files and symbols it names.

    ``docs`` is ``{path, content}`` per documentation file; ``file_paths`` is
    every file the extraction produced an entity for (code and non-code alike);
    ``per_file`` is the parsed record list the resolution pass also consumes.
    Returns ``{relations, stats}``.
    """
    index = _symbol_index(per_file)
    vocabulary = _vocabulary(per_file)
    relations: list[Doc] = []
    path_links = symbol_links = ambiguous = capped = vocabulary_skipped = 0

    for doc in docs:
        doc_path = str(doc["path"])
        source = file_entity_name(doc_path)
        seen: set[str] = set()
        emitted = 0
        for line_number, line in enumerate(str(doc.get("content", "")).splitlines()):
            symbols, line_ambiguous, line_vocabulary = _symbol_mentions(line, index, vocabulary)
            ambiguous += line_ambiguous
            vocabulary_skipped += line_vocabulary
            paths = _path_mentions(line, doc_path, file_paths)
            hits = [(text, target, True) for text, target in paths]
            hits.extend((text, target, False) for text, target in symbols)
            for text, target, proven in hits:
                if target == source or target in seen:
                    continue
                seen.add(target)
                if emitted >= max_links:
                    capped += 1
                    continue
                emitted += 1
                if proven:
                    path_links += 1
                else:
                    symbol_links += 1
                relations.append(
                    _relation(source, target, text, doc_path, line_number, proven=proven)
                )

    return {
        "relations": relations,
        "stats": {
            "docPathReferences": path_links,
            "docSymbolReferences": symbol_links,
            "ambiguousDocMentionsSkipped": ambiguous,
            "vocabularyDocMentionsSkipped": vocabulary_skipped,
            "docReferencesCapped": capped,
        },
    }
