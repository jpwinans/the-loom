"""The codebase-graph encoding: how symbols are written to and read from the
graph as entity names, observation strings, and evidence strings.

Every other extraction/consumption module used to build and re-parse these
strings independently — a writer's f-string here, a reader's private prefix
constant and regex there, kept in sync only by convention. This module is the
one place that convention is written down: build a string here, parse it back
here, and every other module calls in rather than keeping its own copy.

**Compatibility is absolute.** These formats are already written into
existing graphs. Nothing here may change what a builder emits — this module
centralizes the encoding, it does not redesign it. Parsers keep accepting
exactly what today's writers produce, including the case-insensitive prefix
matching the readers historically tolerated (writers emit title case; some
reader call sites had drifted to lowercase prefixes, and continue to work).

**Line-number convention**, documented once, here: every ``line`` (or
``start_line``/``end_line``) parameter a builder in this module takes is
**0-based** — the convention tree-sitter uses and the extractor carries
throughout its internal pipeline. Every builder *renders* that line **1-based**
in the stored string, because that is what a human reader (or an editor's
"go to line") expects. Parsers undo exactly that: they hand back 0-based ints,
so a round trip through a builder and its parser is the identity.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

# =============================================================================
# File entity name
# =============================================================================

FILE_ENTITY_PREFIX = "file:"


def file_entity_name(path: str) -> str:
    """The entity name the extractor gives a file."""
    return f"{FILE_ENTITY_PREFIX}{path}"


def is_file_entity_name(name: str) -> bool:
    """Whether ``name`` is the entity name of a file (as opposed to a symbol,
    or an external ``pkg:`` package)."""
    return name.startswith(FILE_ENTITY_PREFIX)


def parse_file_entity_name(name: str) -> str | None:
    """The file path a file entity's name encodes, or ``None`` if ``name``
    does not name a file entity."""
    if not is_file_entity_name(name):
        return None
    return name[len(FILE_ENTITY_PREFIX) :]


# =============================================================================
# Observation prefixes
# =============================================================================
#
# A symbol/file entity carries free-text ``observations``; a fixed subset of
# them are ``"<Prefix>: <value>"`` lines this module both writes and reads.
# Readers historically matched these prefixes case-insensitively (a
# compensating hack for reader call sites that had drifted to lowercase) while
# writers always emit the title-case form below — parsing keeps tolerating
# both, because that is what is already sitting in existing graphs.


def _observation_prefixed(prefix: str, value: str) -> str:
    return f"{prefix}: {value}"


def _parse_observation_prefix(observations: Iterable[Any], prefix: str) -> str | None:
    """The value after the first observation matching ``prefix`` (case
    insensitive), or ``None`` if none matches."""
    lowered_prefix = f"{prefix.lower()}:"
    for observation in observations:
        text = str(observation)
        if text.lower().startswith(lowered_prefix):
            return text[len(lowered_prefix) :].strip()
    return None


FILE_PATH_PREFIX = "File path"


def file_path_observation(path: str) -> str:
    """The observation an entity extracted from ``path`` carries, naming its
    source file."""
    return _observation_prefixed(FILE_PATH_PREFIX, path)


def parse_file_path(observations: Iterable[Any]) -> str | None:
    """The source file an entity's observations say it came from, or
    ``None`` if none of them say."""
    return _parse_observation_prefix(observations, FILE_PATH_PREFIX)


LINE_RANGE_PREFIX = "Line range"


def line_range_observation(start_line: int, end_line: int) -> str:
    """The observation naming the (inclusive) source span a symbol was
    extracted from. ``start_line``/``end_line`` are 0-based; the stored text
    is 1-based (see the module docstring)."""
    return _observation_prefixed(LINE_RANGE_PREFIX, f"{start_line + 1}-{end_line + 1}")


def parse_line_range(observations: Iterable[Any]) -> tuple[int, int] | None:
    """The ``(start_line, end_line)`` a ``line_range_observation`` encoded,
    0-based — the inverse of ``line_range_observation`` — or ``None`` if no
    observation carries one."""
    text = _parse_observation_prefix(observations, LINE_RANGE_PREFIX)
    if text is None:
        return None
    start_str, sep, end_str = text.partition("-")
    if not sep or not start_str.strip().isdigit() or not end_str.strip().isdigit():
        return None
    return (int(start_str) - 1, int(end_str) - 1)


SYMBOL_KIND_PREFIX = "Symbol kind"


def symbol_kind_observation(kind: str) -> str:
    """The observation naming the extracted kind of a symbol (``function``,
    ``class``, ...) or of a file (``File``) or external package
    (``ExternalPackage``)."""
    return _observation_prefixed(SYMBOL_KIND_PREFIX, kind)


def parse_symbol_kind(observations: Iterable[Any]) -> str | None:
    """The kind a ``symbol_kind_observation`` encoded, or ``None`` if none of
    ``observations`` carries one."""
    return _parse_observation_prefix(observations, SYMBOL_KIND_PREFIX)


# =============================================================================
# Call-site evidence
# =============================================================================

#: ``<caller> calls <callee> at <file>:<line>`` — the fixed, parseable
#: evidence form for every call edge, whichever pass emitted it.
#: ``_CALL_SITE_RE`` finds just the anchored site, from anywhere in a string
#: (a reader only needs the site); ``_CALL_EVIDENCE_RE`` additionally requires
#: the fixed ``<caller> calls <callee>`` head, for full decomposition.
_CALL_SITE_RE = re.compile(r"\bat\s+(?P<path>\S+):(?P<line>\d+)\s*$")
_CALL_EVIDENCE_RE = re.compile(r"^(?P<head>.*) at (?P<path>\S+):(?P<line>\d+)\s*$")


def call_evidence(caller: str, callee: str, path: str, line: int) -> str:
    """Evidence for a call edge, anchored at the **call site**.

    ``line`` is 0-based (the tree-sitter convention the extractor carries)
    and renders 1-based. The site is where the call is written, not where the
    callee is defined — following an edge means reading the caller. How the
    target was established is carried by ``confidence.basis``, not by prose,
    so the format stays fixed.
    """
    return f"{caller} calls {callee} at {path}:{line + 1}"


def parse_call_site_text(evidence: str | None) -> str | None:
    """The raw ``<file>:<line>`` (1-based) substring a call edge's evidence
    is anchored at — the form readers display verbatim — or ``None`` if
    ``evidence`` is not anchored."""
    if not evidence:
        return None
    match = _CALL_SITE_RE.search(evidence)
    if match is None:
        return None
    return f"{match.group('path')}:{match.group('line')}"


def parse_call_site(evidence: str | None) -> tuple[str, int] | None:
    """The ``(path, line)`` — ``line`` 0-based — a call edge's evidence is
    anchored at, or ``None`` if it is not anchored."""
    if not evidence:
        return None
    match = _CALL_SITE_RE.search(evidence)
    if match is None:
        return None
    return (match.group("path"), int(match.group("line")) - 1)


def parse_call_evidence(evidence: str) -> tuple[str, str, str, int] | None:
    """The ``(caller, callee, path, line)`` — ``line`` 0-based — a
    ``call_evidence`` string encoded; the inverse of ``call_evidence``, or
    ``None`` if ``evidence`` is not in that form."""
    match = _CALL_EVIDENCE_RE.match(evidence)
    if match is None:
        return None
    head, path, line = match.group("head"), match.group("path"), match.group("line")
    caller, sep, callee = head.partition(" calls ")
    if not sep:
        return None
    return (caller, callee, path, int(line) - 1)
