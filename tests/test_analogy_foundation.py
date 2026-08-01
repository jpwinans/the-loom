"""Unit tests for the analogy / far-analogy foundation ports.

Inline fixtures only — no FalkorDB. Numbers are computed by hand; the
sliced-Wasserstein path is rank-only so those asserts check structure/ordering
and edge cases, not absolute values.
"""

from __future__ import annotations

from typing import Any

import pytest

from theloom.analysis.absence_surprise import (
    compute_completeness_coefficient,
    compute_type_pattern_score,
    score_transfer_absences,
)
from theloom.analysis.adaptability import (
    assess_adaptability,
    assess_transfer_adaptability,
    compute_consistency_score,
    compute_incremental_score,
    compute_pragmatic_score,
)
from theloom.analysis.analogy_confidence import compute_analogy_confidence
from theloom.analysis.component_signatures import (
    compare_component_signatures,
    compute_all_component_signatures,
    compute_semantic_distance,
    find_far_analogy_candidates,
)
from theloom.analysis.cwsg import cwsg_transfer
from theloom.analysis.sliced_wasserstein import (
    compare_semantic_component_signatures,
    find_semantic_far_analogy_candidates,
    sliced_wasserstein_distance,
)
from theloom.graph.hydrate import hydrate_graph


def _entity(eid: str, name: str, etype: str = "concept") -> dict[str, Any]:
    return {"id": eid, "name": name, "entityType": etype, "observations": []}


def _relation(rid: str, src: str, dst: str, rtype: str) -> dict[str, Any]:
    return {"id": rid, "from": src, "to": dst, "relationType": rtype}


# =============================================================================
# analogy_confidence
# =============================================================================


def test_confidence_two_signal_default() -> None:
    # (0.6*0.8 + 0.4*0.6) / 1.0
    assert compute_analogy_confidence(0.8, 0.6) == pytest.approx(0.72)


def test_confidence_three_signal_interestingness() -> None:
    # w 0.4/0.3/0.3: 0.32 + 0.18 + 0.12
    assert compute_analogy_confidence(0.8, 0.6, 0.4) == pytest.approx(0.62)


def test_confidence_three_signal_purpose() -> None:
    # purpose only (interestingness None): w1=0.4, w2=0.3, w4=0.3
    assert compute_analogy_confidence(0.8, 0.6, None, None, 0.4) == pytest.approx(0.62)


def test_confidence_four_signal() -> None:
    # 0.3*0.8 + 0.2*0.6 + 0.25*0.4 + 0.25*0.2
    assert compute_analogy_confidence(0.8, 0.6, 0.4, None, 0.2) == pytest.approx(0.51)


def test_confidence_clamps_inputs() -> None:
    # sp clamped to 1, ss clamped to 0 -> (0.6*1 + 0.4*0)/1
    assert compute_analogy_confidence(2.0, -1.0) == pytest.approx(0.6)


def test_confidence_zero_total_weight_returns_zero() -> None:
    assert compute_analogy_confidence(0.9, 0.9, weights={"w1": 0.0, "w2": 0.0}) == 0.0


# =============================================================================
# cwsg_transfer
# =============================================================================


def _mapping_result() -> dict[str, Any]:
    # Source s1->s2->s3 (attracts); target t1->t2 (attracts). s3 is unmapped.
    return {
        "sourceDomain": "physics",
        "targetDomain": "atom",
        "mappings": [
            {"sourceId": "s1", "targetId": "t1", "targetName": "Nucleus"},
            {"sourceId": "s2", "targetId": "t2", "targetName": "Electron"},
        ],
        "unmapped": [
            {
                "entityId": "s3",
                "entityName": "Moon",
                "entityType": "src",
                "domain": "source",
            }
        ],
        "quality": {"structuralPreservation": 0.5},
        "sourceRelations": [
            _relation("sr1", "s1", "s2", "attracts"),
            _relation("sr2", "s2", "s3", "attracts"),
        ],
        "targetRelations": [_relation("tr1", "t1", "t2", "attracts")],
    }


def test_cwsg_pure_generates_novel_proposal() -> None:
    result = cwsg_transfer(_mapping_result(), {})

    assert result["totalSourceRelations"] == 2
    assert result["systematicityExcluded"] == 0
    assert result["temperature"] == 0.0

    # sr1 (s1->s2) exists in target already -> skipped; only sr2 (s2->s3) copied.
    assert len(result["copiedRelations"]) == 1
    assert result["copiedRelations"][0]["sourceToId"] == "s3"

    assert len(result["substitutedRelations"]) == 1
    sub = result["substitutedRelations"][0]
    assert sub["targetFromId"] == "t2"
    assert sub["targetToId"] == "__NOVEL__s3"
    assert sub["fromIsNovel"] is False
    assert sub["toIsNovel"] is True

    assert len(result["proposals"]) == 1
    proposal = result["proposals"][0]
    assert proposal["entity"]["name"] == "Moon (analogy)"
    assert proposal["entity"]["entityType"] == "src"
    assert proposal["strategy"] == "analogy_transfer"
    # 2-signal confidence: (0.6*0.5 + 0.4*0.5)/1
    assert proposal["confidence"] == pytest.approx(0.5)
    assert proposal["relations"] == [
        {"targetId": "t2", "relationType": "attracts", "direction": "incoming"}
    ]
    assert proposal["entity"]["observations"] == [
        "Generated by analogy transfer from physics to atom",
        "Source entity: s3",
        "Inferred from source relation: s2 -[attracts]-> s3",
    ]
    assert proposal["rationale"] == (
        'CWSG analogy transfer: source entity "s3" has structural role in physics '
        "with no target correspondence in atom"
    )

    # Optional keys omitted when undefined.
    assert "slippageAugmentations" not in result
    assert "absenceSurprise" not in result
    assert "adaptability" not in result


def test_cwsg_absence_surprise_integration() -> None:
    entities = [_entity("t1", "Nucleus", "tgt"), _entity("t2", "Electron", "tgt")]
    relations = [_relation("tr1", "t1", "t2", "attracts")]
    result = cwsg_transfer(
        _mapping_result(),
        {"computeAbsenceSurprise": True, "allEntities": entities, "allRelations": relations},
    )
    assert "absenceSurprise" in result
    assert set(result["absenceSurprise"]) == {
        "overallScore",
        "meanScore",
        "schemaAbsences",
        "instanceAbsences",
    }


def test_cwsg_adaptability_filter_tags_and_returns() -> None:
    result = cwsg_transfer(
        _mapping_result(),
        {
            "assessAdaptability": True,
            "targetEntityIds": {"t1", "t2"},
            "targetRelationsForAdaptability": [],
            "entityTypeMap": {"t1": "tgt", "t2": "tgt"},
        },
    )
    assert "adaptability" in result
    # One proposal, connected to existing t2 -> not rejected -> survives.
    assert len(result["adaptability"]) == len(result["proposals"]) == 1
    assert result["adaptability"][0]["decision"] in {"accept", "warn"}


# =============================================================================
# component_signatures
# =============================================================================


def _two_isomorphic_components() -> Any:
    entities = [
        _entity("a1", "Alpha"),
        _entity("b1", "Beta"),
        _entity("a2", "Gamma"),
        _entity("b2", "Delta"),
    ]
    relations = [
        _relation("r1", "a1", "b1", "causes"),
        _relation("r2", "a2", "b2", "causes"),
    ]
    return hydrate_graph(entities, relations)


def test_all_component_signatures_shape_and_sort() -> None:
    graph = _two_isomorphic_components()
    result = compute_all_component_signatures(graph)

    assert result["componentCount"] == 2
    assert len(result["signatures"]) == 2
    for sig in result["signatures"]:
        assert sig["entityCount"] == 2
        assert len(sig["signatureVector"]) == len(result["globalHashOrder"])
    # Sorted by componentId asc when entityCount ties.
    ids = [s["componentId"] for s in result["signatures"]]
    assert ids == sorted(ids)


def test_isomorphic_components_have_cosine_one() -> None:
    graph = _two_isomorphic_components()
    sigs = compute_all_component_signatures(graph)["signatures"]
    assert compare_component_signatures(sigs[0], sigs[1]) == pytest.approx(1.0)


def test_compare_signatures_length_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="different lengths"):
        compare_component_signatures({"signatureVector": [1.0, 0.0]}, {"signatureVector": [1.0]})


def test_semantic_distance_jaccard() -> None:
    # No shared tokens -> distance 1.0
    assert compute_semantic_distance(["Alpha", "Beta"], ["Gamma", "Delta"]) == pytest.approx(1.0)
    # Full overlap -> distance 0.0
    assert compute_semantic_distance(["shared term"], ["shared term"]) == pytest.approx(0.0)
    # Both empty -> 0.0
    assert compute_semantic_distance([], []) == 0.0


def test_find_far_analogy_candidates() -> None:
    graph = _two_isomorphic_components()
    result = compute_all_component_signatures(graph)
    sigs = result["signatures"]
    component_entities = {
        sigs[0]["componentId"]: ["Alpha", "Beta"],
        sigs[1]["componentId"]: ["Gamma", "Delta"],
    }
    candidates = find_far_analogy_candidates(sigs, {"componentEntities": component_entities})

    assert len(candidates) == 1
    cand = candidates[0]
    assert cand["structuralSimilarity"] == pytest.approx(1.0)
    assert cand["semanticDissimilarity"] == pytest.approx(1.0)
    assert cand["farAnalogyScore"] == pytest.approx(1.0)
    assert set(cand) == {
        "sourceComponent",
        "targetComponent",
        "structuralSimilarity",
        "semanticDissimilarity",
        "farAnalogyScore",
    }


def test_find_far_analogy_candidates_below_threshold_skipped() -> None:
    graph = _two_isomorphic_components()
    sigs = compute_all_component_signatures(graph)["signatures"]
    # structuralSimilarity is 1.0; a threshold above that yields no candidates.
    assert find_far_analogy_candidates(sigs, {"minStructuralSimilarity": 1.1}) == []


# =============================================================================
# sliced_wasserstein
# =============================================================================


def test_swd_both_empty() -> None:
    assert sliced_wasserstein_distance([], []) == 0.0


def test_swd_one_empty_mean_l2_norm() -> None:
    # Single row [3, 4] -> L2 norm 5.0
    assert sliced_wasserstein_distance([[3.0, 4.0]], []) == pytest.approx(5.0)
    assert sliced_wasserstein_distance([], [[3.0, 4.0]]) == pytest.approx(5.0)


def test_swd_zero_dimension() -> None:
    assert sliced_wasserstein_distance([[]], [[]]) == 0.0


def test_swd_identical_distributions_is_zero() -> None:
    matrix = [[1.0, 2.0], [3.0, 4.0]]
    assert sliced_wasserstein_distance(matrix, matrix) == pytest.approx(0.0, abs=1e-9)


def test_swd_different_distributions_positive() -> None:
    assert sliced_wasserstein_distance([[0.0, 0.0]], [[3.0, 4.0]]) > 0.0


def test_semantic_far_analogy_candidates() -> None:
    sigs = [
        {"componentId": "c1", "signatureMatrix": [[0.0, 0.0]]},
        {"componentId": "c2", "signatureMatrix": [[3.0, 4.0]]},
    ]
    candidates = find_semantic_far_analogy_candidates(sigs)
    assert len(candidates) == 1
    cand = candidates[0]
    assert cand["farAnalogyScore"] == cand["semanticDistance"]
    assert cand["semanticDistance"] > 0.0
    assert set(cand) == {
        "sourceComponent",
        "targetComponent",
        "semanticDistance",
        "farAnalogyScore",
    }


def test_semantic_far_analogy_single_signature_empty() -> None:
    assert (
        find_semantic_far_analogy_candidates([{"componentId": "c1", "signatureMatrix": [[1.0]]}])
        == []
    )


def test_compare_semantic_both_empty_matrices() -> None:
    assert (
        compare_semantic_component_signatures({"signatureMatrix": []}, {"signatureMatrix": []})
        == 0.0
    )


# =============================================================================
# adaptability
# =============================================================================


def test_incremental_empty_is_one() -> None:
    assert compute_incremental_score([]) == 1.0


def test_incremental_half_novel() -> None:
    subs = [
        {"targetFromId": "t1", "targetToId": "__NOVEL__x", "fromIsNovel": False, "toIsNovel": True},
    ]
    # endpoints {t1, __NOVEL__x}; novel {__NOVEL__x} -> 1 - 1/2
    assert compute_incremental_score(subs) == pytest.approx(0.5)


def test_consistency_empty_substituted_is_one() -> None:
    assert compute_consistency_score([], [_relation("r", "a", "b", "causes")], {}) == 1.0


def test_consistency_non_empty_subs_no_targets_is_zero() -> None:
    subs = [
        {
            "targetFromId": "t1",
            "targetToId": "t2",
            "fromIsNovel": False,
            "toIsNovel": False,
            "relationType": "causes",
        },
    ]
    assert compute_consistency_score(subs, [], {}) == 0.0


def test_pragmatic_connectedness() -> None:
    proposal = {
        "relations": [
            {"targetId": "x", "relationType": "causes", "direction": "outgoing"},
            {"targetId": "y", "relationType": "causes", "direction": "outgoing"},
        ]
    }
    # 1 of 2 connected -> max(0.5, 0.5) = 0.5
    assert compute_pragmatic_score(proposal, {"x"}) == pytest.approx(0.5)
    # both connected -> max(1.0, 0.5)
    assert compute_pragmatic_score(proposal, {"x", "y"}) == pytest.approx(1.0)
    # no relations -> max(0.5, purposeRelevance)
    assert compute_pragmatic_score({"relations": []}, set()) == pytest.approx(0.5)


def test_assess_adaptability_gate_decisions() -> None:
    assert assess_adaptability(0.6, 0.6, 0.6)["decision"] == "accept"
    assert assess_adaptability(0.4, 0.4, 0.4)["decision"] == "warn"
    reject = assess_adaptability(0.2, 0.2, 0.2)
    assert reject["decision"] == "reject"
    assert reject["overallScore"] == pytest.approx(0.2)
    assert (
        "Weak signals: pragmatic centrality, incremental preference, structural consistency"
        in (reject["reasoning"])
    )


def test_assess_transfer_adaptability_batch() -> None:
    proposals = [
        {"relations": [{"targetId": "t1", "relationType": "causes", "direction": "outgoing"}]}
    ]
    results = assess_transfer_adaptability(proposals, [], [], {"t1"}, {})
    assert len(results) == 1
    # empty subs -> incremental 1.0, consistency 1.0; pragmatic 1.0 -> overall 1.0
    assert results[0]["overallScore"] == pytest.approx(1.0)
    assert results[0]["decision"] == "accept"


# =============================================================================
# absence_surprise
# =============================================================================


def test_type_pattern_score() -> None:
    entities = [_entity("t1", "A"), _entity("t2", "B")]
    relations = [_relation("r1", "t1", "t2", "causes")]
    # 1 actual / (2*1) possible directed concept->concept pairs
    assert compute_type_pattern_score(relations, entities, "concept", "concept", "causes") == (
        pytest.approx(0.5)
    )
    assert compute_type_pattern_score(relations, entities, "concept", "concept", "enables") == 0.0


def test_completeness_coefficient() -> None:
    entities = [_entity("t1", "A"), _entity("t2", "B")]
    relations = [_relation("r1", "t1", "t2", "causes")]
    # t1 touches 1 relation; avg over 2 concept entities = 2/2 = 1 -> min(1, 1/1)
    assert compute_completeness_coefficient(relations, entities, "t1") == pytest.approx(1.0)


def test_score_transfer_absences() -> None:
    entities = [_entity("t1", "Nucleus", "tgt"), _entity("t2", "Electron", "tgt")]
    relations = [_relation("r1", "t1", "t2", "causes")]
    transfer_result = {
        "substitutedRelations": [
            {
                "sourceRelation": _relation("s1", "src_a", "src_b", "enables"),
                "targetFromId": "t1",
                "targetToId": "__NOVEL__srcX",
                "fromIsNovel": False,
                "toIsNovel": True,
                "relationType": "enables",
            }
        ]
    }
    mapping_result = {"mappings": [{"targetId": "t1"}, {"targetId": "t2"}]}

    result = score_transfer_absences(entities, relations, transfer_result, mapping_result)

    # Schema absence: "enables" present in source substitutions, absent in target.
    assert result["schemaAbsences"] == [
        {"relationType": "enables", "score": 0.8, "sourceCount": 1, "targetCount": 0}
    ]

    assert len(result["instanceAbsences"]) == 1
    inst = result["instanceAbsences"][0]
    assert inst["sourceRelationId"] == "s1"
    assert inst["predictedFromId"] == "t1"
    assert inst["predictedToId"] is None
    # No "enables" edges among concepts -> pStructural 0; prior 0.8; completeness 1.0 -> score 0
    assert inst["pStructural"] == pytest.approx(0.0)
    assert inst["pTransfer"] == pytest.approx(0.8)
    assert inst["cCompleteness"] == pytest.approx(1.0)
    assert inst["score"] == pytest.approx(0.0)

    # overall = max(0.8, 0.0); mean = (0.8 + 0.0)/2
    assert result["overallScore"] == pytest.approx(0.8)
    assert result["meanScore"] == pytest.approx(0.4)


def test_score_transfer_absences_empty_substituted() -> None:
    result = score_transfer_absences([], [], {"substitutedRelations": []}, {"mappings": []})
    assert result == {
        "overallScore": 0,
        "meanScore": 0,
        "schemaAbsences": [],
        "instanceAbsences": [],
    }
