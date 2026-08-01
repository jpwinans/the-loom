"""Extraction run store.

Runs are persisted as events in a dedicated Redis stream
(``{prefix}:_extraction_runs``) — an event-log-backed design — so
status/rollback survive across CLI invocations rather than living in
process memory.
"""

from __future__ import annotations

import json
from typing import Any

from redis import Redis

Doc = dict[str, Any]
_STREAM_SUFFIX = "_extraction_runs"


class RunStore:
    def __init__(self, redis: Redis, key_prefix: str = "loom") -> None:
        self._redis = redis
        self._key = f"{key_prefix}:{_STREAM_SUFFIX}"

    def save_run(self, run: Doc) -> None:
        self._redis.rpush(self._key, json.dumps(run))

    def list_runs(self) -> list[Doc]:
        raw: list[Any] = list(self._redis.lrange(self._key, 0, -1))
        return [json.loads(item) for item in raw]

    def get_run(self, run_id: str) -> Doc | None:
        for run in self.list_runs():
            if run.get("runId") == run_id:
                return run
        return None

    def wipe(self) -> None:
        self._redis.delete(self._key)
