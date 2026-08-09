"""The write primitive: Cypher statements plus their event appends, as one unit.

Every mutation in the Loom — entity, relation, document chunk — is a
``GRAPH.QUERY`` and an ``XADD`` against the same server, sent as one Redis
MULTI/EXEC transaction so the projection and the log move together. This module
is that mechanism, factored out of the graph store so the chunk store gets the
identical guarantee (and the identical compensation) instead of a second,
subtly different copy.

The guarantee, precisely:

- Nothing reaches the server until ``EXEC``. Any failure while the transaction
  is being built — serializing a payload, an injected fault, the process dying
  — leaves the graph and the log both untouched. This is the hole a
  write-then-append order has: a crash after the query loses the event forever.
- Redis runs the queued commands with no other client interleaved, and the
  client cannot die between them.
- A command Redis rejects while *queueing* it (unknown command, bad arity, out
  of memory) aborts the whole transaction: ``EXEC`` runs nothing and
  ``execute`` raises. Both halves stay untouched.
- Redis has no rollback, so a command rejected at *run* time still leaves its
  neighbour applied — and exactly one of the two halves can be the casualty,
  each compensated in the only direction its semantics allow:

  * the Cypher failed (a malformed query — a bug, since every domain
    precondition is checked in Python first): the query applied nothing, so the
    ``XADD`` that ran beside it is unearned and is deleted by id before the
    error propagates. No trace in either place.
  * the ``XADD`` failed: the mutation is applied and there is no inverse
    statement to take it back with, so the event it earned is true and is
    appended again outside the transaction (see ``repair_log``), along with
    every event of the same mutation queued behind it, so the batch keeps its
    order. The caller sees success. If that retry fails too the log gap is
    permanent, and the caller gets an OPERATION_ERROR naming the events that
    are actually missing instead of silence.

The one thing this does *not* promise: a repaired event is appended after the
``EXEC``, so a concurrent writer can slip an entry in front of it. Events stay
ordered within a mutation — the repair re-appends the whole tail behind the
failure rather than only the failed appends — but a repaired one may sit later
in the stream than a mutation that really followed it. The single exception is
a repair that itself fails partway: the events it never reached keep their
original places, so a mutation whose log is intact may still be out of order.

MULTI is *not* a rollback boundary: passing several ``steps`` means that if
statement *k* fails, the statements before it have already applied. A caller
that needs more than one statement owes that difference back — by checking its
preconditions first and by explicitly undoing what did land (see
``FalkorGraphStore.create_relations``).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from falkordb.query_result import QueryResult
from redis import Redis

from theloom.errors import OperationError
from theloom.store import receipts
from theloom.store.events import EventLog

Step = tuple[str, dict[str, Any]]
EventSpec = tuple[str, dict[str, Any]]


def is_error(response: Any) -> bool:
    """Redis returns per-command errors as exception *values* inside a
    transaction's reply list (``raise_on_error=False``)."""
    return isinstance(response, Exception)


def stream_id(response: Any) -> str:
    """An XADD reply, decoded (the connection may or may not decode for us)."""
    return response if isinstance(response, str) else str(response.decode())


def commit_steps(
    redis: Redis,
    graph: Any,
    events_log: EventLog,
    steps: Sequence[Step],
    events: Sequence[EventSpec],
    *,
    register: tuple[str, str] | None = None,
) -> tuple[list[QueryResult], list[str]]:
    """Run the Cypher steps and append their events as one MULTI/EXEC unit.

    ``register``, when given, is ``(registry_key, member)``: a ``SADD`` queued
    into the same transaction as the Cypher and the event append, so a graph's
    first write and its membership in the multigraph registry land together —
    the fix for the registry gap where a graph created implicitly via a bare
    ``graph`` param on any mutating command was invisible to
    ``list-graphs``/``delete-graph`` (see ``GraphSpace.__init__``, the only
    caller that populates it; ``theloom.store.bridges.BridgeRegistry`` and the
    chunk store's reserved graph never do). Idempotent and side-effect-free
    for a graph already registered, so this runs on every write rather than
    only the first — there is no cheaper way to know "first" without an extra
    round trip. Its reply is not inspected: a ``SADD`` against a key this
    module owns exclusively as a set does not fail in ways that should change
    the mutation's outcome.

    Returns ``(query results, appended event ids)``; the ids let a caller that
    only learns the mutation was wrong *after* ``EXEC`` discard the events it
    no longer earns.
    """
    with redis.pipeline(transaction=True) as pipe:
        for cypher, params in steps:
            # The client's own parameter encoder, then the same command
            # Graph.query() would have issued — buffered instead of sent.
            pipe.execute_command(  # type: ignore[no-untyped-call]
                "GRAPH.QUERY",
                graph.name,
                graph._build_params_header(params) + cypher,
                "--compact",
            )
        for event_type, payload in events:
            events_log.queue(pipe, event_type, payload)
        if register is not None:
            pipe.sadd(*register)
        responses: list[Any] = pipe.execute(raise_on_error=False)

    query_responses = responses[: len(steps)]
    event_responses = responses[len(steps) : len(steps) + len(events)]
    event_ids = [stream_id(entry) for entry in event_responses if not is_error(entry)]
    query_failure = next((r for r in query_responses if is_error(r)), None)
    if query_failure is not None:
        # The mutation did not happen, so its events are not earned.
        events_log.discard(event_ids)
        raise query_failure
    failed_at = next((i for i, response in enumerate(event_responses) if is_error(response)), None)
    if failed_at is None:
        # This is the choke point every FalkorDB mutation passes through
        # (CLAUDE.md invariant 1) — recording here, rather than in each of the
        # dozens of store methods that call commit_steps, is what lets
        # write-receipts (theloom.store.receipts) attach to a command's
        # response without those methods' return types changing.
        receipts.record(event_ids)
        return [QueryResult(graph, response) for response in query_responses], event_ids
    # The mutation did happen and cannot be undone, so the events it earned are
    # true. Everything from the first failure on is re-appended in order — the
    # ones that errored because they are absent, the ones that succeeded
    # because a repair appended behind them would otherwise reverse the batch.
    suffix = [
        (event, None if is_error(response) else stream_id(response))
        for event, response in zip(events[failed_at:], event_responses[failed_at:], strict=True)
    ]
    kept = [stream_id(response) for response in event_responses[:failed_at]]
    final_ids = kept + repair_log(events_log, suffix)
    receipts.record(final_ids)
    return (
        [QueryResult(graph, response) for response in query_responses],
        final_ids,
    )


def repair_log(events_log: EventLog, suffix: Sequence[tuple[EventSpec, str | None]]) -> list[str]:
    """Re-append the tail of a committed mutation's events, in order; return their ids.

    ``suffix`` is every event from the first failed append onwards, paired with
    the stream id it already occupies (``None`` when its XADD errored). Only
    reachable for a *runtime* rejection of the append: a queue-time rejection
    (unknown command, bad arity, out of memory) makes Redis abort the whole
    transaction, so neither half runs and ``execute`` raises before any of
    this. A runtime one — the stream key holding a non-stream value, a
    server-side refusal of the write — comes back as an error value beside a
    ``GRAPH.QUERY`` that has already applied, and Redis has no rollback to take
    it back with.

    So compensation runs towards the log, not away from it, and it moves the
    events that *did* land rather than appending around them: an event is
    re-appended first and its earlier copy deleted only once the replacement
    exists, so no window has the event missing from the stream.

    If the retry fails partway, the ids that did land are still returned to the
    caller (they are as real as any other event it wrote) and only the events
    genuinely absent are named in a typed OPERATION_ERROR, with the raw Redis
    failure chained — an operator told "three events are missing" when one is
    present would double-append it. When the halt leaves nothing missing (the
    remaining events are all present, merely behind the repaired ones) there is
    no gap to name: the mutation stands with its events intact and its order
    degraded, which is strictly the weaker of the two losses.
    """
    result = events_log.repair([event for event, _ in suffix])
    done = len(result.appended)
    # Replacements are in the stream now; drop the copies they superseded.
    events_log.discard([entry_id for _, entry_id in suffix[:done] if entry_id is not None])
    stranded = [entry_id for _, entry_id in suffix[done:] if entry_id is not None]
    missing = [event for event, entry_id in suffix[done:] if entry_id is None]
    if result.error is not None and missing:
        types = ", ".join(event_type for event_type, _ in missing)
        raise OperationError(
            "Mutation committed but the event log could not be repaired: "
            f"{len(missing)} event(s) ({types}) are missing from the log and "
            f"cannot be re-appended: {result.error}"
        ) from result.error
    return result.appended + stranded
