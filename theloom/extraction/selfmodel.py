"""Self-model update.

Maintains The Loom's own codebase graph (``loom-codebase``). Refuses unless
the auto-detected project identifies as the-loom (skipped when an explicit
projectPath is given). Bootstraps on first run, incrementally updates
thereafter via the git commit marker.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from theloom.store.multigraph import MultiGraph

Doc = dict[str, Any]

SELF_MODEL_GRAPH_NAME = "loom-codebase"
LAST_UPDATED_COMMIT_KEY = "self_model.last_updated_commit"


def _git(args: list[str], cwd: str | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=30, check=True
    ).stdout.strip()


def _detect_project_root() -> str:
    root = _git(["rev-parse", "--show-toplevel"])
    package_path = os.path.join(root, "package.json")
    try:
        with open(package_path, encoding="utf-8") as handle:
            pkg = json.load(handle)
    except FileNotFoundError:
        # No package.json: this is the Python project. Accept if
        # pyproject names the package the-loom / theloom.
        if _identifies_as_loom(root):
            return root
        raise ValueError(
            f"Project at {root} is not The Loom. "
            "Self-model update only works on The Loom's own repository."
        ) from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid package.json at {root}") from exc
    if pkg.get("name") != "the-loom":
        raise ValueError(
            f"Project at {root} is not The Loom (package name: {pkg.get('name')}). "
            "Self-model update only works on The Loom's own repository."
        )
    return root


def _identifies_as_loom(root: str) -> bool:
    pyproject = os.path.join(root, "pyproject.toml")
    try:
        with open(pyproject, encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return False
    return 'name = "theloom"' in text or 'name = "the-loom"' in text


def update_self_model(
    *,
    project_path: str | None,
    graph_name: str = SELF_MODEL_GRAPH_NAME,
    dry_run: bool = False,
    multi: MultiGraph,
) -> Doc:
    from theloom.extraction import treesitter
    from theloom.extraction.codebasediff import update_codebase_diff
    from theloom.operations.bulk import BulkImportInput, bulk_import

    root = project_path or _detect_project_root()
    head_sha = _git(["rev-parse", "HEAD"], cwd=root)

    if not multi.has_graph(graph_name):
        multi.register_graph(graph_name)
    store = multi.get_store(graph_name)
    stored_commit = store.get_metadata(LAST_UPDATED_COMMIT_KEY)
    graph_empty = len(store.list_entities()) == 0

    if stored_commit and head_sha == stored_commit:
        return {
            "action": "skipped",
            "commitSha": head_sha,
            "previousCommitSha": stored_commit,
            "changedFiles": 0,
            "stats": None,
        }

    if not stored_commit or graph_empty:
        extraction = treesitter.extract_codebase(root)
        import_result = bulk_import(
            BulkImportInput.model_validate(
                {
                    "entities": extraction["entities"],
                    "relations": extraction["relations"],
                    "graph": graph_name,
                    "dryRun": dry_run,
                }
            ),
            multi,
        )
        if not dry_run:
            store.set_metadata(LAST_UPDATED_COMMIT_KEY, head_sha)
        return {
            "action": "bootstrapped",
            "commitSha": head_sha,
            "previousCommitSha": None,
            "changedFiles": extraction["stats"]["totalFiles"],
            "stats": {
                "entitiesCreated": import_result["entitiesCreated"],
                "entitiesUpdated": import_result["entitiesMerged"],
                "entitiesRetracted": 0,
                "relationsCreated": import_result["relationsCreated"],
                "relationsRemoved": 0,
            },
        }

    diff = update_codebase_diff(
        root, graph_name, git_ref=f"{stored_commit}..{head_sha}", dry_run=dry_run, multi=multi
    )
    if not dry_run:
        store.set_metadata(LAST_UPDATED_COMMIT_KEY, head_sha)
    return {
        "action": "updated",
        "commitSha": head_sha,
        "previousCommitSha": stored_commit,
        "changedFiles": len(diff["changedFiles"]),
        "stats": {
            "entitiesCreated": diff["stats"]["entitiesCreated"],
            "entitiesUpdated": diff["stats"]["entitiesUpdated"],
            "entitiesRetracted": diff["stats"]["entitiesRetracted"],
            "relationsCreated": diff["stats"]["relationsCreated"],
            "relationsRemoved": diff["stats"]["relationsRemoved"],
        },
    }
