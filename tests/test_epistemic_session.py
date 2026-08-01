"""Session scoping for the 13 list-style epistemic queries.

``session`` was added to the shared ``EpistemicQueryInput`` base (so every
query built on it inherits the parameter for free) and, for the two queries
with their own input model (``provenance-chain``, ``claims-from-source``),
added directly. Each query threads the session through to ``_list()`` /
``matches_session()`` exactly like ``list-entities`` does: the first-class
``session`` field, with the legacy ``"subgraph: {sid}-{qid}"`` observation tag
accepted as a fallback. Omitting ``session`` must leave every query's
behavior unchanged.
"""

from __future__ import annotations

from typing import Any

import pytest
from falkordb import FalkorDB
from redis import Redis

from theloom.operations.entity import CreateEntityInput, create_entity
from theloom.operations.epistemic import (
    AnsweredQuestionsInput,
    BlockingQuestionsInput,
    ClaimsFromSourceInput,
    EpistemicQueryInput,
    MostCertainInput,
    NeedsEvidenceInput,
    ProvenanceChainInput,
    StaleBeliefsInput,
    TypedEpistemicInput,
    UncertainClaimsInput,
    answered_questions,
    blocking_questions,
    claims_from_source,
    contested_claims,
    inferred_claims,
    most_certain,
    needs_evidence,
    open_questions,
    provenance_chain,
    single_source_claims,
    stale_beliefs,
    uncertain_claims,
    unprovenanced,
)
from theloom.operations.relations import CreateRelationInput, create_relation
from theloom.store.multigraph import MultiGraph

S1 = "session-1"
S2 = "session-2"
LEGACY_SID = "sid"


@pytest.fixture()
def multi(db: FalkorDB, redis_client: Redis, namespace: str) -> MultiGraph:
    return MultiGraph(db, redis_client, default_graph="default", key_prefix=namespace)


def make_entity(multi: MultiGraph, name: str, **overrides: object) -> dict[str, Any]:
    base: dict[str, object] = {
        "name": name,
        "entityType": "concept",
        "observations": [f"observation about {name}"],
    }
    base.update(overrides)
    result = create_entity(CreateEntityInput.model_validate(base), multi)
    assert isinstance(result, dict)
    return result


def make_relation(
    multi: MultiGraph, from_id: str, to_id: str, **overrides: object
) -> dict[str, Any]:
    base: dict[str, object] = {
        "from": from_id,
        "to": to_id,
        "relationType": "supports",
        "polarity": None,
        "strength": "moderate",
        "evidence": None,
    }
    base.update(overrides)
    return create_relation(CreateRelationInput.model_validate(base), multi)


def make_four(
    multi: MultiGraph, label: str, **overrides: object
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Four otherwise-identical entities: field-tagged session-1, field-tagged
    session-2, legacy-tagged ``sid``, and untagged — the standard fixture for
    proving field-tag scoping, legacy-tag scoping, and unscoped pass-through
    in one shot."""
    common: dict[str, object] = dict(overrides)
    observations = list(common.pop("observations", [f"observation about {label}"]))  # type: ignore[arg-type]
    tagged = make_entity(multi, f"{label} S1", session=S1, observations=observations, **common)
    other = make_entity(multi, f"{label} S2", session=S2, observations=observations, **common)
    legacy = make_entity(
        multi, f"{label} Legacy", observations=[*observations, f"subgraph: {LEGACY_SID}"], **common
    )
    plain = make_entity(multi, f"{label} Plain", observations=observations, **common)
    return tagged, other, legacy, plain


# =============================================================================
# Simple entity-list queries (uncertain-claims, stale-beliefs, most-certain,
# inferred-claims, unprovenanced, open-questions)
# =============================================================================


def test_uncertain_claims_scopes_by_session(multi: MultiGraph) -> None:
    conf = {"score": 0.1, "basis": "direct_observation"}
    tagged, other, legacy, plain = make_four(
        multi, "Uncertain", entityType="claim", confidence=conf
    )

    scoped = uncertain_claims(UncertainClaimsInput.model_validate({"session": S1}), multi)
    assert [r["entity"]["id"] for r in scoped] == [tagged["id"]]

    legacy_scoped = uncertain_claims(
        UncertainClaimsInput.model_validate({"session": LEGACY_SID}), multi
    )
    assert [r["entity"]["id"] for r in legacy_scoped] == [legacy["id"]]

    unscoped = uncertain_claims(UncertainClaimsInput.model_validate({}), multi)
    assert {r["entity"]["id"] for r in unscoped} == {
        tagged["id"],
        other["id"],
        legacy["id"],
        plain["id"],
    }


def test_stale_beliefs_scopes_by_session(multi: MultiGraph) -> None:
    # No confidence at all => "never evaluated" => always stale.
    tagged, other, legacy, plain = make_four(multi, "Stale")

    scoped = stale_beliefs(StaleBeliefsInput.model_validate({"session": S1}), multi)
    assert [r["entity"]["id"] for r in scoped] == [tagged["id"]]

    legacy_scoped = stale_beliefs(StaleBeliefsInput.model_validate({"session": LEGACY_SID}), multi)
    assert [r["entity"]["id"] for r in legacy_scoped] == [legacy["id"]]

    unscoped = stale_beliefs(StaleBeliefsInput.model_validate({}), multi)
    assert {r["entity"]["id"] for r in unscoped} == {
        tagged["id"],
        other["id"],
        legacy["id"],
        plain["id"],
    }


def test_most_certain_scopes_by_session(multi: MultiGraph) -> None:
    conf = {"score": 0.95, "basis": "direct_observation"}
    tagged, other, legacy, plain = make_four(multi, "Certain", confidence=conf)

    scoped = most_certain(MostCertainInput.model_validate({"session": S1}), multi)
    assert [r["entity"]["id"] for r in scoped] == [tagged["id"]]

    legacy_scoped = most_certain(MostCertainInput.model_validate({"session": LEGACY_SID}), multi)
    assert [r["entity"]["id"] for r in legacy_scoped] == [legacy["id"]]

    unscoped = most_certain(MostCertainInput.model_validate({}), multi)
    assert {r["entity"]["id"] for r in unscoped} == {
        tagged["id"],
        other["id"],
        legacy["id"],
        plain["id"],
    }


def test_inferred_claims_scopes_by_session(multi: MultiGraph) -> None:
    conf = {"score": 0.5, "basis": "inference"}
    tagged, other, legacy, plain = make_four(multi, "Inferred", confidence=conf)

    scoped = inferred_claims(TypedEpistemicInput.model_validate({"session": S1}), multi)
    assert [e["id"] for e in scoped] == [tagged["id"]]

    legacy_scoped = inferred_claims(
        TypedEpistemicInput.model_validate({"session": LEGACY_SID}), multi
    )
    assert [e["id"] for e in legacy_scoped] == [legacy["id"]]

    unscoped = inferred_claims(TypedEpistemicInput.model_validate({}), multi)
    assert {e["id"] for e in unscoped} == {
        tagged["id"],
        other["id"],
        legacy["id"],
        plain["id"],
    }


def test_unprovenanced_scopes_by_session(multi: MultiGraph) -> None:
    # None of the entities have a provenance argument, so all qualify.
    tagged, other, legacy, plain = make_four(multi, "Unprovenanced")

    scoped = unprovenanced(TypedEpistemicInput.model_validate({"session": S1}), multi)
    assert [e["id"] for e in scoped] == [tagged["id"]]

    legacy_scoped = unprovenanced(
        TypedEpistemicInput.model_validate({"session": LEGACY_SID}), multi
    )
    assert [e["id"] for e in legacy_scoped] == [legacy["id"]]

    unscoped = unprovenanced(TypedEpistemicInput.model_validate({}), multi)
    assert {e["id"] for e in unscoped} == {
        tagged["id"],
        other["id"],
        legacy["id"],
        plain["id"],
    }


def test_open_questions_scopes_by_session(multi: MultiGraph) -> None:
    tagged, other, legacy, plain = make_four(multi, "Question", entityType="question")

    scoped = open_questions(EpistemicQueryInput.model_validate({"session": S1}), multi)
    assert [e["id"] for e in scoped] == [tagged["id"]]

    legacy_scoped = open_questions(
        EpistemicQueryInput.model_validate({"session": LEGACY_SID}), multi
    )
    assert [e["id"] for e in legacy_scoped] == [legacy["id"]]

    unscoped = open_questions(EpistemicQueryInput.model_validate({}), multi)
    assert {e["id"] for e in unscoped} == {
        tagged["id"],
        other["id"],
        legacy["id"],
        plain["id"],
    }


# =============================================================================
# Queries needing "claim" entities with sources/supports/contradicts relations
# =============================================================================


def test_needs_evidence_scopes_by_session(multi: MultiGraph) -> None:
    tagged, other, legacy, plain = make_four(multi, "NeedsEvidence", entityType="claim")

    scoped = needs_evidence(NeedsEvidenceInput.model_validate({"session": S1}), multi)
    assert [r["entity"]["id"] for r in scoped] == [tagged["id"]]

    legacy_scoped = needs_evidence(
        NeedsEvidenceInput.model_validate({"session": LEGACY_SID}), multi
    )
    assert [r["entity"]["id"] for r in legacy_scoped] == [legacy["id"]]

    unscoped = needs_evidence(NeedsEvidenceInput.model_validate({}), multi)
    assert {r["entity"]["id"] for r in unscoped} == {
        tagged["id"],
        other["id"],
        legacy["id"],
        plain["id"],
    }


def test_single_source_claims_scopes_by_session(multi: MultiGraph) -> None:
    tagged, other, legacy, plain = make_four(multi, "SingleSource", entityType="claim")
    src = make_entity(multi, "The Source", entityType="source")
    for claim in (tagged, other, legacy, plain):
        make_relation(multi, claim["id"], src["id"], relationType="sources")

    scoped = single_source_claims(EpistemicQueryInput.model_validate({"session": S1}), multi)
    assert [c["id"] for c in scoped] == [tagged["id"]]

    legacy_scoped = single_source_claims(
        EpistemicQueryInput.model_validate({"session": LEGACY_SID}), multi
    )
    assert [c["id"] for c in legacy_scoped] == [legacy["id"]]

    unscoped = single_source_claims(EpistemicQueryInput.model_validate({}), multi)
    assert {c["id"] for c in unscoped} == {
        tagged["id"],
        other["id"],
        legacy["id"],
        plain["id"],
    }


def test_contested_claims_scopes_by_session(multi: MultiGraph) -> None:
    tagged, other, legacy, plain = make_four(multi, "Contested", entityType="claim")
    for claim in (tagged, other, legacy, plain):
        supporter = make_entity(multi, f"Supporter of {claim['id']}", entityType="evidence")
        opposer = make_entity(multi, f"Opposer of {claim['id']}", entityType="evidence")
        make_relation(multi, supporter["id"], claim["id"], relationType="supports")
        make_relation(multi, opposer["id"], claim["id"], relationType="contradicts")

    scoped = contested_claims(EpistemicQueryInput.model_validate({"session": S1}), multi)
    assert [r["entity"]["id"] for r in scoped] == [tagged["id"]]

    legacy_scoped = contested_claims(
        EpistemicQueryInput.model_validate({"session": LEGACY_SID}), multi
    )
    assert [r["entity"]["id"] for r in legacy_scoped] == [legacy["id"]]

    unscoped = contested_claims(EpistemicQueryInput.model_validate({}), multi)
    assert {r["entity"]["id"] for r in unscoped} == {
        tagged["id"],
        other["id"],
        legacy["id"],
        plain["id"],
    }


def test_claims_from_source_scopes_by_session(multi: MultiGraph) -> None:
    src = make_entity(multi, "Shared Source", entityType="source")
    tagged, other, legacy, plain = make_four(multi, "CitesSource", entityType="claim")
    for claim in (tagged, other, legacy, plain):
        make_relation(multi, claim["id"], src["id"], relationType="sources")

    scoped = claims_from_source(
        ClaimsFromSourceInput.model_validate({"sourceId": src["id"], "session": S1}), multi
    )
    assert [e["id"] for e in scoped] == [tagged["id"]]

    legacy_scoped = claims_from_source(
        ClaimsFromSourceInput.model_validate({"sourceId": src["id"], "session": LEGACY_SID}), multi
    )
    assert [e["id"] for e in legacy_scoped] == [legacy["id"]]

    unscoped = claims_from_source(
        ClaimsFromSourceInput.model_validate({"sourceId": src["id"]}), multi
    )
    assert {e["id"] for e in unscoped} == {
        tagged["id"],
        other["id"],
        legacy["id"],
        plain["id"],
    }


# =============================================================================
# Blocking / answered questions
# =============================================================================


def test_blocking_questions_scopes_by_session(multi: MultiGraph) -> None:
    tagged, other, legacy, plain = make_four(multi, "BlockingQ", entityType="question")
    for question in (tagged, other, legacy, plain):
        blocked = make_entity(multi, f"Blocked by {question['id']}", entityType="concept")
        make_relation(multi, question["id"], blocked["id"], relationType="requires", polarity=None)

    scoped = blocking_questions(BlockingQuestionsInput.model_validate({"session": S1}), multi)
    assert [r["entity"]["id"] for r in scoped] == [tagged["id"]]

    legacy_scoped = blocking_questions(
        BlockingQuestionsInput.model_validate({"session": LEGACY_SID}), multi
    )
    assert [r["entity"]["id"] for r in legacy_scoped] == [legacy["id"]]

    unscoped = blocking_questions(BlockingQuestionsInput.model_validate({}), multi)
    assert {r["entity"]["id"] for r in unscoped} == {
        tagged["id"],
        other["id"],
        legacy["id"],
        plain["id"],
    }


def test_answered_questions_scopes_by_session(multi: MultiGraph) -> None:
    tagged, other, legacy, plain = make_four(multi, "AnsweredQ", entityType="question")
    for question in (tagged, other, legacy, plain):
        answer = make_entity(multi, f"Answer to {question['id']}", entityType="evidence")
        make_relation(multi, answer["id"], question["id"], relationType="supports")

    scoped = answered_questions(AnsweredQuestionsInput.model_validate({"session": S1}), multi)
    assert [e["id"] for e in scoped] == [tagged["id"]]

    legacy_scoped = answered_questions(
        AnsweredQuestionsInput.model_validate({"session": LEGACY_SID}), multi
    )
    assert [e["id"] for e in legacy_scoped] == [legacy["id"]]

    unscoped = answered_questions(AnsweredQuestionsInput.model_validate({}), multi)
    assert {e["id"] for e in unscoped} == {
        tagged["id"],
        other["id"],
        legacy["id"],
        plain["id"],
    }


# =============================================================================
# provenance-chain: session scopes the returned chain, not the traversal —
# the queried entity itself (depth 0) is always included regardless of
# session, matching id-based lookups elsewhere (e.g. needs-evidence's
# claimId path).
# =============================================================================


def test_provenance_chain_scopes_by_session(multi: MultiGraph) -> None:
    start = make_entity(multi, "Chain Start")
    tagged, other, legacy, plain = make_four(multi, "ChainHop")
    for hop in (tagged, other, legacy, plain):
        make_relation(multi, start["id"], hop["id"], relationType="sources")

    scoped = provenance_chain(
        ProvenanceChainInput.model_validate({"entityId": start["id"], "session": S1}), multi
    )
    assert {item["entity"]["id"] for item in scoped} == {start["id"], tagged["id"]}

    legacy_scoped = provenance_chain(
        ProvenanceChainInput.model_validate({"entityId": start["id"], "session": LEGACY_SID}), multi
    )
    assert {item["entity"]["id"] for item in legacy_scoped} == {start["id"], legacy["id"]}

    unscoped = provenance_chain(
        ProvenanceChainInput.model_validate({"entityId": start["id"]}), multi
    )
    assert {item["entity"]["id"] for item in unscoped} == {
        start["id"],
        tagged["id"],
        other["id"],
        legacy["id"],
        plain["id"],
    }
