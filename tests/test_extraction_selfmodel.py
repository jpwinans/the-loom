"""Pure helpers behind the self-model bootstrap: the loom-identity check and
project-root detection, both previously untested.

``_identifies_as_loom`` and ``_detect_project_root`` gate
``update-self-model`` so it can only ever touch The Loom's own repository —
these tests pin that gate directly, at the seam it actually decides on.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from theloom.extraction import selfmodel


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "project"
    repo.mkdir()
    git(repo, "init", "-q")
    return repo


class TestIdentifiesAsLoom:
    def test_true_for_theloom_package_name(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('name = "theloom"\n', encoding="utf-8")
        assert selfmodel._identifies_as_loom(str(tmp_path))

    def test_true_for_the_loom_package_name(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('name = "the-loom"\n', encoding="utf-8")
        assert selfmodel._identifies_as_loom(str(tmp_path))

    def test_false_for_a_different_package_name(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            'name = "someone-elses-project"\n', encoding="utf-8"
        )
        assert not selfmodel._identifies_as_loom(str(tmp_path))

    def test_false_when_pyproject_is_missing(self, tmp_path: Path) -> None:
        assert not selfmodel._identifies_as_loom(str(tmp_path))


class TestDetectProjectRoot:
    def test_returns_root_when_package_json_names_the_loom(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (git_repo / "package.json").write_text(json.dumps({"name": "the-loom"}), encoding="utf-8")
        monkeypatch.chdir(git_repo)
        assert selfmodel._detect_project_root() == str(git_repo.resolve())

    def test_raises_when_package_json_names_something_else(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (git_repo / "package.json").write_text(
            json.dumps({"name": "someone-elses-project"}), encoding="utf-8"
        )
        monkeypatch.chdir(git_repo)
        with pytest.raises(ValueError, match="not The Loom"):
            selfmodel._detect_project_root()

    def test_raises_on_invalid_package_json(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (git_repo / "package.json").write_text("{not valid json", encoding="utf-8")
        monkeypatch.chdir(git_repo)
        with pytest.raises(ValueError, match="Invalid package.json"):
            selfmodel._detect_project_root()

    def test_falls_back_to_pyproject_when_package_json_is_absent(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (git_repo / "pyproject.toml").write_text('name = "theloom"\n', encoding="utf-8")
        monkeypatch.chdir(git_repo)
        assert selfmodel._detect_project_root() == str(git_repo.resolve())

    def test_raises_without_package_json_or_a_matching_pyproject(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(git_repo)
        with pytest.raises(ValueError, match="not The Loom"):
            selfmodel._detect_project_root()
