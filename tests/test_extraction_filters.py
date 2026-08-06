"""``include``/``exclude`` path filters on codebase extraction.

Both fields are declared on ``ExtractCodebaseInput``/``UpdateCodebaseInput``
and exposed in the CLI schema; these tests pin that they actually narrow what
gets collected — glob patterns matched against the file's project-relative,
forward-slash path, ``exclude`` applied after ``include``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from theloom.extraction import treesitter
from theloom.operations.extraction import ExtractCodebaseInput, extract_codebase
from theloom.store.falkor import FalkorGraphStore
from theloom.store.multigraph import MultiGraph

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "repo"
GRAPH = "default"


@pytest.fixture()
def store(multi: MultiGraph) -> FalkorGraphStore:
    return multi.get_store(GRAPH)


class TestCollectSourceFilesGlobs:
    """Unit-level: the glob mechanics ``collect_source_files`` applies."""

    def test_exclude_drops_matching_paths(self) -> None:
        paths = {
            f["relativePath"]
            for f in treesitter.collect_source_files(str(FIXTURE_REPO), exclude=["lib/*"])
        }
        assert "lib/helper.js" not in paths
        assert "lib/index.ts" not in paths
        assert "src/models.py" in paths

    def test_include_keeps_only_matching_paths(self) -> None:
        paths = {
            f["relativePath"]
            for f in treesitter.collect_source_files(str(FIXTURE_REPO), include=["src/*"])
        }
        assert paths == {"src/models.py", "src/policy.py", "src/service.py"}

    def test_exclude_wins_over_include(self) -> None:
        paths = {
            f["relativePath"]
            for f in treesitter.collect_source_files(
                str(FIXTURE_REPO), include=["src/*"], exclude=["src/policy.py"]
            )
        }
        assert paths == {"src/models.py", "src/service.py"}

    def test_empty_include_list_means_no_restriction(self) -> None:
        with_none = {f["relativePath"] for f in treesitter.collect_source_files(str(FIXTURE_REPO))}
        with_empty = {
            f["relativePath"]
            for f in treesitter.collect_source_files(str(FIXTURE_REPO), include=[])
        }
        assert with_empty == with_none


class TestExtractCodebaseExcludesEntities:
    """Ops-level: `extract-codebase`'s exclude keeps the file's entities out
    of the graph entirely, not merely out of the file list."""

    def test_exclude_filters_out_matching_files(
        self, multi: MultiGraph, store: FalkorGraphStore
    ) -> None:
        extract_codebase(
            ExtractCodebaseInput.model_validate(
                {"projectPath": str(FIXTURE_REPO), "graph": GRAPH, "exclude": ["lib/*"]}
            ),
            multi,
        )

        names = {e.name for e in store.list_entities()}
        assert "file:lib/helper.js" not in names
        assert "formatBalance (helper)" not in names
        assert "roundCents (helper)" not in names
        assert "file:src/models.py" in names

    def test_include_keeps_only_matching_files(
        self, multi: MultiGraph, store: FalkorGraphStore
    ) -> None:
        extract_codebase(
            ExtractCodebaseInput.model_validate(
                {"projectPath": str(FIXTURE_REPO), "graph": GRAPH, "include": ["src/*"]}
            ),
            multi,
        )

        names = {e.name for e in store.list_entities()}
        assert "file:src/models.py" in names
        assert "file:lib/helper.js" not in names
        assert "file:README.md" not in names
