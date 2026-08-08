"""Desire 10 (claude-desires.md): verify-fidelity's grounding matcher
replaces substring/word-overlap matching with SEMANTIC mention detection.

The bug this fixes failed in both directions at once: a faithful paraphrase
of an entity name ("the lag in the feedback loop" for "Feedback Delay")
scored zero grounding under the OLD word-only matcher when it shared no
literal words with the name, while an unrelated claim got credited as
grounded for sharing a single word with the entity's name ("silent"
observed live). Round 2 (blind critic finding): a naive single-cutoff
semantic replacement over-corrected -- calibrating the cutoff against a
corpus contaminated with word-overlap "false friends" pushed it above where
most genuine (often word-sharing) paraphrases live, so 11/14 of
``landscape.RELATED_PROBE_PAIRS`` failed to ground. The fix restores
``UNRELATED_PROBE_PAIRS`` to a clean, topically-disjoint corpus (one honest
number, the same one desire 8 reports) and adds a SECOND, targeted check
for the specific case a single scalar threshold can't resolve: when the
winning span shares a literal word with the entity name, the match must
survive having that word removed (:func:`_strip_shared_words`) -- a false
friend's similarity was mostly the word and collapses without it; a genuine
paraphrase that happens to reuse a word does not.

These vectors are engineered to sit in the REAL measured geometry (cosine
~0.35 for unrelated content, ~0.6-0.7 for related/word-inflated content --
not cosine 0.95, which no pair in this repo's live probe corpus ever
reaches) so the mocked scenarios below are shaped like the actual embedder,
not a toy. ``TestRealEmbedderEndToEnd`` additionally exercises the real
configured embedder directly (skipped, not failed, if it can't load).
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from theloom.semantic import landscape
from theloom.semantic.embed import cosine_similarity
from theloom.semantic.search import l2_similarity
from theloom.synthesis.fidelity import _strip_shared_words, check_entity_grounding, verify_fidelity
from theloom.synthesis.llm import SynthesisLlmClient

FEEDBACK_ENTITY = {"id": "e-feedback", "name": "Feedback Delay"}
SILENT_ENTITY = {"id": "e-silent", "name": "Silent Failure Mode"}
ESCALATION_ENTITY = {"id": "e-escalation", "name": "Escalation Protocol"}

# A genuine paraphrase that happens to reuse one word from the entity name --
# the spec's own worked example. Ends with its own sentence punctuation so
# it survives _candidate_mention_spans's sentence split unchanged when
# concatenated with another sentence (see TestBothDirectionsInOneCall).
PARAPHRASE_SENTENCE = "The lag in the feedback loop meant corrections always arrived too late."
# A coincidental word-overlap "false friend": shares "silent" with the
# entity name but is about something else entirely (the live bug report's
# own anecdote: mentionedAs: "silent").
FALSE_FRIEND_SENTENCE = "The orchestra performed a silent movie score."
# A second false friend (a different entity), so the stripping mechanism is
# pinned by more than one example.
ESCALATION_FALSE_FRIEND_SENTENCE = "The support ticket needed escalation to a senior engineer."

# A small, hand-picked corpus (swapped in for the production one via
# monkeypatch) so the cutoff used below is exactly computable rather than
# depending on the production probe corpus's own future edits. Magnitudes
# (cosine 0.35 / 0.70) match this repo's own live-measured unrelated/related
# bands (see theloom/semantic/landscape.py's UNRELATED_PROBE_PAIRS /
# RELATED_PROBE_PAIRS, live: unrelated mean cosine ~0.37, related mean
# cosine ~0.55-0.65), not arbitrary round numbers.
_TINY_UNRELATED = (("cu", "cud"),)
_TINY_RELATED = (("cr", "crd"),)
_UNRELATED_COSINE = 0.35
_RELATED_COSINE = 0.70


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


# Exact post-strip text theloom.synthesis.fidelity._strip_shared_words
# produces for each false-friend/paraphrase span -- computed once here so
# the mocked embedder's vocabulary matches the real implementation's lookups
# exactly (verified: these three lines are the function's actual output).
_FEEDBACK_STRIPPED = "The lag in the loop meant corrections always arrived too late."
_SILENT_STRIPPED = "The orchestra performed a movie score."
_ESCALATION_STRIPPED = "The support ticket needed to a senior engineer."


# Each entity gets its OWN pair of axes: one "content" axis and one "spare"
# axis the off-cosine mass lands on. Reusing a spare axis across two
# DIFFERENT entities' pairs would make their vectors accidentally
# non-orthogonal (a stray nonzero dot product), which is exactly the kind
# of cross-contamination that made TestBothDirectionsInOneCall flaky the
# first time this file was written -- eight dimensions, one reserved pair
# per entity, keeps every unrelated (entity, span) pair's cosine at exactly
# 0 unless a score is deliberately engineered above.
_DIM = 8
_CALIBRATION_AXES = (0, 1)
_FEEDBACK_AXES = (2, 3)
_SILENT_AXES = (4, 5)
_ESCALATION_AXES = (6, 7)


def _build_vectors() -> dict[str, list[float]]:
    calibration_primary, calibration_spare = _CALIBRATION_AXES
    feedback_primary, feedback_spare = _FEEDBACK_AXES
    silent_primary, silent_spare = _SILENT_AXES
    escalation_primary, escalation_spare = _ESCALATION_AXES

    def unit(axis: int) -> list[float]:
        v = [0.0] * _DIM
        v[axis] = 1.0
        return v

    return {
        # Calibration corpus, on its own reserved axis pair.
        "cu": unit(calibration_primary),
        "cud": _vec(_DIM, calibration_primary, _UNRELATED_COSINE, calibration_spare),
        "cr": unit(calibration_primary),
        "crd": _vec(_DIM, calibration_primary, _RELATED_COSINE, calibration_spare),
        # "Feedback Delay": genuine paraphrase sharing "feedback". Raw cosine
        # 0.69 (real-geometry range); stripped of "feedback", cosine drops
        # to 0.58 but STAYS well above the noise floor -- real semantic
        # content beyond the shared word, so it survives the guard.
        "Feedback Delay": unit(feedback_primary),
        PARAPHRASE_SENTENCE: _vec(_DIM, feedback_primary, 0.69, feedback_spare),
        _FEEDBACK_STRIPPED: _vec(_DIM, feedback_primary, 0.58, feedback_spare),
        # "Silent Failure Mode": false friend sharing "silent". Raw cosine
        # 0.62 clears the cutoff on its own (the exact failure mode a
        # single-threshold matcher misses); stripped of "silent", cosine
        # collapses to 0.20 -- almost nothing left once the shared word is
        # gone, so the guard rejects it.
        "Silent Failure Mode": unit(silent_primary),
        FALSE_FRIEND_SENTENCE: _vec(_DIM, silent_primary, 0.62, silent_spare),
        _SILENT_STRIPPED: _vec(_DIM, silent_primary, 0.20, silent_spare),
        # "Escalation Protocol": a second false friend, same shape.
        "Escalation Protocol": unit(escalation_primary),
        ESCALATION_FALSE_FRIEND_SENTENCE: _vec(_DIM, escalation_primary, 0.60, escalation_spare),
        _ESCALATION_STRIPPED: _vec(_DIM, escalation_primary, 0.18, escalation_spare),
    }


@pytest.fixture(autouse=True)
def _tiny_corpus(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(landscape, "UNRELATED_PROBE_PAIRS", _TINY_UNRELATED)
    monkeypatch.setattr(landscape, "RELATED_PROBE_PAIRS", _TINY_RELATED)


def _expected_cutoff() -> float:
    unrelated_score = l2_similarity(_UNRELATED_COSINE)
    related_score = l2_similarity(_RELATED_COSINE)
    return (unrelated_score + related_score) / 2


class TestStripSharedWords:
    """Pins the helper in isolation before trusting it inside the matcher."""

    def test_removes_the_shared_word_and_collapses_whitespace(self) -> None:
        assert _strip_shared_words(PARAPHRASE_SENTENCE, "feedback delay") == _FEEDBACK_STRIPPED

    def test_leaves_text_with_no_shared_word_unchanged(self) -> None:
        text = "output routed back around to become input again"
        assert _strip_shared_words(text, "feedback delay") == text

    def test_stripping_every_word_leaves_the_empty_string(self) -> None:
        assert _strip_shared_words("Silent Failure Mode", "silent failure mode") == ""


class TestParaphraseGrounds:
    def test_semantic_match_grounds_a_paraphrase_that_reuses_one_word(self) -> None:
        """The spec's own worked example: "Feedback Delay" mentioned via a
        paraphrase that happens to retain the word "feedback". Must clear
        BOTH the raw cutoff and the stripped-residual guard."""
        embedder = _MappedEmbedder(_build_vectors())

        groundings = check_entity_grounding(PARAPHRASE_SENTENCE, [FEEDBACK_ENTITY], None, embedder)

        assert len(groundings) == 1
        grounding = groundings[0]
        assert grounding["status"] == "grounded"
        assert grounding["matchBasis"] == "semantic"
        assert grounding["mentionedAs"] == PARAPHRASE_SENTENCE
        vectors = _build_vectors()
        expected_score = l2_similarity(
            cosine_similarity(vectors["Feedback Delay"], vectors[PARAPHRASE_SENTENCE])
        )
        assert grounding["matchScore"] == pytest.approx(expected_score)
        assert grounding["matchScore"] > _expected_cutoff()


class TestWordOverlapFalseFriendIsRejectedByTheStrippingGuard:
    """The exact case a single global cutoff cannot solve on this embedder:
    the RAW score alone clears the cutoff (so a bare-threshold matcher would
    wrongly ground it, reproducing the original bug through the embedder
    instead of the keyword matcher) -- only the stripped-residual check
    catches it."""

    def test_silent_failure_mode_raw_score_clears_cutoff_alone(self) -> None:
        """Documents WHY the guard is necessary: without it, this specific
        text would ground."""
        vectors = _build_vectors()
        raw_score = l2_similarity(
            cosine_similarity(vectors["Silent Failure Mode"], vectors[FALSE_FRIEND_SENTENCE])
        )
        assert raw_score > _expected_cutoff()

    def test_semantic_matcher_rejects_it_once_the_shared_word_is_stripped(self) -> None:
        embedder = _MappedEmbedder(_build_vectors())

        groundings = check_entity_grounding(FALSE_FRIEND_SENTENCE, [SILENT_ENTITY], None, embedder)

        assert len(groundings) == 1
        grounding = groundings[0]
        assert grounding["status"] == "omitted"
        assert grounding["matchBasis"] is None
        assert grounding["mentionedAs"] is None

    def test_a_second_false_friend_is_also_rejected(self) -> None:
        embedder = _MappedEmbedder(_build_vectors())

        groundings = check_entity_grounding(
            ESCALATION_FALSE_FRIEND_SENTENCE, [ESCALATION_ENTITY], None, embedder
        )

        assert groundings[0]["status"] == "omitted"

    def test_the_same_text_would_have_falsely_grounded_under_the_legacy_heuristic(self) -> None:
        """Without an embedder (the legacy fallback), "silent" is a
        significant word shared between the entity name and the unrelated
        sentence, so the old heuristic credits it. This is the mechanism
        desire 10 replaces whenever an embedder is available -- pinned here
        so the fallback's existence doesn't quietly regress into being the
        default."""
        groundings = check_entity_grounding(
            FALSE_FRIEND_SENTENCE, [SILENT_ENTITY], None, embedder=None
        )

        assert groundings[0]["status"] == "grounded"
        assert groundings[0]["matchBasis"] == "partial_word"
        assert groundings[0]["mentionedAs"] == "silent"


class TestBothDirectionsInOneCall:
    def test_paraphrase_grounds_and_overlap_claim_does_not_in_the_same_call(self) -> None:
        """The exact regression shape: one verify-fidelity-style call,
        checking two entities against the same text, where the old matcher
        got the paraphrase wrong AND the overlap wrong simultaneously."""
        embedder = _MappedEmbedder(_build_vectors())
        text = f"{PARAPHRASE_SENTENCE} {FALSE_FRIEND_SENTENCE}"

        result = verify_fidelity(
            text,
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
        low_cutoff_result = check_entity_grounding(
            PARAPHRASE_SENTENCE, [FEEDBACK_ENTITY], None, embedder
        )
        assert low_cutoff_result[0]["status"] == "grounded"

        # A much stricter calibration corpus: unrelated pairs already score
        # high, related pairs score even higher -- pushes the cutoff above
        # the paraphrase's own (stripped) score.
        vectors = _build_vectors()
        vectors["cud"] = _vec(_DIM, _CALIBRATION_AXES[0], 0.90, _CALIBRATION_AXES[1])
        vectors["crd"] = _vec(_DIM, _CALIBRATION_AXES[0], 0.99, _CALIBRATION_AXES[1])
        strict_embedder = _MappedEmbedder(vectors)

        strict_result = check_entity_grounding(
            PARAPHRASE_SENTENCE, [FEEDBACK_ENTITY], None, strict_embedder
        )
        assert strict_result[0]["status"] == "omitted"


class TestExactMatchIsUnaffected:
    def test_exact_name_in_text_grounds_regardless_of_embedder(self) -> None:
        entity = {"id": "e-copper", "name": "Copper Relay"}
        text = "The Copper Relay feeds the buffer."

        without_embedder = check_entity_grounding(text, [entity], None, None)
        with_embedder = check_entity_grounding(
            text,
            [entity],
            None,
            _MappedEmbedder(_build_vectors() | {"Copper Relay": [1.0] + [0.0] * (_DIM - 1)}),
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
