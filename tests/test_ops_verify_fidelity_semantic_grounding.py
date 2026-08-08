"""Desire 10 (claude-desires.md), exercised through the real command surface
(``theloom.operations.synthesis.verify_fidelity`` — what ``loom
verify-fidelity`` actually calls), not just fidelity's internal core. Same
scenario as tests/test_synthesis_fidelity_semantic_grounding.py: a paraphrase
with no shared words must ground, an unrelated claim sharing one word with an
entity's name must not.

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

PARAPHRASE_SENTENCE = "There's a lag before the correction actually lands."
UNRELATED_SENTENCE = "The orchestra performed a silent movie score."
TEXT = f"{PARAPHRASE_SENTENCE} {UNRELATED_SENTENCE}"


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


def _build_vectors() -> dict[str, list[float]]:
    return {
        "cu": [1.0, 0.0, 0.0],
        "cud": _vec(3, 0, 0.1, 1),
        "cr": [1.0, 0.0, 0.0],
        "crd": _vec(3, 0, 0.9, 1),
        "Feedback Delay": [1.0, 0.0, 0.0],
        PARAPHRASE_SENTENCE: _vec(3, 0, 0.95, 2),
        "Silent Failure Mode": [0.0, 1.0, 0.0],
        UNRELATED_SENTENCE: _vec(3, 1, 0.05, 2),
        TEXT: [0.0, 0.0, 1.0],
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
