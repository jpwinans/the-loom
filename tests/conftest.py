"""Shared fixtures: a live FalkorDB (docker compose service) with per-test
namespacing so tests never touch real data and always clean up after themselves.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from falkordb import FalkorDB
from redis import Redis

from theloom.config import LoomConfig, credential_kwargs, load_config
from theloom.store.memory import InMemoryGraphStore
from theloom.store.multigraph import MultiGraph


@pytest.fixture(scope="session")
def config() -> LoomConfig:
    return load_config(env={})


@pytest.fixture(scope="session")
def db(config: LoomConfig) -> FalkorDB:
    return FalkorDB(host=config.host, port=config.port, **credential_kwargs(config))


@pytest.fixture(scope="session")
def redis_client(db: FalkorDB) -> Redis:
    connection: Redis = db.connection
    return connection


@pytest.fixture()
def namespace(db: FalkorDB, redis_client: Redis) -> Iterator[str]:
    """A unique key/graph prefix per test, torn down afterwards."""
    prefix = f"loomtest-{uuid.uuid4().hex[:8]}"
    yield prefix
    for graph_name in db.list_graphs():
        if graph_name.startswith(prefix):
            db.select_graph(graph_name).delete()
    leftovers = [key for key in redis_client.scan_iter(f"{prefix}*")]
    if leftovers:
        redis_client.delete(*leftovers)


@pytest.fixture()
def multi(db: FalkorDB, redis_client: Redis, namespace: str) -> MultiGraph:
    return MultiGraph(db, redis_client, default_graph="default", key_prefix=namespace)


@pytest.fixture()
def memory_store() -> InMemoryGraphStore:
    """An empty in-memory adapter for ``GraphReadPort`` — no docker involved.

    Reach for this (or ``tests.fakes.seeded_memory_store``) whenever the code
    under test only reads the graph."""
    return InMemoryGraphStore()


#: Fixed, well-known key: every process pointed at this FalkorDB server —
#: including a concurrent, unrelated ``pytest`` invocation from another
#: worktree — takes the *same* lock before touching RESULTSET_SIZE, so it
#: serializes across processes, not just within one.
_RESULTSET_LOCK_KEY = "loomtest:resultset-size-lock"
_RESULTSET_CAP = 40


@pytest.fixture()
def small_resultset_cap(db: FalkorDB, redis_client: Redis) -> Iterator[int]:
    """Cap the server's RESULTSET_SIZE far below the seeded row counts so
    truncation bugs surface with small fixtures; restores the prior value.
    FalkorDB silently drops rows past the cap (default 10000), so any read
    that isn't paged or aggregated returns wrong answers on large graphs.

    RESULTSET_SIZE is a server-global (``GRAPH.CONFIG``, not a per-connection
    or per-session knob — FalkorDB exposes no such scoping), so two tests
    running this fixture concurrently used to race on get/set/restore: both
    read the original value before either restored it, so the second
    restore clobbered the first's already-lowered value back to 40 instead
    of the true original — permanently stranding the server at the test cap.
    That is exactly what happened in practice (2026-08-08): a spurious
    "flaky" failure that was really shared mutable server state.

    The fix is to make the whole get/set/yield/restore span one critical
    section, guarded by a cross-process Redis lock (``redis.Redis.lock`` —
    ``SET NX PX`` under the hood, safe release via a stored token, no new
    dependency) rather than an in-process one: the race is between separate
    ``pytest`` invocations against the same live server, not between threads
    in one process, so a ``threading.Lock`` would not have helped. A run
    that cannot acquire the lock within ``blocking_timeout`` skips instead of
    racing anyway — the server is never left in an inconsistent state either
    way: it is on the mend under someone else's lock, or restored to
    whatever this fixture found before it, in both branches.
    """
    lock = redis_client.lock(_RESULTSET_LOCK_KEY, timeout=60, blocking_timeout=30)
    if not lock.acquire(blocking=True):
        pytest.skip(
            "Could not acquire the RESULTSET_SIZE lock within 30s — another "
            "concurrent test run holds the server-global config. Skipping "
            "rather than racing it (see small_resultset_cap's docstring)."
        )
    try:
        original = db.config_get("RESULTSET_SIZE")
        db.config_set("RESULTSET_SIZE", _RESULTSET_CAP)
        try:
            yield _RESULTSET_CAP
        finally:
            db.config_set("RESULTSET_SIZE", original)
    finally:
        lock.release()
