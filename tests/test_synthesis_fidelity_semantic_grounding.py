"""Desire 10 (claude-desires.md): verify-fidelity's grounding matcher
replaces substring/word-overlap matching with SEMANTIC mention detection.

Round-3 design (a RELATIVE decision, not an absolute one — see
theloom/synthesis/fidelity.py's own module docstring for the round 1/2
failure history that motivated it): grounding requires an entity's z-score
above its OWN measured baseline to clear a live-calibrated cutoff, in BOTH
of two independently-embedded representations (symmetric type-anchored, and
asymmetric bare-name) — not just one.

Two testing strategies are used here, deliberately:

1. Tests that pin the DECISION LOGIC (does grounding require both z-scores
   to clear, does stripping still apply first, is a fresh magic number
   absent) monkeypatch ``landscape.measure_specificity`` directly to a
   chosen cutoff — this keeps each test's vector arithmetic tractable
   without needing to hand-derive a full probe-corpus calibration.
2. One test (``TestCutoffIsCorpusDerivedNotHardcoded``) does NOT mock the
   calibration function — it swaps the underlying probe corpus and proves
   the decision changes, the actual "not a fresh magic number" claim.

Realistic-geometry validation against the REAL embedder (not mocks) lives in
tests/test_synthesis_fidelity_real_embedder.py, including fresh false-friend
and paraphrase cases beyond every named regression case from all three
rounds.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from theloom.semantic import landscape
from theloom.synthesis.fidelity import _strip_shared_words, check_entity_grounding, verify_fidelity
from theloom.synthesis.llm import SynthesisLlmClient

FEEDBACK_ENTITY = {"id": "e-feedback", "name": "Feedback Delay"}
SILENT_ENTITY = {"id": "e-silent", "name": "Silent Failure Mode"}
PARAPHRASE_SENTENCE = "The lag in the feedback loop meant corrections always arrived too late."
FALSE_FRIEND_SENTENCE = "The orchestra performed a silent movie score."
FEEDBACK_STRIPPED = "The lag in the loop meant corrections always arrived too late."
SILENT_STRIPPED = "The orchestra performed a movie score."


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
    v = [0.0] * dim
    v[primary] = cosine
    v[secondary] = math.sqrt(max(0.0, 1 - cosine**2))
    return v


def _shared_cosine_vec(dim: int, axes: tuple[int, ...], cosine: float, spare: int) -> list[float]:
    """A unit vector with the SAME cosine similarity to each of several
    unit-axis vectors at once (the rest of the mass on ``spare``) -- for a
    single distractor document that needs a controlled, equal baseline
    score against multiple different entities' own axes simultaneously."""
    v = [0.0] * dim
    for axis in axes:
        v[axis] = cosine
    v[spare] = math.sqrt(max(0.0, 1 - cosine**2 * len(axes)))
    return v


class _FakeSpecificityProfile:
    def __init__(self, cutoff: float) -> None:
        self.specificity_z_cutoff = cutoff


def _mock_specificity(
    monkeypatch: pytest.MonkeyPatch, *, symmetric: float, asymmetric: float
) -> None:
    """Bypass corpus calibration for tests that only care whether the
    DECISION requires both z-scores to clear their (independently chosen)
    cutoffs -- see this module's own docstring for why."""

    def fake(embedder: object, *, representation: str, **kwargs: object) -> _FakeSpecificityProfile:
        return _FakeSpecificityProfile(symmetric if representation == "symmetric" else asymmetric)

    monkeypatch.setattr(landscape, "measure_specificity", fake)


def _mock_sense_specificity(monkeypatch: pytest.MonkeyPatch, *, cutoff: float) -> None:
    """Bypass ``SENSE_ANCHOR_PROBE_PAIRS`` calibration the same way for
    round-4 sense-anchor tests."""

    def fake(embedder: object, **kwargs: object) -> _FakeSpecificityProfile:
        return _FakeSpecificityProfile(cutoff)

    monkeypatch.setattr(landscape, "measure_sense_specificity", fake)


@pytest.fixture(autouse=True)
def _tiny_battery(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_semantic_grounding`` always reads the unrelated-document battery
    from the live corpus (``landscape.unrelated_document_battery()``), even
    when the cutoff itself is mocked (above) -- give it a small, controlled
    one instead of the full 14-pair production corpus."""
    monkeypatch.setattr(
        landscape, "UNRELATED_PROBE_PAIRS", (("bg1", "distractor one"), ("bg2", "distractor two"))
    )


def _battery_vectors(dim: int) -> dict[str, list[float]]:
    return {"bg1": [0.0] * dim, "bg2": [0.0] * dim}


class TestGroundsOnlyWhenBothRepresentationsAgree:
    """The core round-3 property: a candidate must clear the SYMMETRIC
    cutoff AND the ASYMMETRIC cutoff. Neither alone is sufficient -- this
    is exactly what caught "Silent Failure Mode" live (round 3 report):
    its symmetric z cleared a permissive cutoff on its own, its asymmetric
    z did not, so the conjunction correctly rejects it."""

    DIM = 4

    def _vectors(self, span_cosine: float) -> dict[str, list[float]]:
        vectors = _battery_vectors(self.DIM)
        vectors["distractor one"] = _vec(self.DIM, 0, 0.0, 1)
        vectors["distractor two"] = _vec(self.DIM, 0, 0.1, 1)
        vectors["[concept] X"] = [1.0, 0.0, 0.0, 0.0]
        vectors["X"] = [1.0, 0.0, 0.0, 0.0]
        vectors["span"] = _vec(self.DIM, 0, span_cosine, 1)
        return vectors

    def test_grounds_when_both_clear(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _mock_specificity(monkeypatch, symmetric=1.0, asymmetric=1.0)
        embedder = _MappedEmbedder(self._vectors(0.9))
        entity = {"id": "x", "name": "X"}

        groundings = check_entity_grounding("span", [entity], None, embedder)

        assert groundings[0]["status"] == "grounded"
        assert groundings[0]["matchBasis"] == "semantic"
        assert isinstance(groundings[0]["zScore"], float)
        assert isinstance(groundings[0]["asymZScore"], float)

    def test_rejected_when_symmetric_clears_but_asymmetric_does_not(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A generous symmetric cutoff (easy to clear) but an impossible
        # asymmetric one -- the span's own vectors are identical in both
        # representations here, so only the CUTOFF differs.
        _mock_specificity(monkeypatch, symmetric=0.5, asymmetric=1000.0)
        embedder = _MappedEmbedder(self._vectors(0.9))
        entity = {"id": "x", "name": "X"}

        groundings = check_entity_grounding("span", [entity], None, embedder)

        assert groundings[0]["status"] == "omitted"

    def test_rejected_when_asymmetric_clears_but_symmetric_does_not(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mock_specificity(monkeypatch, symmetric=1000.0, asymmetric=0.5)
        embedder = _MappedEmbedder(self._vectors(0.9))
        entity = {"id": "x", "name": "X"}

        groundings = check_entity_grounding("span", [entity], None, embedder)

        assert groundings[0]["status"] == "omitted"


class TestStrippingStillAppliesFirst:
    """Round 2's guard (residual-similarity check) is still the first move
    when a candidate span shares a significant word with the entity name --
    round 3 only changed how the RESULTING score is judged (a z-score, not
    an absolute cutoff), not whether stripping happens at all."""

    def test_semantic_matcher_rejects_a_word_overlap_false_friend(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mock_specificity(monkeypatch, symmetric=0.5, asymmetric=0.5)
        dim = 4
        vectors = _battery_vectors(dim)
        vectors["distractor one"] = _vec(dim, 0, 0.0, 1)
        vectors["distractor two"] = _vec(dim, 0, 0.1, 1)
        vectors["[concept] Silent Failure Mode"] = [1.0, 0.0, 0.0, 0.0]
        vectors["Silent Failure Mode"] = [1.0, 0.0, 0.0, 0.0]
        # Raw span scores high (shares "silent")...
        vectors[FALSE_FRIEND_SENTENCE] = _vec(dim, 0, 0.95, 1)
        # ...but the residual (word stripped) collapses to the noise floor.
        vectors[SILENT_STRIPPED] = _vec(dim, 0, 0.05, 1)
        embedder = _MappedEmbedder(vectors)

        groundings = check_entity_grounding(FALSE_FRIEND_SENTENCE, [SILENT_ENTITY], None, embedder)

        assert groundings[0]["status"] == "omitted"

    def test_the_same_text_would_have_falsely_grounded_under_the_legacy_heuristic(self) -> None:
        """Without an embedder (the legacy fallback), "silent" is a
        significant word shared between the entity name and the unrelated
        sentence, so the old heuristic credits it -- the mechanism desire
        10 replaces whenever an embedder is available."""
        groundings = check_entity_grounding(
            FALSE_FRIEND_SENTENCE, [SILENT_ENTITY], None, embedder=None
        )

        assert groundings[0]["status"] == "grounded"
        assert groundings[0]["matchBasis"] == "partial_word"
        assert groundings[0]["mentionedAs"] == "silent"


class TestStripSharedWords:
    def test_removes_the_shared_word_and_collapses_whitespace(self) -> None:
        assert _strip_shared_words(PARAPHRASE_SENTENCE, "feedback delay") == FEEDBACK_STRIPPED

    def test_leaves_text_with_no_shared_word_unchanged(self) -> None:
        text = "output routed back around to become input again"
        assert _strip_shared_words(text, "feedback delay") == text

    def test_stripping_every_word_leaves_the_empty_string(self) -> None:
        assert _strip_shared_words("Silent Failure Mode", "silent failure mode") == ""


class TestSenseAnchoredDecision:
    """Round 5: when a word-overlap candidate's entity has real
    observations, the sense anchor -- built from observations ALONE, no
    entity name (``landscape.observation_anchor``) -- is compared against
    the INTACT span (never stripped) and IS the decision for that span, not
    the round-3 name-based dual check and not merely an extra check layered
    on top of it. Both directions are pinned by engineering the SAME span
    vector to clear one mechanism's mocked cutoff and fail the other's, so
    the two mechanisms visibly disagree and the sense anchor's verdict is
    the one that wins whenever observations exist. The degraded
    (no-observations) path still strips the shared word from the span
    first, scored against the NAME axis -- that representation stays
    name-anchored, so a SEPARATE, independently-chosen cosine on the
    stripped RESIDUAL text drives it, proving the two mechanisms read
    different texts entirely, not just different cutoffs on the same text.
    """

    DIM = 4
    NAME_AXIS = 0
    SENSE_AXIS = 2
    ENTITY_NAME = "Test Concept"
    OBSERVATIONS = ["a formal working definition of this concept"]
    SPAN = "The scientist explained a completely unrelated concept about anthropomorphizing plants."
    # _strip_shared_words(SPAN, "test concept") -- verified directly, see
    # TestStripSharedWords's own tests for the mechanism this depends on.
    # Only reached by the DEGRADED (no-observations) path (round 5).
    RESIDUAL = "The scientist explained a completely unrelated about anthropomorphizing plants."

    def _vectors(
        self, span_cosine_to_sense: float, residual_cosine_to_name: float
    ) -> dict[str, list[float]]:
        vectors = _battery_vectors(self.DIM)
        vectors["distractor one"] = _shared_cosine_vec(
            self.DIM, (self.NAME_AXIS, self.SENSE_AXIS), 0.0, 1
        )
        vectors["distractor two"] = _shared_cosine_vec(
            self.DIM, (self.NAME_AXIS, self.SENSE_AXIS), 0.1, 1
        )
        name_unit = [0.0] * self.DIM
        name_unit[self.NAME_AXIS] = 1.0
        vectors[f"[concept] {self.ENTITY_NAME}"] = name_unit
        vectors[self.ENTITY_NAME] = name_unit
        # Keyed by the OBSERVATION-ONLY anchor text (round 5) -- no entity
        # name prefix at all.
        vectors[f"{self.OBSERVATIONS[0]}."] = [
            0.0 if i != self.SENSE_AXIS else 1.0 for i in range(self.DIM)
        ]
        # The INTACT SPAN is what the sense-anchor path compares -- only its
        # cosine to the SENSE axis matters to that decision (the anchor
        # never sees the NAME axis at all).
        span_vector = [0.0] * self.DIM
        span_vector[self.SENSE_AXIS] = span_cosine_to_sense
        span_vector[1] = math.sqrt(max(0.0, 1 - span_cosine_to_sense**2))
        vectors[self.SPAN] = span_vector
        # The STRIPPED RESIDUAL is what the DEGRADED (no-observations) path
        # compares instead -- an independently chosen cosine to the NAME
        # axis, unrelated to the span's own sense-axis cosine above.
        residual_vector = [0.0] * self.DIM
        residual_vector[self.NAME_AXIS] = residual_cosine_to_name
        residual_vector[1] = math.sqrt(max(0.0, 1 - residual_cosine_to_name**2))
        vectors[self.RESIDUAL] = residual_vector
        return vectors

    def _entity(self, with_observations: bool) -> dict[str, object]:
        return {
            "id": "x",
            "name": self.ENTITY_NAME,
            "observations": self.OBSERVATIONS if with_observations else [],
        }

    def test_sense_anchor_rejects_what_name_only_would_have_grounded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mock_specificity(monkeypatch, symmetric=0.1, asymmetric=0.1)  # easy to clear
        _mock_sense_specificity(monkeypatch, cutoff=1000.0)  # impossible to clear
        # Low cosine to the SENSE axis (the sense anchor correctly finds
        # nothing here) but a high cosine to the NAME axis in the STRIPPED
        # residual, which the degraded check would ground easily.
        vectors = self._vectors(span_cosine_to_sense=0.1, residual_cosine_to_name=0.9)
        embedder = _MappedEmbedder(vectors)

        with_observations = check_entity_grounding(
            self.SPAN, [self._entity(with_observations=True)], None, embedder
        )
        assert with_observations[0]["status"] == "omitted"
        # Round 5 disclosure: an omitted decision still names the mechanism
        # ATTEMPTED and carries its full evidence, not nulls.
        assert with_observations[0]["matchBasis"] == "semantic"
        assert isinstance(with_observations[0]["zScore"], float)
        assert with_observations[0]["zCutoff"] == 1000.0

        # The SAME vectors, but this entity has no observations: the sense
        # anchor never applies, so the (easy) degraded check decides instead
        # and grounds it -- proving the sense anchor was the deciding
        # factor above, not an accident of the vectors chosen.
        without_observations = check_entity_grounding(
            self.SPAN, [self._entity(with_observations=False)], None, embedder
        )
        assert without_observations[0]["status"] == "grounded"
        assert without_observations[0]["matchBasis"] == "semantic-name-only"

    def test_sense_anchor_grounds_what_name_only_would_have_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mock_specificity(monkeypatch, symmetric=1000.0, asymmetric=1000.0)  # impossible to clear
        _mock_sense_specificity(monkeypatch, cutoff=0.1)  # easy to clear
        # High cosine to the SENSE axis (the anchor grounds this easily) but
        # a low cosine to the NAME axis in the STRIPPED residual, which the
        # degraded check could never ground.
        vectors = self._vectors(span_cosine_to_sense=0.9, residual_cosine_to_name=0.05)
        embedder = _MappedEmbedder(vectors)

        with_observations = check_entity_grounding(
            self.SPAN, [self._entity(with_observations=True)], None, embedder
        )
        assert with_observations[0]["status"] == "grounded"
        assert with_observations[0]["matchBasis"] == "semantic"
        assert isinstance(with_observations[0]["zScore"], float)
        assert with_observations[0]["zCutoff"] == 0.1
        assert with_observations[0]["asymZScore"] is None
        assert with_observations[0]["asymZCutoff"] is None

        without_observations = check_entity_grounding(
            self.SPAN, [self._entity(with_observations=False)], None, embedder
        )
        assert without_observations[0]["status"] == "omitted"
        # Round 5 disclosure: the degraded check was attempted (and failed),
        # so it is named here too, not nulled out.
        assert without_observations[0]["matchBasis"] == "semantic-name-only"
        assert isinstance(without_observations[0]["zScore"], float)

    def test_only_the_guard_placeholder_observation_still_degrades(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The exact synthetic observation ``create-entity`` writes when
        none is supplied (theloom.verification.guards.entity_gate_warnings)
        must not be mistaken for a real definition."""
        _mock_specificity(monkeypatch, symmetric=0.1, asymmetric=0.1)
        _mock_sense_specificity(monkeypatch, cutoff=1000.0)
        vectors = self._vectors(span_cosine_to_sense=0.5, residual_cosine_to_name=0.9)
        embedder = _MappedEmbedder(vectors)
        entity = {
            "id": "x",
            "name": self.ENTITY_NAME,
            "observations": [
                "[guard:OBSERVATIONS_REQUIRED] Entity must have at least one observation"
            ],
        }

        result = check_entity_grounding(self.SPAN, [entity], None, embedder)

        assert result[0]["status"] == "grounded"
        assert result[0]["matchBasis"] == "semantic-name-only"


class TestExactMatchIsUnaffected:
    def test_exact_name_in_text_grounds_regardless_of_embedder(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mock_specificity(monkeypatch, symmetric=1000.0, asymmetric=1000.0)  # impossible to clear
        entity = {"id": "e-copper", "name": "Copper Relay"}
        text = "The Copper Relay feeds the buffer."

        without_embedder = check_entity_grounding(text, [entity], None, None)
        with_embedder = check_entity_grounding(
            text, [entity], None, _MappedEmbedder(_battery_vectors(2))
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
        assert groundings[0]["zScore"] is None
        assert groundings[0]["asymZScore"] is None


class TestCutoffIsCorpusDerivedNotHardcoded:
    """Unlike the tests above, this one does NOT mock
    ``measure_specificity`` -- it swaps the actual probe corpus
    (``landscape.UNRELATED_PROBE_PAIRS`` / ``RELATED_PROBE_PAIRS``) and
    proves the grounding decision follows, the literal "not a fresh magic
    number" claim. Every entity/document below shares one axis pair so the
    arithmetic is small enough to hand-verify: entities score cosine 0.9 to
    their own related document, 0.0-0.1 to unrelated ones."""

    DIM = 2

    def _corpus_vectors(self, crd1_cosine: float, crd2_cosine: float) -> dict[str, list[float]]:
        # One shared axis: "name"-shaped strings and "doc"-shaped strings
        # for both the calibration corpus and the entity under test all
        # live on it, distinguished only by which cosine each pair is
        # placed at. The entity's own candidate ("span") sits at a FIXED
        # cosine (0.5); only the related corpus's own cosines move between
        # the "lenient" and "strict" cases below.
        return {
            "cu1": [1.0, 0.0],
            "cud1": _vec(self.DIM, 0, 0.0, 1),
            "cu2": [1.0, 0.0],
            "cud2": _vec(self.DIM, 0, 0.1, 1),
            "cu3": [1.0, 0.0],
            "cud3": _vec(self.DIM, 0, 0.05, 1),
            "cr1": [1.0, 0.0],
            "crd1": _vec(self.DIM, 0, crd1_cosine, 1),
            "cr2": [1.0, 0.0],
            "crd2": _vec(self.DIM, 0, crd2_cosine, 1),
            "[concept] cu1": [1.0, 0.0],
            "[concept] cu2": [1.0, 0.0],
            "[concept] cu3": [1.0, 0.0],
            "[concept] cr1": [1.0, 0.0],
            "[concept] cr2": [1.0, 0.0],
            "X": [1.0, 0.0],
            "[concept] X": [1.0, 0.0],
            "span": _vec(self.DIM, 0, 0.5, 1),
        }

    def test_a_lenient_corpus_grounds_and_a_strict_one_does_not(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            landscape,
            "UNRELATED_PROBE_PAIRS",
            (("cu1", "cud1"), ("cu2", "cud2"), ("cu3", "cud3")),
        )
        monkeypatch.setattr(landscape, "RELATED_PROBE_PAIRS", (("cr1", "crd1"), ("cr2", "crd2")))

        # Lenient: the corpus's own related pairs score moderately (cosine
        # 0.6/0.55) -- comfortably below the test entity's own "span"
        # z-score, so it grounds.
        lenient_embedder = _MappedEmbedder(self._corpus_vectors(0.6, 0.55))
        entity = {"id": "x", "name": "X"}

        lenient_result = check_entity_grounding("span", [entity], None, lenient_embedder)
        assert lenient_result[0]["status"] == "grounded"

        # Strict: the SAME corpus, but its related pairs now demand
        # near-perfect matches (cosine 0.95/0.93) -- the calibrated cutoff
        # rises well above the (unchanged) test entity's own z-score, so
        # the identical "span" no longer clears the bar.
        strict_embedder = _MappedEmbedder(self._corpus_vectors(0.95, 0.93))

        strict_result = check_entity_grounding("span", [entity], None, strict_embedder)
        assert strict_result[0]["status"] == "omitted"


class TestBothDirectionsInOneCall:
    def test_paraphrase_grounds_and_overlap_claim_does_not_in_the_same_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The exact regression shape: one verify-fidelity-style call,
        checking two entities against the same text, where the old matcher
        got the paraphrase wrong AND the overlap wrong simultaneously."""
        _mock_specificity(monkeypatch, symmetric=0.5, asymmetric=0.5)
        dim = 6
        vectors = _battery_vectors(dim)
        # Distractors need a non-degenerate (nonzero-stdev) baseline
        # against BOTH entities below -- a small, shared cosine to each
        # entity's own axis (0 for Feedback Delay, 2 for Silent Failure
        # Mode), differing between the two distractors.
        vectors["distractor one"] = _shared_cosine_vec(dim, (0, 2), 0.05, 4)
        vectors["distractor two"] = _shared_cosine_vec(dim, (0, 2), 0.15, 4)
        vectors["[concept] Feedback Delay"] = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        vectors["Feedback Delay"] = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        vectors[PARAPHRASE_SENTENCE] = _vec(dim, 0, 0.9, 1)
        vectors[FEEDBACK_STRIPPED] = _vec(dim, 0, 0.85, 1)
        vectors["[concept] Silent Failure Mode"] = [0.0, 0.0, 1.0, 0.0, 0.0, 0.0]
        vectors["Silent Failure Mode"] = [0.0, 0.0, 1.0, 0.0, 0.0, 0.0]
        vectors[FALSE_FRIEND_SENTENCE] = _vec(dim, 2, 0.9, 3)
        vectors[SILENT_STRIPPED] = _vec(dim, 2, 0.05, 3)
        embedder = _MappedEmbedder(vectors)
        text = f"{PARAPHRASE_SENTENCE} {FALSE_FRIEND_SENTENCE}"

        result = verify_fidelity(text, [FEEDBACK_ENTITY, SILENT_ENTITY], [], embedder=embedder)

        by_id = {g["entityId"]: g for g in result["entityGroundings"]}
        assert by_id[FEEDBACK_ENTITY["id"]]["status"] == "grounded"
        # Neither entity carries observations here, so the word-overlap trap
        # (both spans share a word with their entity) degrades to the
        # round-3 name-based check, honestly disclosed -- see
        # TestSenseAnchoredDecision below for the fully-anchored path.
        assert by_id[FEEDBACK_ENTITY["id"]]["matchBasis"] == "semantic-name-only"
        assert by_id[SILENT_ENTITY["id"]]["status"] == "omitted"
        assert [g["entityId"] for g in result["entityGroundings"]] == [
            FEEDBACK_ENTITY["id"],
            SILENT_ENTITY["id"],
        ]
