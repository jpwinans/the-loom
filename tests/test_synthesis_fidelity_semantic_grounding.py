"""Desire 10 (claude-desires.md): verify-fidelity's grounding matcher
replaces substring/word-overlap matching with SEMANTIC mention detection.

The bug this fixes failed in both directions at once: a faithful paraphrase
of an entity name ("the lag in the feedback loop" for "Feedback Delay")
scored zero grounding because it shares no literal words with the name,
while an unrelated claim got credited as grounded for sharing a single word
with the entity's name ("silent" observed live). These tests plant both
shapes with a controlled fake embedder and pin: the paraphrase grounds via
``matchBasis: "semantic"``, the single-shared-word claim does NOT ground,
the cutoff used is desire 8's live-measured landscape (not a second magic
number), grounding order matches input order, and the legacy
partial-word heuristic — the exact mechanism that produced the bug — only
still runs when no embedder is available at all (documented, not silently
reintroduced as the default).
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from theloom.semantic import landscape
from theloom.semantic.embed import cosine_similarity
from theloom.semantic.search import l2_similarity
from theloom.synthesis.fidelity import check_entity_grounding, verify_fidelity
from theloom.synthesis.llm import SynthesisLlmClient

FEEDBACK_ENTITY = {"id": "e-feedback", "name": "Feedback Delay"}
SILENT_ENTITY = {"id": "e-silent", "name": "Silent Failure Mode"}
PARAPHRASE_SENTENCE = "There's a lag before the correction actually lands."
UNRELATED_SENTENCE = "The orchestra performed a silent movie score."
TEXT = f"{PARAPHRASE_SENTENCE} {UNRELATED_SENTENCE}"

# A small, hand-picked corpus (swapped in for the production one via
# monkeypatch) so the cutoff used below is exactly computable rather than
# depending on the production probe corpus's own future edits.
_TINY_UNRELATED = (("cu", "cud"),)
_TINY_RELATED = (("cr", "crd"),)


class _MappedEmbedder:
    """Full per-text control over the vector returned, so specific cosine
    similarities can be engineered for each pair under test."""

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self._vectors = vectors

    def embed_query(self, text: str) -> list[float]:
        return self._vectors[text]

    def embed_document(self, text: str) -> list[float]:
        return self._vectors[text]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vectors[t] for t in texts]


def _vec(dim: int, primary: int, cosine: float, secondary: int) -> list[float]:
    """A unit vector in ``dim``-space whose cosine similarity to the unit
    vector along ``primary`` is exactly ``cosine`` (the rest of the mass on
    ``secondary``, so distinct pairs can be steered independently)."""
    v = [0.0] * dim
    v[primary] = cosine
    v[secondary] = math.sqrt(max(0.0, 1 - cosine**2))
    return v


def _build_vectors() -> dict[str, list[float]]:
    return {
        # Calibration corpus: axis 0 vs axis 1, in an isolated 3-vector.
        "cu": [1.0, 0.0, 0.0],
        "cud": _vec(3, 0, 0.1, 1),
        "cr": [1.0, 0.0, 0.0],
        "crd": _vec(3, 0, 0.9, 1),
        # Entities: e0 = "Feedback Delay" direction, e1 = "Silent Failure
        # Mode" direction, e2 = a spare axis so the whole-text catch-all
        # span can be orthogonal to BOTH entity directions at once.
        "Feedback Delay": [1.0, 0.0, 0.0],
        PARAPHRASE_SENTENCE: _vec(3, 0, 0.95, 2),
        "Silent Failure Mode": [0.0, 1.0, 0.0],
        UNRELATED_SENTENCE: _vec(3, 1, 0.05, 2),
        TEXT: [0.0, 0.0, 1.0],
    }


@pytest.fixture(autouse=True)
def _tiny_corpus(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(landscape, "UNRELATED_PROBE_PAIRS", _TINY_UNRELATED)
    monkeypatch.setattr(landscape, "RELATED_PROBE_PAIRS", _TINY_RELATED)


def _expected_cutoff() -> float:
    unrelated_score = l2_similarity(0.1)
    related_score = l2_similarity(0.9)
    return (unrelated_score + related_score) / 2


class TestParaphraseGrounds:
    def test_semantic_match_grounds_a_paraphrase_with_no_shared_words(self) -> None:
        embedder = _MappedEmbedder(_build_vectors())

        groundings = check_entity_grounding(TEXT, [FEEDBACK_ENTITY], None, embedder)

        assert len(groundings) == 1
        grounding = groundings[0]
        assert grounding["status"] == "grounded"
        assert grounding["matchBasis"] == "semantic"
        assert grounding["mentionedAs"] == PARAPHRASE_SENTENCE
        expected_score = l2_similarity(
            cosine_similarity(
                _build_vectors()["Feedback Delay"], _build_vectors()[PARAPHRASE_SENTENCE]
            )
        )
        assert grounding["matchScore"] == pytest.approx(expected_score)
        assert grounding["matchScore"] > _expected_cutoff()


class TestSingleSharedWordDoesNotGround:
    def test_semantic_matcher_rejects_an_unrelated_claim_sharing_one_word(self) -> None:
        embedder = _MappedEmbedder(_build_vectors())

        groundings = check_entity_grounding(TEXT, [SILENT_ENTITY], None, embedder)

        assert len(groundings) == 1
        grounding = groundings[0]
        assert grounding["status"] == "omitted"
        assert grounding["matchBasis"] is None
        assert grounding["mentionedAs"] is None

    def test_the_same_text_would_have_falsely_grounded_under_the_legacy_heuristic(self) -> None:
        """Documents exactly the bug being fixed: without an embedder (the
        legacy fallback), "silent" is a significant word shared between the
        entity name and the unrelated sentence, so the old heuristic
        credits it. This is the mechanism desire 10 replaces whenever an
        embedder is available -- pinned here so the fallback's existence
        doesn't quietly regress into being the default."""
        groundings = check_entity_grounding(TEXT, [SILENT_ENTITY], None, embedder=None)

        assert groundings[0]["status"] == "grounded"
        assert groundings[0]["matchBasis"] == "partial_word"
        assert groundings[0]["mentionedAs"] == "silent"


class TestBothDirectionsInOneCall:
    def test_paraphrase_grounds_and_overlap_claim_does_not_in_the_same_call(self) -> None:
        """The exact regression shape: one verify-fidelity-style call,
        checking two entities against the same text, where the old matcher
        got the paraphrase wrong AND the overlap wrong simultaneously."""
        embedder = _MappedEmbedder(_build_vectors())

        result = verify_fidelity(
            TEXT,
            [FEEDBACK_ENTITY, SILENT_ENTITY],
            [],
            embedder=embedder,
        )

        by_id = {g["entityId"]: g for g in result["entityGroundings"]}
        assert by_id[FEEDBACK_ENTITY["id"]]["status"] == "grounded"
        assert by_id[FEEDBACK_ENTITY["id"]]["matchBasis"] == "semantic"
        assert by_id[SILENT_ENTITY["id"]]["status"] == "omitted"
        # Order preserved: entities came in [feedback, silent].
        assert [g["entityId"] for g in result["entityGroundings"]] == [
            FEEDBACK_ENTITY["id"],
            SILENT_ENTITY["id"],
        ]


class TestCutoffComesFromDesire8sLandscapeNotAFreshNumber:
    def test_moving_the_calibration_corpus_moves_the_grounding_decision(self) -> None:
        """If the semantic matcher had its own hard-coded threshold, moving
        the landscape's calibration corpus would not change its verdicts.
        Raising the corpus's related-pair floor above the paraphrase's own
        score must flip the paraphrase from grounded to omitted -- proof the
        two share one number."""
        embedder = _MappedEmbedder(_build_vectors())
        low_cutoff_result = check_entity_grounding(TEXT, [FEEDBACK_ENTITY], None, embedder)
        assert low_cutoff_result[0]["status"] == "grounded"

        # A much stricter calibration corpus: unrelated pairs already score
        # high, related pairs score even higher -- pushes the cutoff above
        # the paraphrase's own ~0.76 score.
        vectors = _build_vectors()
        vectors["cud"] = _vec(3, 0, 0.90, 1)
        vectors["crd"] = _vec(3, 0, 0.99, 1)
        strict_embedder = _MappedEmbedder(vectors)

        strict_result = check_entity_grounding(TEXT, [FEEDBACK_ENTITY], None, strict_embedder)
        assert strict_result[0]["status"] == "omitted"


class TestExactMatchIsUnaffected:
    def test_exact_name_in_text_grounds_regardless_of_embedder(self) -> None:
        entity = {"id": "e-copper", "name": "Copper Relay"}
        text = "The Copper Relay feeds the buffer."

        without_embedder = check_entity_grounding(text, [entity], None, None)
        with_embedder = check_entity_grounding(
            text, [entity], None, _MappedEmbedder(_build_vectors() | {"Copper Relay": [1, 0, 0]})
        )

        for groundings in (without_embedder, with_embedder):
            assert groundings[0]["status"] == "grounded"
            assert groundings[0]["matchBasis"] == "exact"
            assert groundings[0]["mentionedAs"] == "Copper Relay"


class _StubLlmClient(SynthesisLlmClient):
    def __init__(self, response_text: str) -> None:
        self._response_text = response_text

    def complete(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        return {"text": self._response_text}

    def get_model(self) -> str:
        return "stub"


class TestLlmRefinementDisclosesItsOwnMatchBasis:
    def test_llm_refined_grounding_is_tagged_llm(self) -> None:
        entity = {"id": "e-x", "name": "Some Entity"}
        stub = _StubLlmClient(
            '[{"name": "Some Entity", "found": true, "mentionedAs": "the thing described"}]'
        )

        groundings = check_entity_grounding(
            "A completely unrelated sentence about gardening.", [entity], stub, None
        )

        assert groundings[0]["status"] == "grounded"
        assert groundings[0]["matchBasis"] == "llm"
        assert groundings[0]["mentionedAs"] == "the thing described"
        assert groundings[0]["matchScore"] is None
