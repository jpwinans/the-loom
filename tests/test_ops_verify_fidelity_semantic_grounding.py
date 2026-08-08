"""Desire 10 (claude-desires.md), exercised through the real command surface
(``theloom.operations.synthesis.verify_fidelity`` — what ``loom
verify-fidelity`` actually calls), not just fidelity's internal core. Same
scenario as tests/test_synthesis_fidelity_semantic_grounding.py: a paraphrase
that reuses one word from the entity name must ground, and a coincidental
word-overlap claim sharing that SAME word with a different entity must not —
both via the residual (word-stripped) check, since (round 2, blind critic
finding) the raw score alone clears the cutoff for both on this embedder's
real geometry.

Uses explicit ``entityIds`` throughout so this stays a pure grounding test —
TL-484's auto-scope/relevance-floor machinery (tests/test_synthesis_fidelity_
scoping.py) is exercised elsewhere and is untouched by desire 10.

Vectors are sized to the real measured geometry (cosine ~0.35 unrelated,
~0.6-0.7 word-inflated/related — see theloom/semantic/landscape.py's own
probe corpus), not an arbitrary 0.95, per the round-2 critic finding that
0.95 mocks proved nothing about the real path.
"""

from __future__ import annotations

import math

import pytest

from theloom.model import EntityCreate
from theloom.operations.synthesis import VerifyFidelityInput
from theloom.operations.synthesis import verify_fidelity as verify_fidelity_op
from theloom.semantic import landscape
from theloom.store.multigraph import MultiGraph

PARAPHRASE_SENTENCE = "The lag in the feedback loop meant corrections always arrived too late."
FALSE_FRIEND_SENTENCE = "The orchestra performed a silent movie score."
TEXT = f"{PARAPHRASE_SENTENCE} {FALSE_FRIEND_SENTENCE}"
FEEDBACK_STRIPPED = "The lag in the loop meant corrections always arrived too late."
SILENT_STRIPPED = "The orchestra performed a movie score."


def _entity(name: str) -> EntityCreate:
    return EntityCreate.model_validate({"name": name, "entityType": "concept", "observations": []})


class _MappedEmbedder:
    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self._vectors = vectors

    def embed_query(self, text: str) -> list[float]:
        return self._vectors[text]

    def embed_document(self, text: str) -> list[float]:
        return self._vectors[text]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vectors[t] for t in texts]


def _vec(dim: int, primary: int, cosine: float, secondary: int) -> list[float]:
    v = [0.0] * dim
    v[primary] = cosine
    v[secondary] = math.sqrt(max(0.0, 1 - cosine**2))
    return v


# One reserved axis pair per entity so unrelated (entity, span) pairs stay
# exactly orthogonal (cosine 0) instead of accidentally sharing a "spare"
# axis with a different entity's engineered vector.
_DIM = 6
_CALIBRATION_AXES = (0, 1)
_FEEDBACK_AXES = (2, 3)
_SILENT_AXES = (4, 5)


def _build_vectors() -> dict[str, list[float]]:
    calibration_primary, calibration_spare = _CALIBRATION_AXES
    feedback_primary, feedback_spare = _FEEDBACK_AXES
    silent_primary, silent_spare = _SILENT_AXES

    def unit(axis: int) -> list[float]:
        v = [0.0] * _DIM
        v[axis] = 1.0
        return v

    return {
        "cu": unit(calibration_primary),
        "cud": _vec(_DIM, calibration_primary, 0.35, calibration_spare),
        "cr": unit(calibration_primary),
        "crd": _vec(_DIM, calibration_primary, 0.70, calibration_spare),
        # "Feedback Delay": genuine paraphrase reusing "feedback". Raw
        # cosine 0.69, stripped of "feedback" still 0.58 -- survives.
        "Feedback Delay": unit(feedback_primary),
        PARAPHRASE_SENTENCE: _vec(_DIM, feedback_primary, 0.69, feedback_spare),
        FEEDBACK_STRIPPED: _vec(_DIM, feedback_primary, 0.58, feedback_spare),
        # "Silent Failure Mode": false friend sharing "silent". Raw cosine
        # 0.62 clears the cutoff alone; stripped of "silent" it collapses
        # to 0.20 -- the exact case the residual check exists to catch.
        "Silent Failure Mode": unit(silent_primary),
        FALSE_FRIEND_SENTENCE: _vec(_DIM, silent_primary, 0.62, silent_spare),
        SILENT_STRIPPED: _vec(_DIM, silent_primary, 0.20, silent_spare),
    }


@pytest.fixture(autouse=True)
def _tiny_corpus(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(landscape, "UNRELATED_PROBE_PAIRS", (("cu", "cud"),))
    monkeypatch.setattr(landscape, "RELATED_PROBE_PAIRS", (("cr", "crd"),))


def test_verify_fidelity_command_grounds_paraphrase_and_rejects_word_overlap(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = multi.get_store()
    feedback = store.create_entity(_entity("Feedback Delay"))
    silent = store.create_entity(_entity("Silent Failure Mode"))
    monkeypatch.setattr(
        "theloom.operations.synthesis.get_embedder",
        lambda: _MappedEmbedder(_build_vectors()),
    )

    result = verify_fidelity_op(
        VerifyFidelityInput(text=TEXT, entityIds=[feedback.id, silent.id]), multi
    )

    assert "notices" not in result  # explicit entityIds: the scoped path, no AUTO_SCOPED
    by_id = {g["entityId"]: g for g in result["entityGroundings"]}

    feedback_grounding = by_id[feedback.id]
    assert feedback_grounding["status"] == "grounded"
    assert feedback_grounding["matchBasis"] == "semantic"
    assert feedback_grounding["mentionedAs"] == PARAPHRASE_SENTENCE
    assert isinstance(feedback_grounding["matchScore"], float)

    silent_grounding = by_id[silent.id]
    assert silent_grounding["status"] == "omitted"
    assert silent_grounding["matchBasis"] is None

    assert result["scores"]["entityGroundingRate"] == pytest.approx(0.5)
