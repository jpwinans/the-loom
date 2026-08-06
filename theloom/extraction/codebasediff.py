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
* every **structural** relation *touching* a changed file is diffed by
  ``(fromName, toName, relationType)``: edges the fresh extraction no longer
  states are closed out bi-temporally (``invalidate_relation`` — the edge
  leaves the projection, its final doc is kept), and edges it now states are
  created. Edges between two untouched files are left alone, and so is
  anything structural extraction does not emit: the semantic layer's
  ``related_to`` links into code, and any ``references`` edge that does not
  come *from a documentation file* (the only shape the doc-link pass states).

Ownership is by the file an entity was extracted from ("File path: " for a
symbol, the ``file:`` prefix for a file entity). An edge belongs to a changed
file when **either** endpoint does: cross-file edges are emitted by the
resolution pass over the whole project, so an edge pointing *into* a changed
file is just as much that file's business as one pointing out of it. Scoping
by the source endpoint alone would strand inbound edges — leaving stale ones
dangling onto superseded symbols and never creating newly-resolvable ones.

The stats are the plan's real numbers, and ``dryRun`` reports that plan
without writing. A shrink guard refuses the whole update when the fresh
extraction has visibly collapsed — a still-present file that now extracts to
nothing, or an update that would supersede more than half of the graph's
*file-owned* population (the only entities an update can supersede, so a
semantic layer sharing the graph cannot dilute the guard) — overridable with
``force``.
"""

from __future__ import annotations

import os
import subprocess
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from theloom.errors import OperationError
from theloom.extraction import doclinks, treesitter
from theloom.extraction.encoding import is_file_entity_name, parse_file_entity_name, parse_file_path
from theloom.model import Entity, EntityCreate, EntityFilter, RelationCreate
from theloom.store.falkor import FalkorGraphStore
from theloom.store.filters import NON_RETRACTED_ENTITY_STATUSES, prefer_active_by_name
from theloom.store.multigraph import MultiGraph
from theloom.timeutil import iso_now

Doc = dict[str, Any]

# Fallback pool for resolving a relation endpoint's name to an id when it
# isn't among the (active-only) entities the planner already read — see
# ``_plan_update``'s ``name_to_id`` fallback.
_NON_RETRACTED = EntityFilter.model_validate({"statusFilter": list(NON_RETRACTED_ENTITY_STATUSES)})

# (fromName, toName, relationType) — the identity of an edge across extractions.
RelationKey = tuple[str, str, str]

# Why an entity stopped appearing, in the model's closed vocabulary. The prose
# form the user reads travels on ``changeReason``.
_DELETED = ("source_retracted", "file deleted")
_CHANGED = ("outdated_knowledge", "code changed")

# The edge types structural extraction emits — and the only ones this diff
# touches (``references`` included: a doc's links are re-derived from its text,
# so editing the doc must retract the mentions it dropped). A semantic layer
# built on top of the same graph links into code with ``related_to``; those
# edges are nobody's re-extraction to retract, so an update leaves them exactly
# where they are.
_STRUCTURAL_RELATION_TYPES = frozenset(
    {"part_of", "requires", "calls", "instance_of", "references"}
)

# ``references`` is the one structural type the rest of the graph also uses —
# a claim referencing a file, a user's own edge. Structural extraction emits it
# from a *documentation file entity* and nowhere else, so only edges of that
# exact shape are this diff's to re-derive; every other ``references`` edge is
# left where it is, like ``related_to``.
_DOC_SUFFIXES = tuple(f".{extension}" for extension in sorted(doclinks.DOC_EXTENSIONS))


def _detect_changed_files(
    project_path: str,
    git_ref: str,
    *,
    include_tests: bool,
    include: Sequence[str] | None = None,
    exclude: Sequence[str] | None = None,
) -> list[Doc]:
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
    is_extractable = treesitter.extractable_paths(
        project_path, include_tests=include_tests, include=include, exclude=exclude
    )
    return _parse_git_diff(output, is_extractable, include=include, exclude=exclude)


def _is_extractable_shape(
    path: str, *, include: Sequence[str] | None, exclude: Sequence[str] | None
) -> bool:
    """The part of treesitter's rule that survives the file being gone: path
    shape alone (skip directories, extension/text-kind, include/exclude
    globs). Used only for a deletion, where the file no longer exists to
    stat, read, or check git tracking on — every other status defers to
    ``treesitter.extractable_paths`` in full.
    """
    parts = path.split("/")
    if any(part in treesitter.SKIP_DIRS or part.startswith(".") for part in parts[:-1]):
        return False
    if treesitter.detect_language(path) is None and treesitter.detect_text_kind(path) is None:
        return False
    if include and not treesitter.matches_globs(path, include):
        return False
    return not treesitter.matches_globs(path, exclude)


def _parse_git_diff(
    output: str,
    is_extractable: Callable[[str], bool],
    *,
    include: Sequence[str] | None = None,
    exclude: Sequence[str] | None = None,
) -> list[Doc]:
    changes: list[Doc] = []
    status_map = {"A": "added", "M": "modified", "D": "deleted", "R": "added", "C": "added"}
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0][0]
        path = parts[2] if status in ("R", "C") and len(parts) >= 3 else parts[1]
        in_scope = (
            _is_extractable_shape(path, include=include, exclude=exclude)
            if status == "D"
            else is_extractable(path)
        )
        if not in_scope:
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
    file_path = parse_file_entity_name(name)
    if file_path is not None:
        return file_path
    return parse_file_path(observations)


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
    # size of the population the supersessions are measured against — the
    # active entities that belong to a file, which is exactly the set an
    # update is able to supersede.
    vanished: list[tuple[str, int]] = field(default_factory=list)
    supersedable_total: int = 0


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

    name_to_id = {name: entity.id for name, entity in existing_by_name.items()}
    # A relation may name an entity this update doesn't otherwise touch and
    # that isn't currently active (superseded by something unrelated to this
    # diff) — resolved with the same name->id tie-break bulk import uses for
    # its own relation resolution (prefer active, else first-seen over every
    # non-retracted status) rather than left unresolvable and silently
    # dropped just because `existing` above only ever saw actives.
    if extraction["relations"]:
        for name, entity in prefer_active_by_name(store.list_entities(_NON_RETRACTED)).items():
            name_to_id.setdefault(name, entity.id)

    plan = _Plan(
        name_to_id=name_to_id,
        supersedable_total=sum(1 for e in existing if existing_file.get(e.name) is not None),
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


def _is_structural(relation_type: str, from_name: str, from_path: str | None) -> bool:
    """True when this edge is one structural extraction re-derives.

    Type alone decides it for every type but ``references``, which the graph
    shares with the semantic layer and with hand-authored edges. Structural
    extraction only ever states ``references`` from a documentation file
    entity, so that shape — and only that shape — is the diff's to retract.
    """
    if relation_type not in _STRUCTURAL_RELATION_TYPES:
        return False
    if relation_type != "references":
        return True
    return (
        is_file_entity_name(from_name)
        and from_path is not None
        and from_path.endswith(_DOC_SUFFIXES)
    )


def _plan_relations(
    plan: _Plan,
    changed_set: set[str],
    extraction: Doc,
    store: FalkorGraphStore,
    existing: list[Entity],
    fresh_file: dict[str, str | None],
    fresh_by_name: dict[str, Doc],
) -> None:
    """Diff the structural edges *touching* the changed files (either endpoint)."""
    id_to_name = {entity.id: entity.name for entity in existing}
    owner = {entity.name: _file_of(entity.name, entity.observations) for entity in existing}

    def touches(*files: str | None) -> bool:
        return any(path in changed_set for path in files)

    live: dict[RelationKey, Doc] = {}
    for doc in store.list_relation_docs():
        from_name = id_to_name.get(doc["from"])
        to_name = id_to_name.get(doc["to"])
        if from_name is None or to_name is None:
            continue
        if not _is_structural(doc["relationType"], from_name, owner.get(from_name)):
            continue
        if not touches(owner.get(from_name), owner.get(to_name)):
            continue
        live.setdefault((from_name, to_name, doc["relationType"]), doc)

    fresh: dict[RelationKey, Doc] = {}
    for relation in extraction["relations"]:
        from_name = relation["from"]
        if not _is_structural(relation["relationType"], from_name, fresh_file.get(from_name)):
            continue
        if not touches(fresh_file.get(from_name), fresh_file.get(relation["to"])):
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
    if plan.supersedes and len(plan.supersedes) * 2 > plan.supersedable_total:
        return (
            f"the update would supersede {len(plan.supersedes)} of {plan.supersedable_total} "
            "entities extracted from files — more than half the graph's structural layer."
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


@dataclass
class _ApplyResult:
    """What ``_apply`` wrote. ``changed_ids`` is every entity id the update
    touched (created, updated, or superseded) — the wire ``changedEntityIds``.
    ``created_entity_ids``/``created_relation_ids`` are the strict subset this
    run actually *created*: safe for a rollback to hard-delete, unlike an
    update or a supersession, which changed something that predates this run.
    """

    changed_ids: list[str]
    created_entity_ids: list[str]
    created_relation_ids: list[str]


def _apply(plan: _Plan, store: FalkorGraphStore) -> _ApplyResult:
    """Write the plan."""
    changed_ids: list[str] = []
    created_entity_ids: list[str] = []
    created_relation_ids: list[str] = []
    for doc in plan.creates:
        created = store.create_entity(_entity_spec(doc))
        plan.name_to_id[doc["name"]] = created.id
        changed_ids.append(created.id)
        created_entity_ids.append(created.id)
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
        created_relation = store.create_relation(_relation_spec(relation, plan.name_to_id))
        created_relation_ids.append(
            f"{created_relation.from_}->{created_relation.to}->{created_relation.relation_type.value}"
        )
    return _ApplyResult(changed_ids, created_entity_ids, created_relation_ids)


def update_codebase_diff(
    project_path: str,
    graph_name: str,
    *,
    git_ref: str = "HEAD~1..HEAD",
    include_tests: bool = True,
    include: Sequence[str] | None = None,
    exclude: Sequence[str] | None = None,
    dry_run: bool = False,
    force: bool = False,
    multi: MultiGraph,
) -> Doc:
    if not os.path.exists(project_path):
        raise FileNotFoundError(f"Project path does not exist: {project_path}")

    started_at = iso_now()
    changed = _detect_changed_files(
        project_path, git_ref, include_tests=include_tests, include=include, exclude=exclude
    )
    if not changed:
        return _empty_result([])

    store = multi.get_store(graph_name)
    extraction = treesitter.extract_codebase(
        project_path, include_tests=include_tests, include=include, exclude=exclude
    )
    plan = _plan_update(changed, extraction, store)

    if not force:
        diagnosis = _diagnose(plan)
        if diagnosis is not None:
            raise OperationError(
                f"Refusing to update graph '{graph_name}' from {git_ref}: {diagnosis} "
                'Re-run with "force": true to apply it anyway.'
            )

    apply_result = _ApplyResult([], [], []) if dry_run else _apply(plan, store)
    run_id = multi.run_store().save_codebase_run(
        started_at=started_at,
        created_entity_ids=apply_result.created_entity_ids,
        created_relation_ids=apply_result.created_relation_ids,
        dry_run=dry_run,
    )
    return {
        "changedFiles": [change["path"] for change in changed],
        "entityDiffs": _entity_diffs(plan),
        "stats": _stats(plan),
        "changedEntityIds": apply_result.changed_ids,
        "runId": run_id,
    }
