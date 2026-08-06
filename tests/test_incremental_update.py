"""Incremental codebase update: relation diffing, retraction, shrink guard.

`update-codebase` re-extracts the project and replays only the files a git
diff names. What lands is *replace-on-re-extract per changed file*: symbols
that vanished are superseded (never hard-deleted), edges sourced from a
changed file are closed out bi-temporally and re-created from the fresh
extraction, and a fresh extraction that collapses is refused rather than
applied. These tests pin all of that against a real throwaway git repo built
from tests/fixtures/repo.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from falkordb import FalkorDB

from theloom.errors import NotFoundError, OperationError
from theloom.extraction import treesitter
from theloom.extraction.codebasediff import update_codebase_diff
from theloom.model import EntityCreate, EntityFilter, RelationCreate
from theloom.operations.bulk import BulkImportInput, bulk_import
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
    """A throwaway git repo holding the fixture project at one commit."""
    work = tmp_path / "project"
    shutil.copytree(FIXTURE_REPO, work)
    git(work, "init", "-q")
    git(work, "config", "user.email", "tests@example.com")
    git(work, "config", "user.name", "Loom Tests")
    commit(work, "initial")
    return work


@pytest.fixture()
def seeded(repo: Path, multi: MultiGraph) -> Path:
    """The fixture repo, fully extracted into the graph (the "full" path)."""
    extraction = treesitter.extract_codebase(str(repo))
    bulk_import(
        BulkImportInput.model_validate(
            {
                "entities": extraction["entities"],
                "relations": extraction["relations"],
                "graph": GRAPH,
            }
        ),
        multi,
    )
    return repo


def update(repo: Path, multi: MultiGraph, **kwargs: Any) -> Doc:
    return update_codebase_diff(str(repo), GRAPH, multi=multi, **kwargs)


def names(store: FalkorGraphStore) -> set[str]:
    return {e.name for e in store.list_entities()}


def edges(store: FalkorGraphStore) -> set[tuple[str, str, str]]:
    """Live (fromName, toName, relationType) triples.

    Names are resolved across *every* entity status, not just `active`. An edge
    onto a superseded symbol is still in the projection until it is invalidated,
    and naming it is the whole point of the retraction assertions — resolving
    names from active entities only would silently drop exactly those edges and
    make "the edge is gone" unfalsifiable.
    """
    by_id = {e.id: e.name for e in store.list_entities(_all_statuses())}
    live: set[tuple[str, str, str]] = set()
    for doc in store.list_relation_docs():
        from_name, to_name = by_id.get(doc["from"]), by_id.get(doc["to"])
        if from_name is not None and to_name is not None:
            live.add((from_name, to_name, doc["relationType"]))
    return live


def entity_by_name(store: FalkorGraphStore, name: str) -> Any:
    return next(e for e in store.list_entities() if e.name == name)


# `transfer` is kept byte-identical, at the same line range, so the diff has an
# unchanged entity in it; `onboard` and the import both change.
REWRITTEN_SERVICE = '''"""Transfer service built on the domain models."""

from src.policy import allows


def transfer(source: Account, target: Account, amount: float) -> None:
    source.balance -= amount
    target.deposit(amount)


def onboard(name: str) -> bool:
    return allows(1.0)


# NOTE: transfers are not atomic yet; the fix is tracked in ADR-0011.
'''


# A module nothing in the fixture references yet, plus the rewrite of an
# *importer* of it. Committed separately so a diff can name one without the
# other, which is how an inbound cross-file edge gets tested.
AUDIT_MODULE = '''"""Audit helpers."""


def audit(amount: float) -> bool:
    """Check one amount."""
    return amount > 0
'''

SERVICE_USING_AUDIT = '''"""Transfer service built on the audit module."""

from src.audit import audit


def onboard(name: str) -> bool:
    return audit(1.0)
'''


# =============================================================================
# Store: relation invalidation (bi-temporal close-out)
# =============================================================================


def test_invalidate_relation_removes_it_from_the_projection(store: FalkorGraphStore) -> None:
    a = store.create_entity(
        EntityCreate.model_validate({"name": "A", "entityType": "concept", "observations": []})
    )
    b = store.create_entity(
        EntityCreate.model_validate({"name": "B", "entityType": "concept", "observations": []})
    )
    store.create_relation(
        RelationCreate.model_validate({"from": a.id, "to": b.id, "relationType": "calls"})
    )

    invalidated = store.invalidate_relation(a.id, b.id, "calls")

    assert invalidated.from_ == a.id
    assert store.read_relation(a.id, b.id, "calls") is None
    assert store.list_relations() == []


def test_invalidate_relation_keeps_history(
    multi: MultiGraph, store: FalkorGraphStore, db: FalkorDB, namespace: str
) -> None:
    a = store.create_entity(
        EntityCreate.model_validate({"name": "A", "entityType": "concept", "observations": []})
    )
    b = store.create_entity(
        EntityCreate.model_validate({"name": "B", "entityType": "concept", "observations": []})
    )
    relation = store.create_relation(
        RelationCreate.model_validate({"from": a.id, "to": b.id, "relationType": "calls"})
    )

    store.invalidate_relation(a.id, b.id, "calls")

    result = db.select_graph(f"{namespace}:graph:{GRAPH}").query(
        "MATCH (v:_RelationVersion) RETURN v.relation_id, v._doc, v.tx_from, v.tx_to"
    )
    assert len(result.result_set) == 1
    relation_id, doc, tx_from, tx_to = result.result_set[0]
    assert relation_id == relation.id
    assert json.loads(doc)["id"] == relation.id
    assert tx_from == relation.created_at
    assert tx_to >= tx_from

    events = multi.event_log(GRAPH).read_all()
    assert events[-1].type == "relation_invalidated"
    assert events[-1].payload["relation"]["id"] == relation.id


def test_edges_helper_still_names_edges_onto_superseded_entities(
    store: FalkorGraphStore,
) -> None:
    """The retraction assertions below are only meaningful if this holds."""
    a = store.create_entity(
        EntityCreate.model_validate({"name": "A", "entityType": "concept", "observations": []})
    )
    b = store.create_entity(
        EntityCreate.model_validate({"name": "B", "entityType": "concept", "observations": []})
    )
    store.create_relation(
        RelationCreate.model_validate({"from": a.id, "to": b.id, "relationType": "calls"})
    )
    store.update_entity(b.id, {"status": "superseded", "statusReason": "source_retracted"})

    assert "B" not in names(store)
    assert ("A", "B", "calls") in edges(store)

    store.invalidate_relation(a.id, b.id, "calls")
    assert ("A", "B", "calls") not in edges(store)


def test_invalidate_missing_relation_raises_not_found(store: FalkorGraphStore) -> None:
    a = store.create_entity(
        EntityCreate.model_validate({"name": "A", "entityType": "concept", "observations": []})
    )
    with pytest.raises(NotFoundError):
        store.invalidate_relation(a.id, a.id, "calls")


# =============================================================================
# Relation diffing on a changed file
# =============================================================================


def test_no_changes_returns_the_empty_result(seeded: Path, multi: MultiGraph) -> None:
    result = update(seeded, multi, git_ref="HEAD..HEAD")
    assert result["changedFiles"] == []
    assert result["entityDiffs"] == []
    assert result["changedEntityIds"] == []
    assert set(result["stats"].values()) == {0}


def test_exclude_keeps_a_changed_file_out_of_the_diff(
    seeded: Path, multi: MultiGraph, store: FalkorGraphStore
) -> None:
    (seeded / "src" / "service.py").write_text(REWRITTEN_SERVICE, encoding="utf-8")
    commit(seeded, "rewrite the service on top of policy")

    before = names(store)
    result = update(seeded, multi, exclude=["src/*"])

    assert result["changedFiles"] == []
    assert names(store) == before


def test_changed_file_relations_are_rediffed(
    seeded: Path, multi: MultiGraph, store: FalkorGraphStore
) -> None:
    (seeded / "src" / "service.py").write_text(REWRITTEN_SERVICE, encoding="utf-8")
    commit(seeded, "rewrite the service on top of policy")

    result = update(seeded, multi)

    live = edges(store)
    # Edges the rewritten file no longer states are gone...
    assert ("onboard (service)", "open_account (models)", "calls") not in live
    assert ("file:src/service.py", "file:src/models.py", "requires") not in live
    # ...and the ones it now states are there.
    assert ("onboard (service)", "allows (policy)", "calls") in live
    assert ("file:src/service.py", "file:src/policy.py", "requires") in live
    # Edges sourced from untouched files are left exactly as they were.
    assert ("Reporter.summarize (index)", "formatBalance (helper)", "calls") in live
    assert ("open_account (models)", "Account (models)", "calls") in live

    assert result["stats"]["relationsRemoved"] == 2
    assert result["stats"]["relationsCreated"] == 2
    assert result["stats"]["entitiesRetracted"] == 0


def test_semantic_layer_edges_survive_a_structural_update(
    seeded: Path, multi: MultiGraph, store: FalkorGraphStore
) -> None:
    """The enricher's `related_to` links are nobody's re-extraction to retract."""
    pattern = store.create_entity(
        EntityCreate.model_validate(
            {
                "name": "Service layer pattern",
                "entityType": "pattern",
                "observations": ["map_layer: semantic"],
            }
        )
    )
    service_file = entity_by_name(store, "file:src/service.py")
    onboard = entity_by_name(store, "onboard (service)")
    for from_id, to_id in ((pattern.id, onboard.id), (service_file.id, pattern.id)):
        store.create_relation(
            RelationCreate.model_validate(
                {"from": from_id, "to": to_id, "relationType": "related_to"}
            )
        )

    (seeded / "src" / "service.py").write_text(REWRITTEN_SERVICE, encoding="utf-8")
    commit(seeded, "rewrite the service on top of policy")

    result = update(seeded, multi)

    live = edges(store)
    assert ("Service layer pattern", "onboard (service)", "related_to") in live
    assert ("file:src/service.py", "Service layer pattern", "related_to") in live
    assert result["stats"]["relationsRemoved"] == 2


def test_a_hand_authored_reference_into_changed_code_survives(
    seeded: Path, multi: MultiGraph, store: FalkorGraphStore
) -> None:
    """`references` is diffed only where the doc-link pass emits it.

    Structural extraction states `references` from a *doc file* and nowhere
    else, so an agent's or a user's `references` edge into code — here a claim
    about the service — is nobody's re-extraction to retract, exactly like
    `related_to`.
    """
    claim = store.create_entity(
        EntityCreate.model_validate(
            {
                "name": "Transfers must check the policy ceiling",
                "entityType": "claim",
                "observations": ["map_layer: semantic"],
            }
        )
    )
    service_file = entity_by_name(store, "file:src/service.py")
    store.create_relation(
        RelationCreate.model_validate(
            {"from": claim.id, "to": service_file.id, "relationType": "references"}
        )
    )

    (seeded / "src" / "service.py").write_text(REWRITTEN_SERVICE, encoding="utf-8")
    commit(seeded, "rewrite the service on top of policy")

    result = update(seeded, multi)

    live = edges(store)
    assert ("Transfers must check the policy ceiling", "file:src/service.py", "references") in live
    assert result["stats"]["relationsRemoved"] == 2


def test_a_new_mention_resolves_to_a_superseded_entity_sharing_its_name(
    seeded: Path, multi: MultiGraph, store: FalkorGraphStore
) -> None:
    """A relation naming an entity this diff doesn't otherwise touch, and that
    isn't currently active (superseded for some unrelated reason before this
    update ran), resolves with the same active-preferred name->id tie-break
    bulk import uses for its own relation resolution — rather than being
    silently dropped because name_to_id only ever saw active entities."""
    target = entity_by_name(store, "transfer (service)")
    store.update_entity(target.id, {"status": "superseded", "statusReason": "duplicate"})

    doc = seeded / "docs" / "architecture.md"
    doc.write_text(doc.read_text(encoding="utf-8") + "\nSee also `transfer()`.\n", encoding="utf-8")
    commit(seeded, "mention transfer in the docs")

    update(seeded, multi)

    assert ("file:docs/architecture.md", "transfer (service)", "references") in edges(store)


def test_editing_a_doc_retracts_the_mentions_it_dropped(
    seeded: Path, multi: MultiGraph, store: FalkorGraphStore
) -> None:
    doc = seeded / "docs" / "architecture.md"
    doc.write_text(
        "# Architecture\n\nThe domain models live in src/models.py.\n",
        encoding="utf-8",
    )
    commit(seeded, "trim the architecture doc")

    update(seeded, multi)

    live = edges(store)
    assert ("file:docs/architecture.md", "file:src/models.py", "references") in live
    assert ("file:docs/architecture.md", "file:src/service.py", "references") not in live
    assert ("file:docs/architecture.md", "open_account (models)", "references") not in live


def test_deleted_file_supersedes_its_entities_and_edges(
    seeded: Path, multi: MultiGraph, store: FalkorGraphStore
) -> None:
    (seeded / "lib" / "helper.js").unlink()
    commit(seeded, "drop the helper module")

    result = update(seeded, multi)

    assert names(store).isdisjoint(
        {"file:lib/helper.js", "formatBalance (helper)", "roundCents (helper)"}
    )
    superseded = [
        e
        for e in store.list_entities(_all_statuses())
        if e.name in {"file:lib/helper.js", "formatBalance (helper)", "roundCents (helper)"}
    ]
    assert len(superseded) == 3
    assert {e.status for e in superseded} == {"superseded"}
    assert {e.status_reason for e in superseded} == {"source_retracted"}
    assert {e.change_reason for e in superseded} == {"file deleted"}

    assert result["stats"]["entitiesRetracted"] == 3
    # Both part_of edges the deleted file sourced, plus both cross-file edges
    # pointing *into* it from the untouched lib/index.ts, are closed out.
    assert result["stats"]["relationsRemoved"] == 4
    assert ("formatBalance (helper)", "file:lib/helper.js", "part_of") not in edges(store)
    assert {d["status"] for d in result["entityDiffs"]} == {"superseded"}


def test_deleted_file_retracts_edges_pointing_into_it(
    seeded: Path, multi: MultiGraph, store: FalkorGraphStore
) -> None:
    """Inbound cross-file edges are the deleted file's business too.

    `lib/index.ts` is untouched by the diff, but both of its edges into
    `lib/helper.js` describe code that no longer exists. Leaving them would
    dangle live edges onto superseded entities forever.
    """
    inbound = {
        ("file:lib/index.ts", "file:lib/helper.js", "requires"),
        ("Reporter.summarize (index)", "formatBalance (helper)", "calls"),
    }
    assert inbound <= edges(store)

    (seeded / "lib" / "helper.js").unlink()
    commit(seeded, "drop the helper module")

    update(seeded, multi)

    assert edges(store).isdisjoint(inbound)


def test_added_file_gains_edges_pointing_into_it(
    seeded: Path, multi: MultiGraph, store: FalkorGraphStore
) -> None:
    """The inverse: a new module's inbound edges are created, not dropped.

    The importer (`src/service.py`) is rewritten in its own commit, so the diff
    under test names only `src/audit.py` — yet the fresh whole-project
    resolution pass states edges from the untouched importer into the new file.
    """
    (seeded / "src" / "audit.py").write_text(AUDIT_MODULE, encoding="utf-8")
    commit(seeded, "add the audit module")
    (seeded / "src" / "service.py").write_text(SERVICE_USING_AUDIT, encoding="utf-8")
    commit(seeded, "rewrite the service on top of audit")

    result = update(seeded, multi, git_ref="HEAD~2..HEAD~1")

    assert result["changedFiles"] == ["src/audit.py"]
    live = edges(store)
    assert ("file:src/service.py", "file:src/audit.py", "requires") in live
    assert ("onboard (service)", "audit (audit)", "calls") in live


def test_removed_symbol_in_a_changed_file_is_superseded(
    seeded: Path, multi: MultiGraph, store: FalkorGraphStore
) -> None:
    policy = seeded / "src" / "policy.py"
    policy.write_text(
        '"""Policy checks."""\n\nMAX_TRANSFER = 10000.0\n\n\n'
        "def under_review(amount: float) -> str:\n"
        '    """Return the review state for an amount."""\n'
        '    return "under_review" if amount > MAX_TRANSFER else "cleared"\n',
        encoding="utf-8",
    )
    commit(seeded, "drop the allows helper")

    result = update(seeded, multi)

    assert "allows (policy)" not in names(store)
    gone = next(e for e in store.list_entities(_all_statuses()) if e.name == "allows (policy)")
    assert gone.status == "superseded"
    assert gone.status_reason == "outdated_knowledge"
    assert gone.change_reason == "code changed"
    assert result["stats"]["entitiesRetracted"] == 1
    assert ("allows (policy)", "file:src/policy.py", "part_of") not in edges(store)


def test_added_file_lands_with_full_provenance(
    seeded: Path, multi: MultiGraph, store: FalkorGraphStore
) -> None:
    (seeded / "src" / "audit.py").write_text(
        '"""Audit helpers."""\n\nfrom src.policy import allows\n\n\n'
        "def audit(amount: float) -> bool:\n"
        '    """Check one amount."""\n'
        "    return allows(amount)\n",
        encoding="utf-8",
    )
    commit(seeded, "add the audit module")

    result = update(seeded, multi)

    audit = entity_by_name(store, "audit (audit)")
    assert audit.provenance is not None
    assert audit.provenance.extractor == "tree-sitter"
    assert audit.provenance.extraction_method == "tree-sitter"
    assert audit.provenance.external_ref == "src/audit.py:6"
    assert audit.confidence is not None
    assert audit.confidence.score == 1.0
    assert any(obs.startswith("docstring: ") for obs in audit.observations)

    live = edges(store)
    assert ("audit (audit)", "file:src/audit.py", "part_of") in live
    assert ("file:src/audit.py", "file:src/policy.py", "requires") in live
    assert ("audit (audit)", "allows (policy)", "calls") in live
    assert result["stats"]["entitiesCreated"] == 2
    assert result["stats"]["relationsCreated"] == 3


def test_changed_entity_ids_cover_every_write(
    seeded: Path, multi: MultiGraph, store: FalkorGraphStore
) -> None:
    (seeded / "src" / "service.py").write_text(REWRITTEN_SERVICE, encoding="utf-8")
    (seeded / "lib" / "helper.js").unlink()
    (seeded / "src" / "audit.py").write_text("def audit():\n    return 1\n", encoding="utf-8")
    commit(seeded, "modify, delete and add in one commit")

    result = update(seeded, multi)

    stats = result["stats"]
    expected = stats["entitiesCreated"] + stats["entitiesUpdated"] + stats["entitiesRetracted"]
    assert len(result["changedEntityIds"]) == expected
    assert len(set(result["changedEntityIds"])) == expected
    known = {e.id for e in store.list_entities(_all_statuses())}
    assert set(result["changedEntityIds"]) <= known
    assert stats["entitiesUnchanged"] >= 1


# =============================================================================
# Dry run
# =============================================================================


def test_dry_run_reports_the_real_plan_without_writing(
    seeded: Path, multi: MultiGraph, store: FalkorGraphStore
) -> None:
    (seeded / "src" / "service.py").write_text(REWRITTEN_SERVICE, encoding="utf-8")
    (seeded / "lib" / "helper.js").unlink()
    commit(seeded, "rewrite and delete")

    before_entities, before_edges = names(store), edges(store)
    planned = update(seeded, multi, dry_run=True)

    assert names(store) == before_entities
    assert edges(store) == before_edges

    applied = update(seeded, multi)
    assert planned["stats"] == applied["stats"]
    assert planned["entityDiffs"] == applied["entityDiffs"]
    assert planned["changedFiles"] == applied["changedFiles"]
    assert planned["stats"]["relationsRemoved"] > 0
    assert planned["stats"]["entitiesRetracted"] > 0


# =============================================================================
# Shrink guard
# =============================================================================


def test_guard_refuses_a_changed_file_that_extracts_to_nothing(
    seeded: Path, multi: MultiGraph, store: FalkorGraphStore
) -> None:
    (seeded / "src" / "models.py").write_bytes(b"\xff\xfe\x00binary garbage\x00")
    commit(seeded, "corrupt the models module")

    before = names(store)
    with pytest.raises(OperationError) as excinfo:
        update(seeded, multi)
    assert "src/models.py" in str(excinfo.value)
    assert "force" in str(excinfo.value)
    assert names(store) == before

    forced = update(seeded, multi, force=True)
    assert forced["stats"]["entitiesRetracted"] == 4
    assert "Account (models)" not in names(store)


def test_guard_refuses_superseding_more_than_half_the_graph(
    seeded: Path, multi: MultiGraph, store: FalkorGraphStore
) -> None:
    for path in ("src/models.py", "src/service.py", "src/policy.py", "lib/helper.js"):
        (seeded / path).unlink()
    commit(seeded, "delete most of the project")

    before = names(store)
    with pytest.raises(OperationError) as excinfo:
        update(seeded, multi)
    message = str(excinfo.value)
    assert "more than half" in message
    assert "force" in message
    assert names(store) == before

    forced = update(seeded, multi, force=True)
    assert forced["stats"]["entitiesRetracted"] == 14
    assert "file:lib/index.ts" in names(store)


def test_guard_is_not_diluted_by_the_semantic_layer(
    seeded: Path, multi: MultiGraph, store: FalkorGraphStore
) -> None:
    """A semantic layer sharing the graph must not buy the structural layer a
    free collapse: the guard measures against the entities an update *can*
    supersede, not against every active node in the graph."""
    for index in range(40):
        store.create_entity(
            EntityCreate.model_validate(
                {
                    "name": f"Pattern {index}",
                    "entityType": "pattern",
                    "observations": ["map_layer: semantic"],
                }
            )
        )

    for path in ("src/models.py", "src/service.py", "src/policy.py", "lib/helper.js"):
        (seeded / path).unlink()
    commit(seeded, "delete most of the project")

    before = names(store)
    with pytest.raises(OperationError) as excinfo:
        update(seeded, multi)
    assert "more than half" in str(excinfo.value)
    assert names(store) == before


def test_guard_runs_before_a_dry_run_reports(seeded: Path, multi: MultiGraph) -> None:
    (seeded / "src" / "models.py").write_bytes(b"\xff\xfe\x00binary garbage\x00")
    commit(seeded, "corrupt the models module")

    with pytest.raises(OperationError):
        update(seeded, multi, dry_run=True)


def test_guard_ignores_a_file_that_becomes_git_untracked_before_the_update(
    repo: Path, multi: MultiGraph, store: FalkorGraphStore
) -> None:
    """A file the diff names must be judged in scope by the *same* rule
    extraction itself uses (git tracking included), or a file that legitimately
    left scope between the named commits and the current working tree looks
    like a collapse and trips the guard for no reason."""
    tool = repo / "tools" / "gen.py"
    tool.parent.mkdir()
    tool.write_text(
        '"""Codegen tool."""\n\n\ndef generate() -> str:\n    return "gen"\n', encoding="utf-8"
    )
    commit(repo, "add the codegen tool")
    before_sha = git(repo, "rev-parse", "HEAD").strip()

    extraction = treesitter.extract_codebase(str(repo))
    bulk_import(
        BulkImportInput.model_validate(
            {
                "entities": extraction["entities"],
                "relations": extraction["relations"],
                "graph": GRAPH,
            }
        ),
        multi,
    )
    assert "generate (gen)" in names(store)

    tool.write_text(
        '"""Codegen tool."""\n\n\ndef generate() -> str:\n    return "generated"\n',
        encoding="utf-8",
    )
    commit(repo, "tweak the codegen tool")
    after_sha = git(repo, "rev-parse", "HEAD").strip()

    (repo / ".gitignore").write_text("tools/\n", encoding="utf-8")
    git(repo, "rm", "--cached", "tools/gen.py")
    commit(repo, "stop tracking the codegen tool")

    result = update(repo, multi, git_ref=f"{before_sha}..{after_sha}")

    assert result["stats"]["entitiesRetracted"] == 0
    assert "generate (gen)" in names(store)


def _all_statuses() -> EntityFilter:
    return EntityFilter.model_validate(
        {"statusFilter": ["active", "superseded", "deprecated", "retracted", "investigating"]}
    )
