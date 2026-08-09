"""The world-resolution side channel (branchable belief worlds, desire 12):
one contextvar, exactly like ``theloom.store.receipts``, so ``world``
threads through every command the same way ``graph`` already does — one
resolution path, no per-command reimplementation.

The problem this solves: every command's input model already carries a
``graph`` field, and its handler resolves it by calling
``multi.get_store(params.graph)`` — the single choke point every store
instance is built from (see ``theloom.store.multigraph.MultiGraph.get_store``).
``world`` needed the same property without touching the ~140 call sites that
already call ``get_store``: ``theloom.operations.common.CommandInput`` grows
one new optional field (every command gains it for free, structurally), and
``theloom.cli.registry.run_handler`` opens an ``active(params.world)`` scope
around the handler dispatch — mirroring ``receipts.collecting`` exactly, down
to being a no-op outside the scope. ``get_store`` then reads ``current()``
itself, so a handler that has never heard of worlds still gets world-scoped
reads/writes for free the moment its ``graph`` param resolves through
``get_store`` — which every one of them already does.

Explicit beats ambient at the one seam that matters: ``get_store(name,
world=...)`` accepts an explicit override (used by
``theloom.store.worlds.resolve_layers`` to build a specific ancestor's
*plain* store regardless of whatever world happens to be ambient), and only
falls back to this contextvar when the caller passes nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_world: ContextVar[str | None] = ContextVar("loom_world_id", default=None)

#: The name every command's default, unforked world resolves to — never a
#: real ref id, so it can never collide with one (``theloom.store.refs``
#: mints hex-suffixed ids).
MAIN = "main"


@contextmanager
def active(world: str | None) -> Iterator[None]:
    """Open a world-resolution scope for one command dispatch.

    ``world is None`` (the command's ``world`` field omitted, or explicitly
    ``"main"``) clears the ambient world for the block — the common case,
    and the one every pre-existing command exercises, so its behavior is
    untouched. Reentrant, like ``receipts.collecting``: a nested scope
    restores the outer one on exit.
    """
    token = _world.set(None if world in (None, MAIN) else world)
    try:
        yield
    finally:
        _world.reset(token)


def current() -> str | None:
    """The ambient world id, or ``None`` when the active scope is ``main``
    (or no scope is open at all — a direct store call in a test, a REPL)."""
    return _world.get()
