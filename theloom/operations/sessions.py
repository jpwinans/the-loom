"""Session workspace commands (desire 2): a namespaced, TTL-bearing, one-call
-reap scratch boundary layered over the multigraph registry.

Thin CLI wiring only, matching every other operations module: validate
input, call the store, fold on the TL-477 contract keys
(``notices``/``applied``, the list envelope) the store layer never sets
itself. The actual mechanism lives beneath this —
``theloom.store.refs.RefRegistry`` is the generic, reusable ref/TTL
bookkeeping, and ``theloom.store.multigraph.MultiGraph.begin_session`` /
``end_session`` / ``list_sessions`` are the session-specific composition
(namespace minting, graph-prefix reap) — so a future consumer (branchable
belief worlds, desire 12 / Part 5) reuses the registry without touching this
module or anything CLI-shaped at all.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from theloom.operations.common import CommandInput
from theloom.operations.notices import list_envelope, notice, with_notices
from theloom.store.multigraph import MultiGraph


class BeginSessionInput(CommandInput):
    name: str | None = Field(
        default=None,
        description="Optional human label for the session; purely descriptive, shown "
        "back by list-sessions and end-session but never used for addressing "
        "(sessionId is).",
    )
    ttl_seconds: int | None = Field(
        default=None,
        alias="ttlSeconds",
        description="How long the session is expected to live, in seconds. Informational: "
        "past this point the session shows expired=true in list-sessions, but "
        "nothing reaps it automatically — end-session is always the one call "
        "that actually deletes its graphs.",
    )


class EndSessionInput(CommandInput):
    session_id: str = Field(
        alias="sessionId",
        description="The sessionId returned by begin-session.",
    )


class EmptyInput(CommandInput):
    pass


def begin_session(params: BeginSessionInput, multi: MultiGraph) -> dict[str, Any]:
    """Start a session workspace: mint a unique namespace and TTL, register it.

    Every graph the caller subsequently creates with a name under the
    returned ``namespace`` (via create-graph, or an ad-hoc bare ``graph``
    param on any mutating command) is automatically tracked for one-call
    reap by ``end-session`` — no separate "join this session" step, and no
    change to how graphs are created.
    """
    doc = multi.begin_session(params.name, params.ttl_seconds)
    return with_notices(doc, applied=True)


def end_session(params: EndSessionInput, multi: MultiGraph) -> dict[str, Any]:
    """Reap a session: delete every graph currently registered under its
    namespace, in one call, and mark the session reaped.

    Reaping an already-reaped session is a truthful no-op: ``applied`` is
    ``False`` and an ``ALREADY_REAPED`` notice explains why, rather than
    claiming a second deletion that did not happen.
    """
    doc = multi.end_session(params.session_id)
    already_reaped = doc.pop("alreadyReaped")
    notices = (
        [
            notice(
                "ALREADY_REAPED",
                f"Session '{params.session_id}' was already reaped; there were no "
                "graphs left to delete.",
            )
        ]
        if already_reaped
        else None
    )
    return with_notices(doc, notices=notices, applied=not already_reaped)


def list_sessions(_: EmptyInput, multi: MultiGraph) -> dict[str, Any]:
    """Every session workspace, oldest first, with its namespace, TTL, and
    current member graphs."""
    return list_envelope(multi.list_sessions())
