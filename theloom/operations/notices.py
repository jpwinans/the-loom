"""The shared notice/``applied`` response convention (TL-486; foundation for
the sibling contract tickets TL-481–485 and TL-472).

The Agent Contract's complaint (TL-477) is that a command response can be
success-shaped for something that did not actually happen: loop detection
that never persisted, a propagation that only *would* have changed
confidences, a document ingest that silently dropped a parameter. This module
is the *one* reusable mechanism any command response uses to say that
plainly, so sibling tickets adopt one shape rather than each inventing its
own ad hoc "warning" field:

- ``notice(code, message, hint=None)`` builds one structured notice — never a
  bare prose string, so a caller can branch on ``code`` instead of grepping
  text. Wire shape: ``{"code": str, "message": str, "hint": str | None}``
  (``hint`` omitted when absent, not sent as a literal ``null``).

- ``with_notices(result, notices=None, *, applied=None)`` folds a command's
  own wire-ready result dict together with two additive, independent keys:

  - ``"notices"``: a list of ``notice()`` docs — added only when non-empty,
    so a clean result carries no ``"notices": []`` noise.
  - ``"applied"``: a bool marking whether a mutating command actually wrote
    anything (``False`` for a dry run / simulation, ``True`` for a real
    write) — added only when explicitly given (not ``None``), so a command
    with no dry-run concept never gains the key.

  Both keys are additive on top of whatever the command already returns:
  adopting the convention never changes a command's existing response shape
  for callers that don't look for these keys, and never requires restating
  fields the command already sets.

Any operations module can import and call these directly from its own
handler — nothing here touches ``CommandInput``, the registry, or the CLI
layer, so adopting the convention is a pure addition to a handler's return
value. (This ticket only builds the mechanism; wiring it into detect-loops,
propagate-credit, run-inference, etc. is the sibling tickets' job.)

``list_envelope(items, notices=None)`` is the structural completion of the
same idea: one uniform ``{"items": [...], "count": len(items)[, "notices"]}``
shape for every list-returning command, replacing the three attachment
strategies that grew up independently (a bare top-level array, a bare array
with notices smeared onto every element because there was nowhere else to put
one, and an ad hoc ``{"count", "<listName>"}`` pair whose list key differed
per command). ``count`` is always ``len(items)`` — the number of rows the
response actually carries, never a separate "total available before a filter"
figure (a command that truncates says so via a notice, not a second number
with an ambiguous relationship to the first).

``NOTICE_CATALOG`` (desire 3, the queryable notice catalog behind the
``notices-catalog`` command) is the single source of truth for every notice
*code* this build of The Loom can emit: a ``code -> meaning`` mapping that
``notice()`` itself enforces — building a notice with a code that isn't a
catalog key raises ``ValueError`` rather than shipping an uncataloged code.
That refusal is what makes the catalog self-maintaining rather than a list
someone has to remember to update: a new notice code cannot go live without
a meaning here the same commit it lands in an emitting call site, and
``theloom.cli.notices_catalog`` separately walks the registry to discover
*which* commands can actually surface each code, so neither half is
hand-listed. See ``theloom/cli/notices_catalog.py`` and
``tests/test_notices_catalog.py`` for the generation and the tests that
would fail if a code were documented but unreachable, or emitted but
undocumented.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

Doc = dict[str, Any]


NOTICE_CATALOG: dict[str, str] = {
    "ALREADY_REAPED": (
        "The ref named in the request (a session workspace, or a belief "
        "world) was already reaped; there was nothing left to delete, so "
        "nothing further happened."
    ),
    "AUTO_SCOPED": (
        "A required scoping parameter was omitted, so the command auto-selected "
        "a scope using its own retrieval (e.g. hybrid search) instead of "
        "grading or acting against the whole graph."
    ),
    "CONTESTED_ON_MERGE": (
        "merge-world found one or more entities or relations changed in "
        "both the source and target world since the fork. These conflicts "
        "were not auto-resolved: the merge applied only the uncontested "
        "set, and the contested items are listed in the response's "
        "'contested' field for a caller to resolve manually (retry with "
        "strategy: 'select')."
    ),
    "DRY_RUN": (
        "The command ran as a simulation only: it computed what it would do "
        "but did not persist any changes to the graph."
    ),
    "EMPTY_TRAVERSAL": (
        "The traversal found zero edges to follow from the source entity in "
        "the searched direction, so no results could be produced."
    ),
    "NONE_PERSISTED": (
        "No entities of the kind this command lists have been persisted in "
        "the graph yet. This does not mean none exist -- only that the "
        "generating command has not been run with persistence, or has not "
        "found any yet."
    ),
    "NOT_PERSISTED": (
        "The command computed results but did not write them to the graph; a "
        "later read (e.g. a list command over the same entity kind) will not "
        "see them until the command is re-run with persistence requested."
    ),
    "PARAMETER_IGNORED": (
        "A supplied parameter was accepted for schema compatibility but was "
        "not applied -- the response reflects the command's real behavior, "
        "not the ignored parameter's implication."
    ),
    "TRUNCATED": (
        "The result set was larger than the page returned in this response; "
        "only a prefix is included."
    ),
    "WORLD_PROJECTION_PARTIAL": (
        "This command ran against a non-main belief world, but the data it "
        "operates on (embeddings, or other state written outside the event "
        "log) is not forked -- it reflects only what was written inside "
        "this world, not what the world inherited from its parent. The "
        "command computed a real answer over that partial data rather than "
        "silently pretending to see the whole projection."
    ),
}


def list_envelope(items: Sequence[Any], notices: list[Doc] | None = None) -> Doc:
    """The uniform list-command response: ``{"items", "count"[, "notices"]}``.

    ``items`` is usually a list of docs but is not required to be one — a
    handful of commands (``find-related-graphs``) list bare strings."""
    return with_notices({"items": list(items), "count": len(items)}, notices)


def notice(code: str, message: str, hint: str | None = None) -> Doc:
    """One structured notice: ``{"code", "message", "hint"}`` — ``hint`` is
    omitted (not set to ``null``) when there is nothing actionable to add.

    ``code`` must be a key in ``NOTICE_CATALOG``: this is the enforcement
    half of the queryable notice catalog (desire 3) — a code cannot ship
    without a cataloged meaning, so ``loom notices-catalog`` can never miss
    one. Raises ``ValueError`` (an internal-invariant bug, not a caller
    input error) rather than silently emitting an uncataloged code.
    """
    if code not in NOTICE_CATALOG:
        raise ValueError(
            f"Notice code {code!r} is not registered in NOTICE_CATALOG "
            "(theloom/operations/notices.py) -- add its meaning there before "
            "emitting it, so `notices-catalog` can enumerate it."
        )
    doc: Doc = {"code": code, "message": message}
    if hint is not None:
        doc["hint"] = hint
    return doc


def with_notices(
    result: Doc,
    notices: list[Doc] | None = None,
    *,
    applied: bool | None = None,
) -> Doc:
    """Attach the shared envelope keys to a command's result dict, additively.

    Returns a shallow copy of ``result`` — the caller's dict is never
    mutated. ``notices`` is only added when non-empty; ``applied`` is only
    added when explicitly given (not ``None``).
    """
    out = dict(result)
    if notices:
        out["notices"] = notices
    if applied is not None:
        out["applied"] = applied
    return out
