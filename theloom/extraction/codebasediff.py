"""Incremental codebase update via git diff.

``git diff --name-status`` names the files that moved; the project is then
re-extracted whole (so the cross-file resolution pass still sees every file)
and the result is replayed **only over the named files**. Per changed file the
semantics are *replace on re-extract*:

* an entity the fresh extraction no longer produces is **superseded**, never
  deleted — ``source_retracted`` for a deleted file, ``outdated_knowledge``
  for one whose code changed, with the human reason on ``changeReason``;
* an entity whose observations changed is updated, and a new one is created
  carrying the same provenance and confidence the full extractor writes;
* every **structural** relation sourced from a changed file is diffed by
  ``(fromName, toName, relationType)``: edges the fresh extraction no longer
  states are closed out bi-temporally (``invalidate_relation`` — the edge
  leaves the projection, its final doc is kept), and edges it now states are
  created. Edges sourced from untouched files, and the semantic layer's
  ``related_to`` links into code, are left alone.

Ownership is by the file an entity was extracted from ("File path: " for a
symbol, the ``file:`` prefix for a file entity), and an edge belongs to the
file its **source** endpoint belongs to — the per-file pass and the resolution
pass both emit every edge from a symbol (or file) in the file being read.

The stats are the plan's real numbers, and ``dryRun`` reports that plan
without writing. A shrink guard refuses the whole update when the fresh
extraction has visibly collapsed — a still-present file that now extracts to
nothing, or an update that would supersede more than half the graph —
overridable with ``force``.
"""

from __future__ import annotations

import os
import subprocess
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from theloom.errors import OperationError
from theloom.extraction import treesitter
from theloom.model import Entity, EntityCreate, RelationCreate
from theloom.store.falkor import FalkorGraphStore
from theloom.store.multigraph import MultiGraph
from theloom.timeutil import iso_now

Doc = dict[str, Any]

# (fromName, toName, relationType) — the identity of an edge across extractions.
RelationKey = tuple[str, str, str]

_FILE_PREFIX = "file:"
_FILE_PATH_OBSERVATION = "File path: "

# Why an entity stopped appearing, in the model's closed vocabulary. The prose
# form the user reads travels on ``changeReason``.
_DELETED = ("source_retracted", "file deleted")
_CHANGED = ("outdated_knowledge", "code changed")

# The edge types structural extraction emits — and the only ones this diff
# touches. A semantic layer built on top of the same graph links into code with
# ``related_to``; those edges are nobody's re-extraction to retract, so an
# update leaves them exactly where they are.
_STRUCTURAL_RELATION_TYPES = frozenset({"part_of", "requires", "calls", "instance_of"})


def _is_extractable(path: str) -> bool:
    """True when extraction would collect this path — one rule, treesitter's."""
    parts = path.split("/")
    if any(part in treesitter.SKIP_DIRS or part.startswith(".") for part in parts[:-1]):
        return False
    return (
        treesitter.detect_language(path) is not None
        or treesitter.detect_text_kind(path) is not None
    )


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
        if not _is_extractable(path):
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


def _file_of(name: str, observations: Sequence[Any]) -> str | None:
    """The project file an entity was extracted from, or None (e.g. a package)."""
    if name.startswith(_FILE_PREFIX):
        return name[len(_FILE_PREFIX) :]
    for observation in observations:
        text = str(observation)
        if text.startswith(_FILE_PATH_OBSERVATION):
            return text[len(_FILE_PATH_OBSERVATION) :]
    return None


@dataclass
class _Plan:
    """Everything the update would do, computed before anything is written."""

    creates: list[Doc] = field(default_factory=list)
    updates: list[tuple[str, Doc]] = field(default_factory=list)
    supersedes: list[tuple[Entity, tuple[str, str]]] = field(default_factory=list)
    unchanged: int = 0
    retract_relations: list[Doc] = field(default_factory=list)
    create_relations: list[Doc] = field(default_factory=list)
    name_to_id: dict[str, str] = field(default_factory=dict)
    # Guard inputs: still-present files that now extract to nothing, and the
    # size of the live projection the supersessions are measured against.
    vanished: list[tuple[str, int]] = field(default_factory=list)
    active_total: int = 0


def _plan_update(changed: list[Doc], extraction: Doc, store: FalkorGraphStore) -> _Plan:
    changed_set = {change["path"] for change in changed}
    deleted_set = {change["path"] for change in changed if change["change"] == "deleted"}

    fresh_entities: list[Doc] = extraction["entities"]
    fresh_file = {e["name"]: _file_of(e["name"], e["observations"]) for e in fresh_entities}
    fresh_by_name = {e["name"]: e for e in fresh_entities}

    existing = store.list_entities()
    existing_file = {e.name: _file_of(e.name, e.observations) for e in existing}
    existing_by_name: dict[str, Entity] = {}
    for entity in existing:
        existing_by_name.setdefault(entity.name, entity)

    plan = _Plan(
        name_to_id={name: entity.id for name, entity in existing_by_name.items()},
        active_total=len(existing),
    )

    for doc in fresh_entities:
        if fresh_file[doc["name"]] not in changed_set:
            continue
        prior = existing_by_name.get(doc["name"])
        if prior is None:
            plan.creates.append(doc)
        elif list(prior.observations) != list(doc["observations"]):
            plan.updates.append((prior.id, doc))
        else:
            plan.unchanged += 1

    for entity in existing:
        path = existing_file.get(entity.name)
        if path is None or path not in changed_set or entity.name in fresh_by_name:
            continue
        plan.supersedes.append((entity, _DELETED if path in deleted_set else _CHANGED))

    _plan_relations(plan, changed_set, extraction, store, existing, fresh_file, fresh_by_name)

    fresh_counts = Counter(path for path in fresh_file.values() if path is not None)
    prior_counts = Counter(path for path in existing_file.values() if path is not None)
    plan.vanished = [
        (change["path"], prior_counts[change["path"]])
        for change in changed
        if change["change"] != "deleted"
        and fresh_counts[change["path"]] == 0
        and prior_counts[change["path"]] > 0
    ]
    return plan


def _plan_relations(
    plan: _Plan,
    changed_set: set[str],
    extraction: Doc,
    store: FalkorGraphStore,
    existing: list[Entity],
    fresh_file: dict[str, str | None],
    fresh_by_name: dict[str, Doc],
) -> None:
    """Diff the structural edges *sourced from* the changed files."""
    id_to_name = {entity.id: entity.name for entity in existing}
    owner = {entity.name: _file_of(entity.name, entity.observations) for entity in existing}

    live: dict[RelationKey, Doc] = {}
    for doc in store.list_relation_docs():
        if doc["relationType"] not in _STRUCTURAL_RELATION_TYPES:
            continue
        from_name = id_to_name.get(doc["from"])
        to_name = id_to_name.get(doc["to"])
        if from_name is None or to_name is None or owner.get(from_name) not in changed_set:
            continue
        live.setdefault((from_name, to_name, doc["relationType"]), doc)

    fresh: dict[RelationKey, Doc] = {}
    for relation in extraction["relations"]:
        if relation["relationType"] not in _STRUCTURAL_RELATION_TYPES:
            continue
        if fresh_file.get(relation["from"]) not in changed_set:
            continue
        fresh.setdefault((relation["from"], relation["to"], relation["relationType"]), relation)

    plan.retract_relations = [doc for key, doc in live.items() if key not in fresh]
    candidates = [relation for key, relation in fresh.items() if key not in live]

    # An edge may be the first thing to mention an entity that belongs to no
    # file — a third-party package node. Those come along; a symbol in an
    # untouched file that the graph has never seen does not, and its edge is
    # dropped rather than counted.
    resolvable = set(plan.name_to_id) | {doc["name"] for doc in plan.creates}
    for relation in candidates:
        for endpoint in (relation["from"], relation["to"]):
            if endpoint in resolvable or fresh_file.get(endpoint) is not None:
                continue
            entity = fresh_by_name.get(endpoint)
            if entity is not None:
                plan.creates.append(entity)
                resolvable.add(endpoint)
    plan.create_relations = [
        relation
        for relation in candidates
        if relation["from"] in resolvable and relation["to"] in resolvable
    ]


def _diagnose(plan: _Plan) -> str | None:
    """Why this update looks like a collapse rather than a change."""
    if plan.vanished:
        path, prior = plan.vanished[0]
        return (
            f"{path} is named by the diff but the fresh extraction found nothing in it, "
            f"while the graph holds {prior} entities from it — the file is unreadable, "
            "unparseable, or no longer collected. Applying the update would supersede "
            "every entity it has."
        )
    if plan.supersedes and len(plan.supersedes) * 2 > plan.active_total:
        return (
            f"the update would supersede {len(plan.supersedes)} of {plan.active_total} "
            "entities — more than half the graph."
        )
    return None


def _entity_diffs(plan: _Plan) -> list[Doc]:
    diffs = [
        {"entityName": doc["name"], "entityType": doc["entityType"], "status": "added"}
        for doc in plan.creates
    ]
    diffs += [
        {"entityName": doc["name"], "entityType": doc["entityType"], "status": "modified"}
        for _, doc in plan.updates
    ]
    diffs += [
        {
            "entityName": entity.name,
            "entityType": entity.entity_type.value,
            "status": "superseded",
        }
        for entity, _ in plan.supersedes
    ]
    return diffs


def _stats(plan: _Plan) -> Doc:
    return {
        "entitiesCreated": len(plan.creates),
        "entitiesUpdated": len(plan.updates),
        "entitiesRetracted": len(plan.supersedes),
        "entitiesUnchanged": plan.unchanged,
        "relationsCreated": len(plan.create_relations),
        "relationsRemoved": len(plan.retract_relations),
    }


def _entity_spec(doc: Doc) -> EntityCreate:
    """The fresh extraction's entity, provenance and confidence included."""
    spec: Doc = {
        "name": doc["name"],
        "entityType": doc["entityType"],
        "observations": doc["observations"],
    }
    if doc.get("confidence"):
        spec["confidence"] = {
            **doc["confidence"],
            "lastEvaluated": doc["confidence"].get("lastEvaluated") or iso_now(),
        }
    if doc.get("provenance"):
        provenance = doc["provenance"]
        spec["provenance"] = {
            **provenance,
            "sourceId": provenance.get("sourceId"),
            "externalRef": provenance.get("externalRef"),
            "extractionDate": provenance.get("extractionDate") or iso_now(),
            "extractionMethod": provenance.get("extractionMethod"),
        }
    return EntityCreate.model_validate(spec)


def _relation_spec(relation: Doc, name_to_id: dict[str, str]) -> RelationCreate:
    spec: Doc = {
        "from": name_to_id[relation["from"]],
        "to": name_to_id[relation["to"]],
        "relationType": relation["relationType"],
        "polarity": relation.get("polarity"),
        "strength": relation.get("strength") or "moderate",
        "evidence": relation.get("evidence"),
    }
    if relation.get("confidence"):
        spec["confidence"] = {
            **relation["confidence"],
            "lastEvaluated": relation["confidence"].get("lastEvaluated") or iso_now(),
        }
    return RelationCreate.model_validate(spec)


def _apply(plan: _Plan, store: FalkorGraphStore) -> list[str]:
    """Write the plan; returns every entity id the update touched."""
    changed_ids: list[str] = []
    for doc in plan.creates:
        created = store.create_entity(_entity_spec(doc))
        plan.name_to_id[doc["name"]] = created.id
        changed_ids.append(created.id)
    for entity_id, doc in plan.updates:
        store.update_entity(entity_id, {"observations": doc["observations"]})
        changed_ids.append(entity_id)
    for entity, (status_reason, change_reason) in plan.supersedes:
        store.update_entity(
            entity.id,
            {
                "status": "superseded",
                "statusReason": status_reason,
                "statusChangedAt": iso_now(),
                "changeReason": change_reason,
            },
        )
        changed_ids.append(entity.id)
    for doc in plan.retract_relations:
        store.invalidate_relation(doc["from"], doc["to"], doc["relationType"])
    for relation in plan.create_relations:
        store.create_relation(_relation_spec(relation, plan.name_to_id))
    return changed_ids


def update_codebase_diff(
    project_path: str,
    graph_name: str,
    *,
    git_ref: str = "HEAD~1..HEAD",
    include_tests: bool = True,
    dry_run: bool = False,
    force: bool = False,
    multi: MultiGraph,
) -> Doc:
    if not os.path.exists(project_path):
        raise FileNotFoundError(f"Project path does not exist: {project_path}")

    changed = _detect_changed_files(project_path, git_ref)
    if not changed:
        return _empty_result([])

    store = multi.get_store(graph_name)
    extraction = treesitter.extract_codebase(project_path, include_tests=include_tests)
    plan = _plan_update(changed, extraction, store)

    if not force:
        diagnosis = _diagnose(plan)
        if diagnosis is not None:
            raise OperationError(
                f"Refusing to update graph '{graph_name}' from {git_ref}: {diagnosis} "
                'Re-run with "force": true to apply it anyway.'
            )

    return {
        "changedFiles": [change["path"] for change in changed],
        "entityDiffs": _entity_diffs(plan),
        "stats": _stats(plan),
        "changedEntityIds": [] if dry_run else _apply(plan, store),
    }
