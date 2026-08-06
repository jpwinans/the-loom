"""Extraction run store.

Runs are persisted as events in a dedicated Redis stream
(``{prefix}:_extraction_runs``) — an event-log-backed design — so
status/rollback survive across CLI invocations rather than living in
process memory.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from redis import Redis

from theloom.timeutil import iso_now

Doc = dict[str, Any]
_STREAM_SUFFIX = "_extraction_runs"


class RunStore:
    def __init__(self, redis: Redis, key_prefix: str = "loom") -> None:
        self._redis = redis
        self._key = f"{key_prefix}:{_STREAM_SUFFIX}"

    def save_run(self, run: Doc) -> None:
        self._redis.rpush(self._key, json.dumps(run))

    def save_codebase_run(
        self,
        *,
        started_at: str,
        created_entity_ids: list[str],
        created_relation_ids: list[str],
        dry_run: bool,
    ) -> str:
        """Mint a run id for a codebase-extraction run (extract-codebase or
        update-codebase) and persist it unless ``dry_run``.

        Shaped exactly like the LLM document pipeline's own run record (the
        ``createdEntityIds``/``createdRelationIds`` fields are all
        extraction-rollback reads) so rollback works identically over every
        extraction path — not only the document pipeline that originated
        this store — as long as the caller passes ids for what it actually
        *created*, never what it merely merged into or superseded, which
        predates the run and a rollback must not touch.

        Always returns an id, even for a dry run, matching every other
        extraction path's convention — nothing else about a dry run is
        persisted either, so the id names a run that was never saved.
        """
        run_id = str(uuid.uuid4())
        if not dry_run:
            self.save_run(
                {
                    "runId": run_id,
                    "status": "completed",
                    "startedAt": started_at,
                    "completedAt": iso_now(),
                    "totalEntitiesCreated": len(created_entity_ids),
                    "totalRelationsCreated": len(created_relation_ids),
                    "createdEntityIds": created_entity_ids,
                    "createdRelationIds": created_relation_ids,
                    "sourceEntityIds": [],
                    "synthesisEntityIds": [],
                    "convergenceEntityIds": [],
                }
            )
        return run_id

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
