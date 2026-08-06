"""extraction-rollback over codebase extraction runs.

extraction-rollback was written for the LLM document pipeline, the only path
that ever wrote a run record — extract-codebase and update-codebase never
did, so "rollback" on one of their runs looked supported (no error) but
deleted nothing. Both now write a real run record, scoped to what the run
actually *created* (never what it merely merged into or superseded, which
predates the run and rollback must not touch).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from theloom.errors import NotFoundError
from theloom.extraction.codebasediff import update_codebase_diff
from theloom.operations.bulk import BulkImportInput, bulk_import
from theloom.operations.extraction import (
    ExtractCodebaseInput,
    ExtractionRollbackInput,
    ExtractionStatusInput,
    extract_codebase,
    extraction_rollback,
    extraction_status,
)
from theloom.store.falkor import FalkorGraphStore
from theloom.store.multigraph import MultiGraph

Doc = dict[str, Any]

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "repo"
GRAPH = "default"


@pytest.fixture()
def store(multi: MultiGraph) -> FalkorGraphStore:
    return multi.get_store(GRAPH)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def commit(repo: Path, message: str) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    work = tmp_path / "project"
    shutil.copytree(FIXTURE_REPO, work)
    git(work, "init", "-q")
    git(work, "config", "user.email", "tests@example.com")
    git(work, "config", "user.name", "Loom Tests")
    commit(work, "initial")
    return work


def names(store: FalkorGraphStore) -> set[str]:
    return {e.name for e in store.list_entities()}


class TestExtractCodebaseRollback:
    def test_extract_codebase_writes_a_run_record(
        self, multi: MultiGraph, store: FalkorGraphStore
    ) -> None:
        result = extract_codebase(
            ExtractCodebaseInput.model_validate({"projectPath": str(FIXTURE_REPO), "graph": GRAPH}),
            multi,
        )

        assert result["runId"]
        run = extraction_status(
            ExtractionStatusInput.model_validate({"runId": result["runId"]}), multi
        )
        assert run["status"] == "completed"
        assert run["createdEntityIds"]
        assert set(run["createdEntityIds"]) <= {e.id for e in store.list_entities()}

    def test_rollback_deletes_exactly_what_the_run_created(
        self, multi: MultiGraph, store: FalkorGraphStore
    ) -> None:
        result = extract_codebase(
            ExtractCodebaseInput.model_validate({"projectPath": str(FIXTURE_REPO), "graph": GRAPH}),
            multi,
        )
        assert names(store)  # sanity: the extraction actually landed something

        rollback = extraction_rollback(
            ExtractionRollbackInput.model_validate({"runId": result["runId"], "graph": GRAPH}),
            multi,
        )

        assert rollback["deletedEntities"] > 0
        assert names(store) == set()

    def test_rollback_never_touches_a_merged_entity_from_an_earlier_run(
        self, multi: MultiGraph, store: FalkorGraphStore
    ) -> None:
        """A second extraction over the same project merges into (never
        recreates) entities the first run already made — rollback of the
        *second* run must leave those pre-existing entities alone."""
        extract_codebase(
            ExtractCodebaseInput.model_validate({"projectPath": str(FIXTURE_REPO), "graph": GRAPH}),
            multi,
        )
        before = names(store)

        second = extract_codebase(
            ExtractCodebaseInput.model_validate({"projectPath": str(FIXTURE_REPO), "graph": GRAPH}),
            multi,
        )
        run = extraction_status(
            ExtractionStatusInput.model_validate({"runId": second["runId"]}), multi
        )
        assert run["createdEntityIds"] == []  # everything merged, nothing new

        extraction_rollback(
            ExtractionRollbackInput.model_validate({"runId": second["runId"], "graph": GRAPH}),
            multi,
        )

        assert names(store) == before

    def test_dry_run_extract_codebase_writes_no_run_record(self, multi: MultiGraph) -> None:
        result = extract_codebase(
            ExtractCodebaseInput.model_validate(
                {"projectPath": str(FIXTURE_REPO), "graph": GRAPH, "dryRun": True}
            ),
            multi,
        )
        with pytest.raises(NotFoundError):
            extraction_status(
                ExtractionStatusInput.model_validate({"runId": result["runId"]}), multi
            )


class TestRollbackRelationTargeting:
    def test_rollback_deletes_the_runs_own_typed_edge_not_the_oldest_one(
        self, multi: MultiGraph, store: FalkorGraphStore
    ) -> None:
        """A pair can accumulate one typed edge per run. Rolling back the
        second run must drop *its* edge and leave the first run's edge — a
        codebase symbol that both calls and later extends the same target is
        ordinary, and hard-deleting the wrong edge erases its history."""
        first = bulk_import(
            BulkImportInput.model_validate(
                {
                    "entities": [
                        {"name": "Foo", "entityType": "concept", "observations": ["a"]},
                        {"name": "Base", "entityType": "concept", "observations": ["b"]},
                    ],
                    "relations": [{"from": "Foo", "to": "Base", "relationType": "calls"}],
                    "graph": GRAPH,
                }
            ),
            multi,
        )
        second = bulk_import(
            BulkImportInput.model_validate(
                {
                    "entities": [],
                    "relations": [{"from": "Foo", "to": "Base", "relationType": "instance_of"}],
                    "graph": GRAPH,
                }
            ),
            multi,
        )
        foo_id = first["mapping"]["Foo"]
        base_id = first["mapping"]["Base"]
        assert {r.relation_type for r in store.read_relations(foo_id, base_id)} == {
            "calls",
            "instance_of",
        }

        run_id = multi.run_store().save_codebase_run(
            started_at="2026-01-01T00:00:00Z",
            created_entity_ids=[],
            created_relation_ids=second["createdRelationIds"],
            dry_run=False,
        )
        rollback = extraction_rollback(
            ExtractionRollbackInput.model_validate({"runId": run_id, "graph": GRAPH}), multi
        )

        assert rollback["deletedRelations"] == 1
        assert [r.relation_type for r in store.read_relations(foo_id, base_id)] == ["calls"]


class TestUpdateCodebaseRollback:
    def test_update_codebase_writes_a_run_record_scoped_to_new_entities(
        self, repo: Path, multi: MultiGraph, store: FalkorGraphStore
    ) -> None:
        extract_codebase(
            ExtractCodebaseInput.model_validate({"projectPath": str(repo), "graph": GRAPH}), multi
        )
        before = names(store)

        (repo / "src" / "audit.py").write_text(
            '"""Audit helpers."""\n\n\ndef audit(amount: float) -> bool:\n    return amount > 0\n',
            encoding="utf-8",
        )
        commit(repo, "add the audit module")

        result = update_codebase_diff(str(repo), GRAPH, multi=multi)

        assert result["runId"]
        run = extraction_status(
            ExtractionStatusInput.model_validate({"runId": result["runId"]}), multi
        )
        assert set(run["createdEntityIds"]) == {
            e.id for e in store.list_entities() if e.name not in before
        }

        extraction_rollback(
            ExtractionRollbackInput.model_validate({"runId": result["runId"], "graph": GRAPH}),
            multi,
        )
        assert names(store) == before
