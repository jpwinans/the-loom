"""Shared fixtures: a live FalkorDB (docker compose service) with per-test
namespacing so tests never touch real data and always clean up after themselves.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from falkordb import FalkorDB
from redis import Redis

from theloom.config import LoomConfig, load_config


@pytest.fixture(scope="session")
def config() -> LoomConfig:
    return load_config(env={})


@pytest.fixture(scope="session")
def db(config: LoomConfig) -> FalkorDB:
    return FalkorDB(host=config.host, port=config.port)


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
def small_resultset_cap(db: FalkorDB) -> Iterator[int]:
    """Cap the server's RESULTSET_SIZE far below the seeded row counts so
    truncation bugs surface with small fixtures; restores the prior value.
    FalkorDB silently drops rows past the cap (default 10000), so any read
    that isn't paged or aggregated returns wrong answers on large graphs."""
    original = db.config_get("RESULTSET_SIZE")
    db.config_set("RESULTSET_SIZE", 40)
    yield 40
    db.config_set("RESULTSET_SIZE", original)
