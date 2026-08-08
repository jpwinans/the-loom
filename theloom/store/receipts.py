"""Write-receipts (desire 1): every mutating command's response carries the
event-log ids it appended, without changing a single store method's return
contract.

The problem this solves: ``GraphSpace._commit``/``_commit_steps`` (via
``theloom.store.commit.commit_steps``) already knows the exact event ids a
mutation earned — but every store method (``create_entity``, ``update_entity``,
``create_relation``, ...) discards them, returning only the domain object. Two
hundred call sites across ``theloom/operations`` and ``theloom/composites``
call these store methods expecting an ``Entity``/``Relation``/etc back;
changing every one of those signatures to thread ``(value, event_ids)`` tuples
through would be the "second write path" CLAUDE.md forbids in spirit even
though it touches no Cypher — it would fork how a caller gets at a mutation's
result depending on whether it wants the receipt.

Instead this module is a per-command *side channel*, active only for the
duration of one CLI dispatch (``theloom.cli.registry.run_handler`` opens it):

- ``collecting(command)`` is a context manager that starts an empty id sink
  and remembers the causing command's name, for the scope of one handler call
  — including every nested store call the handler makes (a composite that
  calls ``create_relation`` from inside ``update_entity``'s supersedes logic
  earns both mutations' ids under the one outer command name).
- ``record(event_ids)`` is called once, by ``commit_steps``, right after a
  commit (or its repair) lands — the single choke point every FalkorDB
  mutation goes through (CLAUDE.md invariant 1). Outside a ``collecting()``
  scope (a test calling a store method directly, a REPL) it is a no-op: nothing
  pays for bookkeeping nobody asked for.
- ``current_command()`` is read by ``theloom.store.events.EventLog`` itself
  when it builds an XADD, to stamp the event with ``causedBy`` — the same
  contextvar, so the ids collected for the response and the ``causedBy`` on
  the events those ids name are always the same command, without a second
  parameter threaded through every event-emitting call site in
  ``falkor.py``/``chunkstore.py``/``bridges.py``.
- ``attach(result, event_ids)`` folds the collected ids onto a command's
  response as ``eventIds`` — additive, mirroring
  ``theloom.operations.notices.with_notices``: nothing is added when nothing
  was collected (a read-only command's response is untouched), a dict response
  gains the key, and the handful of legacy plain-string success messages
  (``delete-relation``) are promoted to ``{"message": ..., "eventIds": [...]}``
  so the receipt has somewhere to live.

A ``ContextVar`` rather than an instance attribute on the store: ``MultiGraph.
get_store()`` builds a fresh ``FalkorGraphStore`` on every call (by design —
see its docstring), so a handler that fetches the store twice (``update_entity``
calling ``create_relation``, which calls ``get_store`` again) would lose any
per-instance accumulator between the two. A contextvar scoped to the whole
command dispatch has no such seam.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_ids: ContextVar[list[str] | None] = ContextVar("loom_receipt_event_ids", default=None)
_command: ContextVar[str | None] = ContextVar("loom_receipt_command", default=None)


@contextmanager
def collecting(command: str) -> Iterator[list[str]]:
    """Open a receipt-collection scope for one command dispatch.

    Every event id ``record()``ed anywhere during the block — including
    inside nested store/handler calls — lands in the returned list, in
    append order. Reentrant: a nested ``collecting()`` (none of today's
    handlers open one, but a future composite-of-composites might) gets its
    own scope and restores the outer one on exit, rather than sharing or
    clobbering it.
    """
    ids_token = _ids.set([])
    command_token = _command.set(command)
    try:
        yield _ids.get()  # type: ignore[return-value]  # just set, never None
    finally:
        _ids.reset(ids_token)
        _command.reset(command_token)


def record(event_ids: Sequence[str]) -> None:
    """Append newly-appended event ids to the active collector, if any."""
    sink = _ids.get()
    if sink is not None and event_ids:
        sink.extend(event_ids)


def forget(event_ids: Sequence[str]) -> None:
    """Remove ids from the active collector, if any — the compensating half
    of ``record``, for ids recorded by a commit that a caller then decided
    were not earned after all (a batch mutation whose reply disagreed with
    what it asked for, a duplicate bridge) and discarded via
    ``EventLog.discard``. A no-op for an id never recorded, so calling it from
    ``EventLog.discard`` unconditionally — its one call site — is always safe,
    whether or not a receipt was ever collected for that id."""
    sink = _ids.get()
    if sink is not None and event_ids:
        wanted = set(event_ids)
        sink[:] = [entry_id for entry_id in sink if entry_id not in wanted]


def current_command() -> str | None:
    """The command name the active ``collecting()`` scope was opened with, or
    ``None`` outside one — read by ``EventLog`` to stamp ``causedBy``."""
    return _command.get()


def attach(result: Any, event_ids: Sequence[str]) -> Any:
    """Fold collected event ids onto a command's response as ``eventIds``.

    Additive and shape-preserving wherever it can be: a dict response gains
    the key; nothing is attached when nothing was collected (a pure read, or
    a dry run that never reached a commit), so a read-only command's response
    is byte-identical to before this module existed. The few legacy
    plain-string success messages are promoted to an object so the receipt
    has a key to live under — a deliberate, documented output-shape change,
    not a silent one.
    """
    if not event_ids:
        return result
    ids = list(event_ids)
    if isinstance(result, dict):
        out = dict(result)
        out["eventIds"] = ids
        return out
    if isinstance(result, str):
        return {"message": result, "eventIds": ids}
    return result
