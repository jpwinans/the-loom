"""TL-484 (child of the TL-477 Agent Contract epic): ``verify-fidelity``
must not silently mislead a caller who omits ``entityIds``.

Before this fix, an unscoped call graded ``text`` against every entity in the
graph. On a real-sized graph the entity/relation denominators are dominated by
entities the text never mentions, so the score collapses toward zero — a
verdict shaped exactly like a real "this text is poorly grounded" result, even
when the text is genuinely well-grounded in the handful of entities it
actually references. Nothing in ``--help``/``--schema``/COMMANDS.md said so:
the fix documents the behavior in the input model's
``Field(description=...)`` (machine-readable via ``--schema``) and changes the
*runtime* behavior so the silent whole-graph failure mode cannot happen.

Fix (auto-scope, chosen over an outright refusal): reuse the
``anchor_search_for``/``find_anchors`` hybrid-search core that
``synthesize``/``plan_synthesis``/``traverse_synthesis`` already use for
anchor selection — no reimplementation of that machinery. An omitted (or
empty) ``entityIds`` scopes to that retrieval's picks and carries an
``AUTO_SCOPED`` notice (the shared ``theloom.operations.notices``
convention) naming the count and the selected entities; a hint says
``entityIds`` can be passed explicitly. If retrieval finds nothing to scope
to, the command refuses with ``INPUT_REQUIRED`` naming ``entityIds`` and the
hybrid-search-then-verify two-step, rather than falling through to a
whole-graph or an empty-scope score.

Round 2 (integration arbiter finding): on an EMBEDDED graph, that refusal
was unreachable. Vector k-nearest-neighbors always returns up to
MAX_ANCHORS candidates regardless of how (ir)relevant they are — there is no
"no results" case for kNN — so unrelated prose and pure gibberish both got
auto-scoped across the whole graph with a verdict-shaped near-zero grade,
exactly the failure mode this ticket exists to prevent. The fix is
``VERIFY_FIDELITY_RELEVANCE_FLOOR`` in ``theloom.operations.synthesis``: a
vector-path candidate must score above what two orthogonal (cosine
similarity 0 — no linear relationship) vectors would score on the shared
1/(1+L2) scale to count as an anchor. Below that, and only when NO store has
any embedding at all, does auto-scope fall to the (unmodified)
keyword-only path.

These tests pin: (1) the unscoped call never returns a whole-graph
verdict with no notice, in any case; (2) the auto-scoped path meaningfully
outperforms the old whole-graph behavior on a graph shaped like the reported
bug (a handful of relevant entities lost in many irrelevant ones); (3) on an
EMBEDDED graph, a query orthogonal to every entity refuses rather than
auto-scoping to noise, while a genuinely related query still auto-scopes
with a meaningful grade; (4) the graceful keyword fallback when entities
carry no embeddings at all; (5) the refusal when nothing matches by any
path; (6) the scoped path (explicit ``entityIds``) is completely
untouched — same scores, no notice, and the auto-scope retrieval is never
even invoked.
"""

from __future__ import annotations

import math

import pytest

from tests.fakes import FakeEmbedder
from theloom.errors import InputRequiredError
from theloom.model import EntityCreate, RelationCreate
from theloom.operations.synthesis import VERIFY_FIDELITY_RELEVANCE_FLOOR, VerifyFidelityInput
from theloom.operations.synthesis import verify_fidelity as verify_fidelity_op
from theloom.store.multigraph import MultiGraph
from theloom.synthesis.fidelity import verify_fidelity as verify_fidelity_core

MAX_ANCHORS = 10  # theloom.synthesis.selector.MAX_ANCHORS — asserted against directly


def _entity(name: str) -> EntityCreate:
    return EntityCreate.model_validate({"name": name, "entityType": "concept", "observations": []})


class TestAutoScopeOutperformsWholeGraph:
    """The exact shape of the reported bug: a few relevant entities, buried
    in many irrelevant ones, all carrying embeddings. Grading against
    everything collapses the score; auto-scoping to retrieval's picks does
    not."""

    # 7 relevant entities — same count as the ticket's own reported example
    # ("scoped to 7 relevant entityIds scored 0.541/moderate"). Distractors
    # sit at cosine similarity 0 (orthogonal) to the query, exactly at
    # VERIFY_FIDELITY_RELEVANCE_FLOOR — the floor drops them (strict `>`),
    # so the scoped set is deterministically exactly the 7 relevant ones,
    # never diluted by MAX_ANCHORS filler.
    RELEVANT = [
        "Copper Relay",
        "Signal Buffer",
        "Thermal Core",
        "Power Cell",
        "Control Valve",
        "Feedback Loop",
        "Output Gate",
    ]
    TEXT = (
        "The Copper Relay feeds the Signal Buffer, which feeds the Thermal Core, "
        "which feeds the Power Cell, which feeds the Control Valve, which feeds "
        "the Feedback Loop, which feeds the Output Gate."
    )

    def _seed(self, multi: MultiGraph, distractor_count: int) -> dict[str, str]:
        store = multi.get_store()
        store.ensure_vector_index(dimension=2)
        ids: dict[str, str] = {}
        for name in self.RELEVANT:
            entity = store.create_entity(_entity(name))
            store.set_entity_vector(entity.id, [1.0, 0.0])
            ids[name] = entity.id
        for i in range(distractor_count):
            entity = store.create_entity(_entity(f"Distractor Node {i:03d}"))
            store.set_entity_vector(entity.id, [0.0, 1.0])
        # A causal chain across all 7 relevant entities, in the same order
        # TEXT mentions them — every edge is "preserved" under structural
        # scoring, and no distractor participates in any relation.
        for from_name, to_name in zip(self.RELEVANT, self.RELEVANT[1:], strict=False):
            store.create_relation(
                RelationCreate.model_validate(
                    {"from": ids[from_name], "to": ids[to_name], "relationType": "causes"}
                )
            )
        return ids

    def test_auto_scoped_result_beats_the_whole_graph_baseline(
        self, multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        distractor_count = 48  # echoes the ticket's own reported probe size
        relevant_ids = self._seed(multi, distractor_count)
        monkeypatch.setattr(
            "theloom.operations.synthesis.get_embedder", lambda: FakeEmbedder([1.0, 0.0])
        )
        store = multi.get_store()

        # The old behavior, reproduced directly against fidelity's own core:
        # grading the same text against every entity/relation in the graph.
        baseline = verify_fidelity_core(
            self.TEXT, store.list_entity_docs(), store.list_relation_docs()
        )
        assert baseline["level"] == "low"

        result = verify_fidelity_op(VerifyFidelityInput(text=self.TEXT), multi)

        assert [n["code"] for n in result["notices"]] == ["AUTO_SCOPED"]
        scoped_ids = {g["entityId"] for g in result["entityGroundings"]}
        # The relevance floor drops every distractor (exactly orthogonal to
        # the query, i.e. right at the floor, which is exclusive) — so the
        # scoped set is precisely the 7 relevant entities, not diluted by
        # MAX_ANCHORS filler the way an unfiltered kNN top-k would be.
        assert scoped_ids == set(relevant_ids.values())
        assert len(scoped_ids) < MAX_ANCHORS + distractor_count  # not the whole 55-entity graph

        assert result["scores"]["entityGroundingRate"] == pytest.approx(1.0)
        assert result["scores"]["relationPreservationRate"] == pytest.approx(1.0)
        assert result["level"] == "high"
        assert result["scores"]["compositeIndex"] > baseline["scores"]["compositeIndex"]

        notice_message = result["notices"][0]["message"]
        for name in self.RELEVANT:
            assert name in notice_message
        assert "hint" in result["notices"][0]
        assert "entityIds" in result["notices"][0]["hint"]

    def test_never_returns_a_whole_graph_verdict_with_no_notice(
        self, multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The acceptance bar's FAIL condition, made explicit: however the
        unscoped call resolves, it must not be a bare (notice-less) result
        scored against the entire entity/relation set."""
        self._seed(multi, distractor_count=48)
        monkeypatch.setattr(
            "theloom.operations.synthesis.get_embedder", lambda: FakeEmbedder([1.0, 0.0])
        )

        result = verify_fidelity_op(VerifyFidelityInput(text=self.TEXT), multi)

        assert result.get("notices"), "an unscoped call must always carry a notice"
        scoped_ids = {g["entityId"] for g in result["entityGroundings"]}
        assert len(scoped_ids) < len(multi.get_store().list_entity_docs())


class TestRelevanceFloorOnAnEmbeddedGraph:
    """Round-2 fix: the integration arbiter found that on an embedded graph
    the refusal branch was unreachable — vector kNN always returns up to
    MAX_ANCHORS candidates no matter how irrelevant the query, so unrelated
    prose AND pure gibberish both got auto-scoped across the whole graph
    with a verdict-shaped near-zero grade. ``VERIFY_FIDELITY_RELEVANCE_FLOOR``
    exists to make that refusal reachable: a candidate must clear cosine
    similarity 0.5, not merely edge past orthogonality (cosine 0) — real
    embedding models are anisotropic enough that unrelated text routinely
    beats the orthogonal score (this repo's own local embedder places
    unrelated pairs around cosine ≈ 0.41-0.44), which is exactly how the
    naive orthogonal floor failed to catch the arbiter's probe."""

    def test_floor_is_exactly_the_cosine_half_score_on_the_shared_scale(self) -> None:
        """Pins the formula, not just its numeric value: 1/(1 + L2) at
        cosine similarity 0.5 for unit vectors is 1/(1 + sqrt(1)) = 0.5 —
        deliberately more conservative than the orthogonal (cosine 0) score
        of ~0.4142, which real (anisotropic) embedding models can clear even
        for genuinely unrelated text (see the constant's own comment)."""
        assert pytest.approx(0.5) == VERIFY_FIDELITY_RELEVANCE_FLOOR
        assert pytest.approx(1 / (1 + math.sqrt(2 - 2 * 0.5))) == VERIFY_FIDELITY_RELEVANCE_FLOOR

    def test_orthogonal_query_refuses_even_though_every_entity_is_embedded(
        self, multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The previously-unreachable branch: every entity in the graph
        carries an embedding (so the vector path always returns candidates),
        but the query text's embedding is orthogonal to all of them —
        exactly the "unrelated prose / gibberish" shape the arbiter probed.
        None of them may count as an anchor."""
        store = multi.get_store()
        store.ensure_vector_index(dimension=2)
        for name in ["Copper Relay", "Signal Buffer", "Thermal Core"]:
            entity = store.create_entity(_entity(name))
            store.set_entity_vector(entity.id, [1.0, 0.0])
        monkeypatch.setattr(
            "theloom.operations.synthesis.get_embedder", lambda: FakeEmbedder([0.0, 1.0])
        )

        with pytest.raises(InputRequiredError) as excinfo:
            verify_fidelity_op(
                VerifyFidelityInput(text="zzqqxvv wgblrk ttphmn qyzorb vklneq."), multi
            )

        assert excinfo.value.code == "INPUT_REQUIRED"
        assert "entityIds" in str(excinfo.value)
        assert "hybrid-search" in str(excinfo.value)

    def test_anisotropic_baseline_above_orthogonal_still_refuses(
        self, multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The exact regression the naive orthogonal floor missed: a query
        at cosine similarity 0.3 to every entity (score ≈ 0.458) clears the
        old orthogonal floor (≈ 0.4142) easily, but is still well below the
        0.5 floor this fix actually uses. Simulates the anisotropic
        "unrelated but not orthogonal" baseline real embedding models
        produce — proven live against this repo's own local embedder in the
        manual CLI verification (tl477-build5b), where unrelated pairs
        scored ≈ 0.48-0.49, never near 0."""
        store = multi.get_store()
        store.ensure_vector_index(dimension=2)
        for name in ["Copper Relay", "Signal Buffer", "Thermal Core"]:
            entity = store.create_entity(_entity(name))
            store.set_entity_vector(entity.id, [1.0, 0.0])
        # cos_sim([1, 0], [0.3, sqrt(1 - 0.3**2)]) == 0.3 exactly.
        monkeypatch.setattr(
            "theloom.operations.synthesis.get_embedder",
            lambda: FakeEmbedder([0.3, math.sqrt(1 - 0.3**2)]),
        )

        with pytest.raises(InputRequiredError):
            verify_fidelity_op(
                VerifyFidelityInput(text="Unrelated but not-quite-orthogonal filler text."), multi
            )

    def test_related_query_still_auto_scopes_with_a_meaningful_grade(
        self, multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The positive control for the same graph shape: a query whose
        embedding actually matches the entities must still auto-scope and
        score well — the floor rejects noise, not signal."""
        store = multi.get_store()
        store.ensure_vector_index(dimension=2)
        ids = {}
        for name in ["Copper Relay", "Signal Buffer", "Thermal Core"]:
            entity = store.create_entity(_entity(name))
            store.set_entity_vector(entity.id, [1.0, 0.0])
            ids[name] = entity.id
        monkeypatch.setattr(
            "theloom.operations.synthesis.get_embedder", lambda: FakeEmbedder([1.0, 0.0])
        )
        text = "Copper Relay feeds Signal Buffer, and Signal Buffer powers Thermal Core."

        result = verify_fidelity_op(VerifyFidelityInput(text=text), multi)

        assert [n["code"] for n in result["notices"]] == ["AUTO_SCOPED"]
        scoped_ids = {g["entityId"] for g in result["entityGroundings"]}
        assert scoped_ids == set(ids.values())
        assert result["scores"]["entityGroundingRate"] == pytest.approx(1.0)
        assert result["level"] == "high"


class TestAutoScopeWithoutEmbeddings:
    """No entity in the graph has a vector: retrieval must fall back to
    keyword matching (never call the embedder — see
    test_synthesis_anchors.test_anchor_search_is_empty_without_embeddings_and_never_embeds
    for that guarantee at the retrieval layer) and still scope meaningfully."""

    # No shared substrings with the distractor names below (keyword scoring
    # matches raw, punctuation-attached tokens as substrings of the entity
    # name, so an incidental overlap like "anthem" containing "the" would
    # silently corrupt this test).
    TEXT = "Copper Relay feeds Signal Buffer, and Signal Buffer powers Thermal Core."

    def test_keyword_fallback_scopes_to_name_matches_only(self, multi: MultiGraph) -> None:
        store = multi.get_store()
        relevant_ids = {
            name: store.create_entity(_entity(name)).id
            for name in ["Copper Relay", "Signal Buffer", "Thermal Core"]
        }
        for name in ["Marble Ledger", "Violet Harbor", "Granite Column"]:
            store.create_entity(_entity(name))

        result = verify_fidelity_op(VerifyFidelityInput(text=self.TEXT), multi)

        assert [n["code"] for n in result["notices"]] == ["AUTO_SCOPED"]
        scoped_ids = {g["entityId"] for g in result["entityGroundings"]}
        assert scoped_ids == set(relevant_ids.values())
        assert result["scores"]["entityGroundingRate"] == pytest.approx(1.0)
        assert result["level"] == "high"


class TestAutoScopeRefusesWhenNothingMatches:
    def test_raises_input_required_naming_entity_ids_and_the_two_step(
        self, multi: MultiGraph
    ) -> None:
        store = multi.get_store()
        store.create_entity(_entity("Marble Ledger"))
        store.create_entity(_entity("Violet Harbor"))
        text = "Quixotic zephyrs undulate beneath opaque nebulae."

        with pytest.raises(InputRequiredError) as excinfo:
            verify_fidelity_op(VerifyFidelityInput(text=text), multi)

        assert excinfo.value.code == "INPUT_REQUIRED"
        message = str(excinfo.value)
        assert "entityIds" in message
        assert "hybrid-search" in message

    def test_empty_graph_refuses_rather_than_scoring_zero_entities(self, multi: MultiGraph) -> None:
        with pytest.raises(InputRequiredError):
            verify_fidelity_op(VerifyFidelityInput(text="Anything at all."), multi)


class TestEmptyEntityIdsListIsTreatedAsOmitted:
    """An explicit ``entityIds: []`` signals "no scope given" exactly like
    omitting the field — it must not slip through fidelity's own truthy
    check (``if entity_ids:``) back into whole-graph scoring."""

    def test_empty_list_auto_scopes_same_as_omitted(self, multi: MultiGraph) -> None:
        store = multi.get_store()
        relevant_ids = {
            name: store.create_entity(_entity(name)).id
            for name in ["Copper Relay", "Signal Buffer", "Thermal Core"]
        }
        store.create_entity(_entity("Marble Ledger"))
        text = "The Copper Relay, the Signal Buffer, and the Thermal Core."

        omitted = verify_fidelity_op(VerifyFidelityInput(text=text), multi)
        empty = verify_fidelity_op(VerifyFidelityInput(text=text, entityIds=[]), multi)

        for result in (omitted, empty):
            assert [n["code"] for n in result["notices"]] == ["AUTO_SCOPED"]
            scoped_ids = {g["entityId"] for g in result["entityGroundings"]}
            assert scoped_ids == set(relevant_ids.values())

    def test_empty_list_refuses_same_as_omitted_when_nothing_matches(
        self, multi: MultiGraph
    ) -> None:
        store = multi.get_store()
        store.create_entity(_entity("Marble Ledger"))
        text = "Quixotic zephyrs undulate beneath opaque nebulae."

        with pytest.raises(InputRequiredError):
            verify_fidelity_op(VerifyFidelityInput(text=text, entityIds=[]), multi)


class TestScopedPathIsUnchanged:
    """TL-479 (scoring quality) owns the scoped path's semantics; this ticket
    must not touch them. Pinned two ways: identical scores to calling
    fidelity's core directly, and the auto-scope retrieval is provably never
    invoked (a monkeypatch that would fail the test if it were)."""

    def test_explicit_entity_ids_produce_the_prior_scores_and_no_notice(
        self, multi: MultiGraph
    ) -> None:
        store = multi.get_store()
        ids = {
            name: store.create_entity(_entity(name)).id
            for name in ["Copper Relay", "Signal Buffer", "Thermal Core"]
        }
        store.create_entity(_entity("Distractor"))
        store.create_relation(
            RelationCreate.model_validate(
                {"from": ids["Copper Relay"], "to": ids["Signal Buffer"], "relationType": "causes"}
            )
        )
        text = "The Copper Relay feeds directly into the Signal Buffer."
        scoped_ids = [ids["Copper Relay"], ids["Signal Buffer"]]

        direct = verify_fidelity_core(
            text, store.list_entity_docs(), store.list_relation_docs(), entity_ids=scoped_ids
        )
        via_op = verify_fidelity_op(VerifyFidelityInput(text=text, entityIds=scoped_ids), multi)

        assert "notices" not in via_op
        assert {k: v for k, v in via_op.items() if k != "notices"} == direct

    def test_auto_scope_retrieval_is_never_invoked_when_entity_ids_given(
        self, multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(*_args: object, **_kwargs: object) -> object:
            raise AssertionError("find_anchors must not run when entityIds is given")

        monkeypatch.setattr("theloom.operations.synthesis.find_anchors", _boom)
        store = multi.get_store()
        entity = store.create_entity(_entity("Copper Relay"))

        result = verify_fidelity_op(
            VerifyFidelityInput(text="The Copper Relay.", entityIds=[entity.id]), multi
        )

        assert "notices" not in result
