"""Registry-walking test for desire 9 (one envelope): NO command in the
registry may return a bare top-level JSON array. ``list-loops`` used to
return ``{count, loops}``, ``list-documents`` a bare array with notices
smeared onto every item, ``propagate-credit`` a top-level array — three
attachment strategies for the same "this is a list of things" shape. Every
list-returning command now uses ``{items, count, notices?}``
(``theloom.operations.notices.list_envelope``).

Two layers, not one:

- ``theloom.cli.registry.run_handler`` itself asserts a handler's return
  value is never a bare ``list`` (see its docstring) — a permanent, always-on
  regression guard, not just a test-time check. This test cannot pass by
  accident: if the guard did not exist, a future command that regresses to a
  bare array would only be caught here, and only for the commands this test
  actually reaches.
- This test walks every command in ``COMMANDS`` and drives it through the
  real dispatch path (``run_handler``, not the handler function directly),
  against a graph seeded with a little real data so list-shaped responses
  have something in them to check, not just the trivially-true empty case.

A command that needs input this test doesn't supply raises a typed
``LoomError`` (VALIDATION_ERROR/INPUT_REQUIRED/NOT_FOUND) — expected and
skipped; the point is not "every command succeeds with `{}`", it's "whichever
ones do succeed never hand back a bare array". A handful of commands are
excluded outright because invoking them for real would make this test slow,
network-dependent, or side-effecting outside the test graph (see
``_EXCLUDED``); none of them return list-shaped output, so nothing about the
invariant goes unchecked by excluding them.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from tests.fakes import FakeEmbedder
from theloom import config as config_module
from theloom.cli.registry import COMMANDS, run_handler
from theloom.errors import LoomError
from theloom.semantic.embed import EMBEDDING_DIMENSIONS
from theloom.store.multigraph import MultiGraph

# Some embedding call sites (e.g. embed-entities) create the vector index at
# the model's declared width rather than inferring it from the first vector
# written, so the fake vector must match it or a later command's real query
# vector collides with a differently-sized index.
_FAKE_VECTOR = [1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1)

# Excluded from the walk — none are list-returning, so skipping them loses no
# coverage of the invariant this test exists to prove:
#   - serve: starts a live server and blocks forever.
#   - self-model-update: a full tree-sitter scan of this repo's own codebase
#     (allow_empty=True, defaults to scanning THE_LOOM itself) — correct but
#     far too slow for a per-command smoke pass.
#   - visualize: writes an HTML file to ./loom-viz/<graph>.html in the
#     working directory when `output` is omitted, and requires the built
#     tapestry frontend template — a filesystem side effect this test must
#     not have.
_EXCLUDED = {"serve", "self-model-update", "visualize"}


@pytest.fixture()
def fake_embedder() -> Iterator[None]:
    """Every `get_embedder()` call site defers to this override (see
    tests/fakes.py) — installing it means the composites in the walk that
    touch embeddings (semantic-gaps, find-clusters, ...) exercise their real
    code path without downloading or running the real fastembed model."""
    config_module.set_embedder_override(FakeEmbedder(_FAKE_VECTOR))
    yield
    config_module.set_embedder_override(None)


def _seed(multi: MultiGraph) -> None:
    """A small, varied scene: enough for most list/read commands to have
    something real to return, without so much that the walk is slow."""
    from theloom.operations.entity import CreateEntityInput, create_entity
    from theloom.operations.relations import CreateRelationInput, create_relation

    def entity(**overrides: Any) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "name": overrides.pop("name"),
            "entityType": overrides.pop("entityType", "concept"),
            "observations": overrides.pop("observations", ["a seeded observation"]),
        }
        doc.update(overrides)
        return create_entity(CreateEntityInput.model_validate(doc), multi)

    def relation(from_id: str, to_id: str, relation_type: str, **overrides: Any) -> None:
        doc: dict[str, Any] = {
            "from": from_id,
            "to": to_id,
            "relationType": relation_type,
            "polarity": None,
            "strength": "moderate",
            "evidence": None,
        }
        doc.update(overrides)
        create_relation(CreateRelationInput.model_validate(doc), multi)

    source = entity(name="Seed Source", entityType="source")
    claim = entity(
        name="Seed Claim",
        entityType="claim",
        confidence={"score": 0.4, "basis": "single_source"},
    )
    evidence = entity(name="Seed Evidence", entityType="evidence")
    question = entity(name="Seed Question", entityType="question")
    concept_a = entity(name="Seed Concept A")
    concept_b = entity(name="Seed Concept B")

    relation(claim["id"], source["id"], "sources")
    relation(evidence["id"], claim["id"], "supports")
    relation(concept_a["id"], concept_b["id"], "causes", polarity="+", strength="strong")
    relation(question["id"], concept_a["id"], "requires")


def test_no_command_returns_a_bare_top_level_array(multi: MultiGraph, fake_embedder: None) -> None:
    _seed(multi)

    exercised = 0
    skipped_needed_input = 0
    for descriptor in COMMANDS:
        if descriptor.name in _EXCLUDED:
            continue
        try:
            result = run_handler(descriptor.name, {}, multi)
        except LoomError:
            skipped_needed_input += 1
            continue
        exercised += 1
        assert not isinstance(result, list), (
            f"command '{descriptor.name}' returned a bare top-level array"
        )

    # Sanity on the walk itself: a third of the registry is a conservative
    # floor for how much should be reachable with bare `{}` against the
    # seeded graph (most commands take entity/relation ids or other specific
    # arguments this test cannot guess) — low enough not to be brittle to
    # new commands, high enough that a regression collapsing the walk to
    # "nothing ran, nothing failed" would still trip it.
    assert exercised > len(COMMANDS) // 3
    assert skipped_needed_input < len(COMMANDS)
