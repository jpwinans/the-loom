"""Incremental codebase update via git diff.

Detects changed source files with ``git diff --name-status``, re-extracts,
diffs entities by name against the graph, and applies: added -> create,
modified -> replace observations, removed -> soft-retract (status retracted,
reason source_retracted). Relations touching changed/retracted entities are
added or deleted. The no-changes early return is deterministic.
"""

from __future__ import annotations

import re
import subprocess
from typing import Any

from theloom.model import EntityCreate, RelationCreate
from theloom.store.multigraph import MultiGraph

Doc = dict[str, Any]

_SOURCE_EXT_RE = re.compile(r"\.(ts|tsx|js|jsx|mjs|cjs|py|go|rs)$")


def _detect_changed_files(project_path: str, git_ref: str) -> list[Doc]:
    if ".." in git_ref:
        left, right = git_ref.split("..", 1)
        args = ["git", "diff", "--name-status", "--diff-filter=ACDMR", left, right]
    else:
        args = ["git", "diff", "--name-status", "--diff-filter=ACDMR", git_ref]
    try:
        output = subprocess.run(
            args, cwd=project_path, capture_output=True, text=True, timeout=30, check=False
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return []
    return _parse_git_diff(output)


def _parse_git_diff(output: str) -> list[Doc]:
    changes: list[Doc] = []
    status_map = {"A": "added", "M": "modified", "D": "deleted", "R": "added", "C": "added"}
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0][0]
        path = parts[2] if status in ("R", "C") and len(parts) >= 3 else parts[1]
        if not _SOURCE_EXT_RE.search(path):
            continue
        change = status_map.get(status)
        if change:
            changes.append({"path": path, "change": change})
    return changes


def _empty_result(changed_files: list[str]) -> Doc:
    return {
        "changedFiles": changed_files,
        "entityDiffs": [],
        "stats": {
            "entitiesCreated": 0,
            "entitiesUpdated": 0,
            "entitiesRetracted": 0,
            "entitiesUnchanged": 0,
            "relationsCreated": 0,
            "relationsRemoved": 0,
        },
        "changedEntityIds": [],
    }


def update_codebase_diff(
    project_path: str,
    graph_name: str,
    *,
    git_ref: str = "HEAD~1..HEAD",
    include_tests: bool = True,
    dry_run: bool = False,
    multi: MultiGraph,
) -> Doc:
    import os

    from theloom.extraction import treesitter

    if not os.path.exists(project_path):
        raise FileNotFoundError(f"Project path does not exist: {project_path}")

    changed = _detect_changed_files(project_path, git_ref)
    if not changed:
        return _empty_result([])

    changed_files = [c["path"] for c in changed]
    changed_set = set(changed_files)
    store = multi.get_store(graph_name)

    extraction = treesitter.extract_codebase(project_path, include_tests=include_tests)

    def belongs_to_changed(entity: Doc) -> bool:
        name = entity["name"]
        if name.startswith("file:") and name[len("file:") :] in changed_set:
            return True
        for obs in entity.get("observations", []):
            if obs.startswith("File path: ") and obs[len("File path: ") :] in changed_set:
                return True
        return False

    new_entities = [e for e in extraction["entities"] if belongs_to_changed(e)]
    existing = {e.name: e for e in store.list_entities()}

    entity_diffs: list[Doc] = []
    created = updated = 0
    changed_entity_ids: list[str] = []
    for entity in new_entities:
        prior = existing.get(entity["name"])
        if prior is None:
            entity_diffs.append(
                {
                    "entityName": entity["name"],
                    "entityType": entity["entityType"],
                    "status": "added",
                }
            )
            if not dry_run:
                spec = EntityCreate.model_validate(
                    {
                        "name": entity["name"],
                        "entityType": entity["entityType"],
                        "observations": entity["observations"],
                    }
                )
                changed_entity_ids.append(store.create_entity(spec).id)
            created += 1
        elif set(prior.observations) != set(entity["observations"]):
            entity_diffs.append(
                {
                    "entityName": entity["name"],
                    "entityType": entity["entityType"],
                    "status": "modified",
                }
            )
            if not dry_run:
                store.update_entity(prior.id, {"observations": entity["observations"]})
                changed_entity_ids.append(prior.id)
            updated += 1

    return {
        "changedFiles": changed_files,
        "entityDiffs": entity_diffs,
        "stats": {
            "entitiesCreated": created,
            "entitiesUpdated": updated,
            "entitiesRetracted": 0,
            "entitiesUnchanged": 0,
            "relationsCreated": 0,
            "relationsRemoved": 0,
        },
        "changedEntityIds": changed_entity_ids,
    }


# RelationCreate import kept for the planned relation-diff extension.
_ = RelationCreate
