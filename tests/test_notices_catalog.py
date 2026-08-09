"""``notices-catalog`` (desire 3): every notice code with its meaning and the
commands that can emit it, generated from source -- never a hand-maintained
list that can silently drift.

Two registry-walking tests carry the actual contract (desire 7):

- ``test_every_notice_literal_in_source_is_cataloged`` is a source-wide
  static scan: every ``notice("CODE", ...)`` call site anywhere in the
  ``theloom`` package must name a code that's a key in ``NOTICE_CATALOG``.
  This is what fails a build the moment a new code is introduced without a
  cataloged meaning -- independent of whether any test happens to exercise
  that code path at runtime.
- ``test_every_cataloged_code_is_reachable_from_some_command`` is the
  reachability half: every documented code must be emitted somewhere
  reachable from a real command's own handler, so a phantom entry (a
  meaning with no command behind it) fails loudly instead of shipping.

Together they make the catalog bidirectionally honest: nothing emitted goes
undocumented, and nothing documented goes unreachable.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import theloom
from theloom.cli.io import format_success
from theloom.cli.notices_catalog import (
    EmptyInput,
    _notice_bound_names,
    build_catalog,
    notices_catalog,
)
from theloom.cli.registry import _BY_NAME as REGISTRY_BY_NAME
from theloom.cli.registry import run_handler
from theloom.operations.notices import NOTICE_CATALOG


def _all_literal_notice_codes() -> set[str]:
    """Every string literal passed as the first argument to a call of
    whatever local name a module binds to ``notice`` -- a plain AST walk
    over every file in the package, not limited to top-level functions or to
    commands the registry can reach (unlike ``build_catalog``'s deliberately
    narrower reachability walk)."""
    root = Path(theloom.__file__).parent
    codes: set[str] = set()
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        notice_names = _notice_bound_names(tree)
        if not notice_names:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id not in notice_names or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                codes.add(first.value)
    return codes


def test_every_notice_literal_in_source_is_cataloged() -> None:
    literal_codes = _all_literal_notice_codes()
    # Pin: if this drops to zero the scanner broke, not the codebase --
    # today's known emission sites (ALREADY_REAPED, AUTO_SCOPED, DRY_RUN,
    # EMPTY_TRAVERSAL, NONE_PERSISTED, NOT_PERSISTED, PARAMETER_IGNORED,
    # TRUNCATED) must always be found.
    assert literal_codes, "expected to find at least the known notice call sites"
    uncataloged = literal_codes - set(NOTICE_CATALOG)
    assert not uncataloged, (
        f"notice code(s) used in source but missing from NOTICE_CATALOG "
        f"(theloom/operations/notices.py): {sorted(uncataloged)}"
    )


def test_every_cataloged_code_is_reachable_from_some_command() -> None:
    catalog = {row["code"]: row for row in build_catalog()}
    assert set(catalog) == set(NOTICE_CATALOG)
    unreachable = sorted(code for code, row in catalog.items() if not row["commands"])
    assert not unreachable, (
        f"notice code(s) cataloged but not reachable from any command's own handler: {unreachable}"
    )


def test_catalog_matches_known_emission_sites() -> None:
    """Pins the exact code -> commands mapping for the emission sites this
    branch is built on, so a regression in the call-graph walk (missing an
    edge, following a spurious one) is caught even though the two tests
    above would only notice a total miss, not a wrong attribution."""
    catalog = {row["code"]: set(row["commands"]) for row in build_catalog()}
    # ALREADY_REAPED is the ref-lifecycle notice RefRegistry's docstring
    # anticipated being shared: session workspaces and belief worlds are
    # both kinds of the same generic ref, and both report it the same way.
    assert catalog["ALREADY_REAPED"] == {"end-session", "abandon-world"}
    assert catalog["AUTO_SCOPED"] == {"verify-fidelity"}
    assert catalog["CONTESTED_ON_MERGE"] == {"merge-world"}
    assert catalog["DRY_RUN"] == {"propagate-credit", "run-inference"}
    assert catalog["EMPTY_TRAVERSAL"] == {"semiring-distances"}
    assert catalog["NONE_PERSISTED"] == {"list-loops"}
    assert catalog["NOT_PERSISTED"] == {"detect-loops"}
    assert catalog["TRUNCATED"] == {"list-entities"}
    # Checked inventory, not memory: every command that reads/writes state a
    # world's overlay cannot fork -- embeddings (vectors, a bare Cypher
    # property SET) and graph-level metadata (:_GraphMeta, no event at all).
    # Re-grepped for Part 5's write-path audit (round 3): find-clusters/
    # semantic-gaps/resolve-gaps (all rank or filter by entity vector via
    # theloom.operations.semantic's shared _search_similar) and the three
    # composites whose own sections touch vectors directly --
    # far-analogy-retrieval (get_entity_vectors), explore-frontier
    # (CoverageGap), hypothesis-engine (its 'gaps' section calls
    # semantic_gaps) -- were missing from this set.
    assert catalog["WORLD_PROJECTION_PARTIAL"] == {
        "embed-entity",
        "embed-entities",
        "embedding-reconcile",
        "embedding-status",
        "semantic-search",
        "hybrid-search",
        "semantic-neighbors",
        "suggest-relations",
        "find-clusters",
        "semantic-gaps",
        "resolve-gaps",
        "session-changelog",
        "postmortem-evaluate",
        "trigger-status",
        "process-triggers",
        "self-model-update",
        "far-analogy-retrieval",
        "explore-frontier",
        "hypothesis-engine",
    }
    # documents.py's graph-ignored notice is shared by every document command
    # that accepts (and ignores) `graph` -- pin the full set so a command
    # silently dropped from that list is caught.
    assert catalog["PARAMETER_IGNORED"] == {
        "ingest-document",
        "ingest-directory",
        "ingest-url",
        "ingest-content",
        "list-documents",
        "delete-document",
        "reingest-document",
        "analyze-category",
    }


def test_composites_that_call_into_notice_emitting_handlers_are_not_credited() -> None:
    """graph-reconnaissance and entity-deep-dive call detect-loops internally
    (persist=False) but never forward its notices into their own response --
    crediting them with NOT_PERSISTED would be a false claim about what a
    caller of those composites can actually observe (desire 7's whole
    point: the surface must not promise more than the code does)."""
    catalog = {row["code"]: set(row["commands"]) for row in build_catalog()}
    assert "graph-reconnaissance" not in catalog["NOT_PERSISTED"]
    assert "entity-deep-dive" not in catalog["NOT_PERSISTED"]
    assert "simulate-change" not in catalog["NOT_PERSISTED"]


def test_every_catalog_meaning_is_nonempty_prose() -> None:
    for code, meaning in NOTICE_CATALOG.items():
        assert isinstance(meaning, str) and meaning.strip(), code


# =============================================================================
# The command itself: envelope, schema, read-only-ness
# =============================================================================


def test_notices_catalog_command_is_registered_read_only_and_allow_empty() -> None:
    descriptor = REGISTRY_BY_NAME["notices-catalog"]
    assert descriptor.allow_empty is True
    assert descriptor.input_model is EmptyInput


def test_notices_catalog_handler_returns_the_uniform_envelope() -> None:
    result = notices_catalog(EmptyInput(), None)  # type: ignore[arg-type]
    assert set(result) == {"items", "count"}
    assert result["count"] == len(result["items"]) == len(NOTICE_CATALOG)
    codes = {row["code"] for row in result["items"]}
    assert codes == set(NOTICE_CATALOG)
    for row in result["items"]:
        assert set(row) == {"code", "meaning", "commands"}
        assert isinstance(row["commands"], list)
        assert row["commands"] == sorted(row["commands"])


def test_notices_catalog_survives_run_handler_with_no_receipts() -> None:
    """A pure read: run_handler must never attach eventIds (desire 1's
    receipts convention is additive-only, and this command commits nothing)."""
    result = run_handler("notices-catalog", {}, multi=object())  # type: ignore[arg-type]
    assert "eventIds" not in result
    json.loads(format_success(result))  # survives the CLI's own JSON formatter


def test_notices_catalog_rejects_unknown_extra_fields_gracefully() -> None:
    """EmptyInput strips unknown keys (CommandInput's extra="ignore"), so an
    unrelated key never raises -- consistent with every other allow_empty
    command in the registry."""
    result = run_handler("notices-catalog", {"bogus": True}, multi=object())  # type: ignore[arg-type]
    assert result["count"] == len(NOTICE_CATALOG)


def test_notices_catalog_is_in_the_contract_category() -> None:
    descriptor = REGISTRY_BY_NAME["notices-catalog"]
    assert descriptor.category == "Contract"


@pytest.mark.parametrize("code", sorted(NOTICE_CATALOG))
def test_each_cataloged_code_appears_exactly_once_in_the_catalog(code: str) -> None:
    catalog = build_catalog()
    matches = [row for row in catalog if row["code"] == code]
    assert len(matches) == 1
