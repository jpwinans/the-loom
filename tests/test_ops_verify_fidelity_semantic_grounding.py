"""Desire 10 (claude-desires.md), exercised through the real command surface
(``theloom.operations.synthesis.verify_fidelity`` — what ``loom
verify-fidelity`` actually calls), not just fidelity's internal core. Same
scenario as tests/test_synthesis_fidelity_semantic_grounding.py: a paraphrase
that reuses one word from the entity name must ground, and a coincidental
word-overlap claim sharing that SAME word with a different entity must not.

Round 3: grounding is a RELATIVE (per-entity z-score, dual-representation)
decision — see theloom/synthesis/fidelity.py's own module docstring for the
round 1/2 failure history. This test mocks
``theloom.semantic.landscape.measure_specificity`` directly (both
representations) rather than hand-deriving a full probe-corpus calibration,
the same simplification tests/test_synthesis_fidelity_semantic_grounding.py
uses and explains in its own module docstring.

Uses explicit ``entityIds`` throughout so this stays a pure grounding test —
TL-484's auto-scope/relevance-floor machinery (tests/test_synthesis_fidelity_
scoping.py) is exercised elsewhere and is untouched by desire 10.
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


def _shared_cosine_vec(dim: int, axes: tuple[int, ...], cosine: float, spare: int) -> list[float]:
    v = [0.0] * dim
    for axis in axes:
        v[axis] = cosine
    v[spare] = math.sqrt(max(0.0, 1 - cosine**2 * len(axes)))
    return v


class _FakeSpecificityProfile:
    def __init__(self, cutoff: float) -> None:
        self.specificity_z_cutoff = cutoff


# One reserved axis per entity so unrelated (entity, span) pairs stay
# exactly orthogonal instead of accidentally sharing a "spare" axis with a
# different entity's engineered vector.
_DIM = 6
_FEEDBACK_AXIS = 0
_SILENT_AXIS = 2


def _build_vectors() -> dict[str, list[float]]:
    vectors: dict[str, list[float]] = {
        "distractor one": _shared_cosine_vec(_DIM, (_FEEDBACK_AXIS, _SILENT_AXIS), 0.05, 4),
        "distractor two": _shared_cosine_vec(_DIM, (_FEEDBACK_AXIS, _SILENT_AXIS), 0.15, 4),
    }
    feedback_unit = [0.0] * _DIM
    feedback_unit[_FEEDBACK_AXIS] = 1.0
    silent_unit = [0.0] * _DIM
    silent_unit[_SILENT_AXIS] = 1.0

    vectors["Feedback Delay"] = feedback_unit
    vectors["[concept] Feedback Delay"] = feedback_unit
    # Genuine paraphrase reusing "feedback": raw and residual both clear.
    vectors[PARAPHRASE_SENTENCE] = _vec(_DIM, _FEEDBACK_AXIS, 0.9, 1)
    vectors[FEEDBACK_STRIPPED] = _vec(_DIM, _FEEDBACK_AXIS, 0.85, 1)

    vectors["Silent Failure Mode"] = silent_unit
    vectors["[concept] Silent Failure Mode"] = silent_unit
    # False friend sharing "silent": raw clears, residual collapses.
    vectors[FALSE_FRIEND_SENTENCE] = _vec(_DIM, _SILENT_AXIS, 0.9, 3)
    vectors[SILENT_STRIPPED] = _vec(_DIM, _SILENT_AXIS, 0.05, 3)
    return vectors


@pytest.fixture(autouse=True)
def _tiny_battery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        landscape, "UNRELATED_PROBE_PAIRS", (("bg1", "distractor one"), ("bg2", "distractor two"))
    )


@pytest.fixture(autouse=True)
def _mocked_specificity(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(embedder: object, *, representation: str, **kwargs: object) -> _FakeSpecificityProfile:
        return _FakeSpecificityProfile(0.5)

    monkeypatch.setattr(landscape, "measure_specificity", fake)


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
    assert isinstance(feedback_grounding["zScore"], float)
    assert isinstance(feedback_grounding["asymZScore"], float)

    silent_grounding = by_id[silent.id]
    assert silent_grounding["status"] == "omitted"
    assert silent_grounding["matchBasis"] is None
    assert silent_grounding["zScore"] is None

    assert result["scores"]["entityGroundingRate"] == pytest.approx(0.5)
