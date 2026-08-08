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

Fix (auto-scope, chosen over an outright refusal): reuse the exact
``find_anchors``/``anchor_search_for`` hybrid-search core that
``synthesize``/``plan_synthesis``/``traverse_synthesis`` already use for
anchor selection — no reimplementation. An omitted (or empty) ``entityIds``
scopes to that retrieval's picks and carries an ``AUTO_SCOPED`` notice (the
shared ``theloom.operations.notices`` convention) naming the count and the
selected entities; a hint says ``entityIds`` can be passed explicitly. If
retrieval finds nothing to scope to (no embeddings and no keyword overlap),
the command refuses with ``INPUT_REQUIRED`` naming ``entityIds`` and the
hybrid-search-then-verify two-step, rather than falling through to a
whole-graph or an empty-scope score.

These tests pin: (1) the unscoped call never returns a whole-graph
verdict with no notice, in any case; (2) the auto-scoped path meaningfully
outperforms the old whole-graph behavior on a graph shaped like the reported
bug (a handful of relevant entities lost in many irrelevant ones); (3) the
graceful keyword fallback when entities carry no embeddings; (4) the refusal
when nothing matches; (5) the scoped path (explicit ``entityIds``) is
completely untouched — same scores, no notice, and the auto-scope retrieval
is never even invoked.
"""

from __future__ import annotations

import pytest

from tests.fakes import FakeEmbedder
from theloom.errors import InputRequiredError
from theloom.model import EntityCreate, RelationCreate
from theloom.operations.synthesis import VerifyFidelityInput
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
    # ("scoped to 7 relevant entityIds scored 0.541/moderate"). With
    # MAX_ANCHORS=10, auto-scoping fills the remaining 3 slots with
    # tie-broken distractors, so the grounding rate is deterministically
    # 7/10 regardless of which distractors those are.
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
        assert len(scoped_ids) == MAX_ANCHORS  # not the whole 55-entity graph
        assert set(relevant_ids.values()) <= scoped_ids  # the 7 relevant always rank in

        # Deterministic regardless of which distractors tie-broke into the
        # remaining anchor slots: no distractor has any relation, and no
        # distractor's name occurs in TEXT, so grounding/relation counts are
        # pinned to the 7 relevant entities and their 6 chain relations.
        assert result["scores"]["entityGroundingRate"] == pytest.approx(0.7)
        assert result["scores"]["relationPreservationRate"] == pytest.approx(1.0)
        assert result["level"] in ("moderate", "high")
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
