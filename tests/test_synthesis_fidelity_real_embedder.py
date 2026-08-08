"""Desire 10 (claude-desires.md), round 2: at least one test that exercises
the REAL configured embedder (not a mock) on real paraphrase/word-overlap
cases, per the blind critic's finding that the mocked tests in
tests/test_synthesis_fidelity_semantic_grounding.py proved the mechanism was
wired correctly but said nothing about whether it behaves this way against
the actual model's geometry.

Deliberately its own file, with no autouse corpus-monkeypatching fixture:
tests/test_synthesis_fidelity_semantic_grounding.py's ``_tiny_corpus``
fixture patches ``theloom.semantic.landscape``'s probe corpus module-wide
for every test in that file, which would silently corrupt a real-embedder
test placed there too (the cutoff would be computed from placeholder
"cu"/"cud" strings instead of the production corpus). This file uses
:func:`theloom.semantic.embed.get_embedder` and
``theloom.semantic.landscape``'s PRODUCTION probe corpus, unmodified.

Skips (does not fail) if the real embedder can't be reached (fastembed not
installed, no network for the first-run model download, ...), so CI stays
green without ever needing this test's result; run it locally, as the
critic does, to see it actually exercise the model.
"""

from __future__ import annotations

import pytest

from theloom.config import set_embedder_override
from theloom.semantic.embed import get_embedder
from theloom.synthesis.fidelity import check_entity_grounding

FEEDBACK_ENTITY = {"id": "e-feedback", "name": "Feedback Delay"}
SILENT_ENTITY = {"id": "e-silent", "name": "Silent Failure Mode"}


@pytest.fixture()
def real_embedder() -> object:
    set_embedder_override(None)  # guarantee no leaked test double from another module
    embedder = get_embedder()
    try:
        embedder.embed_query("warm up the embedding model")
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"real embedder unavailable in this environment: {exc}")
    return embedder


def test_a_genuine_paraphrase_grounds_via_the_real_embedder(real_embedder: object) -> None:
    """The spec's own worked example, reworded (not the literal probe-corpus
    sentence, so this is a genuinely independent check): a faithful
    paraphrase of "Feedback Delay" that happens to retain the word
    "feedback" must ground, semantically, against the real model."""
    result = check_entity_grounding(
        "Engineers noticed the lag in the feedback loop long before anyone else did.",
        [FEEDBACK_ENTITY],
        None,
        real_embedder,  # type: ignore[arg-type]
    )

    assert result[0]["status"] == "grounded"
    assert result[0]["matchBasis"] == "semantic"
    assert isinstance(result[0]["matchScore"], float)


def test_a_single_shared_word_false_friend_does_not_ground_via_the_real_embedder(
    real_embedder: object,
) -> None:
    """The live bug report's own anecdote: an entity name containing
    "silent" must NOT be credited as grounded merely because an unrelated
    sentence also contains the word "silent"."""
    result = check_entity_grounding(
        "The orchestra performed a silent movie score.",
        [SILENT_ENTITY],
        None,
        real_embedder,  # type: ignore[arg-type]
    )

    assert result[0]["status"] == "omitted"
    assert result[0]["matchBasis"] is None


def test_both_in_one_call_via_the_real_embedder(real_embedder: object) -> None:
    """The exact regression shape end to end: one call, two entities, the
    paraphrase grounds and the word-overlap claim does not."""
    text = (
        "Engineers noticed the lag in the feedback loop long before anyone else did. "
        "The orchestra performed a silent movie score."
    )

    result = check_entity_grounding(
        text,
        [FEEDBACK_ENTITY, SILENT_ENTITY],
        None,
        real_embedder,  # type: ignore[arg-type]
    )

    by_id = {g["entityId"]: g for g in result}
    assert by_id[FEEDBACK_ENTITY["id"]]["status"] == "grounded"
    assert by_id[SILENT_ENTITY["id"]]["status"] == "omitted"
