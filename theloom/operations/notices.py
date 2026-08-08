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
"""

from __future__ import annotations

from typing import Any

Doc = dict[str, Any]


def notice(code: str, message: str, hint: str | None = None) -> Doc:
    """One structured notice: ``{"code", "message", "hint"}`` — ``hint`` is
    omitted (not set to ``null``) when there is nothing actionable to add."""
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
