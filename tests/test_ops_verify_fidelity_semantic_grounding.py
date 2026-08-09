"""Desire 10 (claude-desires.md), exercised through the real command surface
(``theloom.operations.synthesis.verify_fidelity`` — what ``loom
verify-fidelity`` actually calls), not just fidelity's internal core. Same
scenario as tests/test_synthesis_fidelity_semantic_grounding.py: a paraphrase
that reuses one word from the entity name must ground, and a coincidental
word-overlap claim sharing that SAME word with a different entity must not.

Round 5: when a word-overlap candidate's entity carries real observations
(as both entities below do, matching how a real Loom entity is actually
created), the sense anchor — built from observations ALONE, no entity name
(theloom/semantic/landscape.py's own "one-sided cut" docstring section) —
compared against the INTACT candidate span (never stripped) is the deciding
check, not the round-3 name-based dual z-score. This test mocks
``theloom.semantic.landscape.measure_specificity`` AND
``measure_sense_specificity`` directly rather than hand-deriving a full
probe-corpus calibration, the same simplification
tests/test_synthesis_fidelity_semantic_grounding.py uses and explains in its
own module docstring.

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

FEEDBACK_OBSERVATION = (
    "corrections that arrive only after the harm from a slow feedback loop is done"
)
SILENT_OBSERVATION = "a malfunction that produces no visible error, log entry, or alert"
PARAPHRASE_SENTENCE = "The lag in the feedback loop meant corrections always arrived too late."
FALSE_FRIEND_SENTENCE = "The orchestra performed a silent movie score."
TEXT = f"{PARAPHRASE_SENTENCE} {FALSE_FRIEND_SENTENCE}"


def _entity(name: str, observation: str) -> EntityCreate:
    return EntityCreate.model_validate(
        {"name": name, "entityType": "concept", "observations": [observation]}
    )


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


# One reserved axis pair per entity (name axis, sense-anchor axis) so
# unrelated (entity, span) pairs stay exactly orthogonal instead of
# accidentally sharing a "spare" axis with a different entity's engineered
# vector. The NAME axes exist only because _semantic_grounding always
# computes the round-3 name-based vectors upfront for every entity, even
# when (as here) both entities carry observations and the sense anchor
# ends up deciding instead.
_DIM = 10
_FEEDBACK_NAME_AXIS = 0
_FEEDBACK_SENSE_AXIS = 2
_SILENT_NAME_AXIS = 4
_SILENT_SENSE_AXIS = 6


def _build_vectors() -> dict[str, list[float]]:
    all_axes = (_FEEDBACK_NAME_AXIS, _FEEDBACK_SENSE_AXIS, _SILENT_NAME_AXIS, _SILENT_SENSE_AXIS)
    vectors: dict[str, list[float]] = {
        "distractor one": _shared_cosine_vec(_DIM, all_axes, 0.05, 8),
        "distractor two": _shared_cosine_vec(_DIM, all_axes, 0.15, 8),
    }

    def unit(axis: int) -> list[float]:
        v = [0.0] * _DIM
        v[axis] = 1.0
        return v

    vectors["Feedback Delay"] = unit(_FEEDBACK_NAME_AXIS)
    vectors["[concept] Feedback Delay"] = unit(_FEEDBACK_NAME_AXIS)
    # Keyed by the OBSERVATION-ONLY anchor text (round 5) -- no
    # "Feedback Delay: " prefix at all.
    vectors[f"{FEEDBACK_OBSERVATION}."] = unit(_FEEDBACK_SENSE_AXIS)
    # Genuine paraphrase reusing "feedback": compared INTACT (round 5 -- no
    # stripping on the sense-anchor path) against the anchor, clearing the
    # (mocked) sense cutoff.
    vectors[PARAPHRASE_SENTENCE] = _vec(_DIM, _FEEDBACK_SENSE_AXIS, 0.9, 1)

    vectors["Silent Failure Mode"] = unit(_SILENT_NAME_AXIS)
    vectors["[concept] Silent Failure Mode"] = unit(_SILENT_NAME_AXIS)
    vectors[f"{SILENT_OBSERVATION}."] = unit(_SILENT_SENSE_AXIS)
    # False friend sharing "silent": compared INTACT against the anchor,
    # collapsing well below the cutoff even without any stripping.
    vectors[FALSE_FRIEND_SENTENCE] = _vec(_DIM, _SILENT_SENSE_AXIS, 0.05, 3)
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


@pytest.fixture(autouse=True)
def _mocked_sense_specificity(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(embedder: object, **kwargs: object) -> _FakeSpecificityProfile:
        return _FakeSpecificityProfile(0.5)

    monkeypatch.setattr(landscape, "measure_sense_specificity", fake)


def test_verify_fidelity_command_grounds_paraphrase_and_rejects_word_overlap(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = multi.get_store()
    feedback = store.create_entity(_entity("Feedback Delay", FEEDBACK_OBSERVATION))
    silent = store.create_entity(_entity("Silent Failure Mode", SILENT_OBSERVATION))
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
    assert feedback_grounding["zCutoff"] == 0.5
    # Decided by the sense anchor, not the round-3 dual check.
    assert feedback_grounding["asymZScore"] is None
    assert feedback_grounding["asymZCutoff"] is None

    silent_grounding = by_id[silent.id]
    assert silent_grounding["status"] == "omitted"
    # Round 5 disclosure: an omitted decision still names the mechanism
    # ATTEMPTED (the sense anchor, since this entity carries observations)
    # and carries its full evidence -- "an honest no must be as auditable
    # as a yes" -- rather than nulling everything out.
    assert silent_grounding["matchBasis"] == "semantic"
    assert isinstance(silent_grounding["matchScore"], float)
    assert isinstance(silent_grounding["zScore"], float)
    assert silent_grounding["zCutoff"] == 0.5

    assert result["scores"]["entityGroundingRate"] == pytest.approx(0.5)
