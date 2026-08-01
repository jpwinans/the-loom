"""Unit tests for the entity-proposal foundation layer (interestingness,
capability-spec, fingerprint, deduplication-gate, entity-proposer).

All fixtures are inline plain dicts over a tiny in-memory FakeStore — no live
FalkorGraphStore, no DB.
"""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

from theloom.analysis.interestingness import (
    compute_compression_progress,
    compute_interestingness,
    compute_structural_novelty,
    compute_subjective_information_density,
)
from theloom.graph.hydrate import hydrate_graph
from theloom.reification.fingerprint import (
    compute_fingerprint,
    describe_fingerprint,
    group_by_fingerprint,
    neighborhood_meta,
)
from theloom.semantic.deduplication_gate import deduplicate_proposals, proposal_to_text
from theloom.semantic.entity_proposer import propose_entities
from theloom.verification.capability_spec import CapabilitySpec

Doc = dict[str, Any]


# =============================================================================
# Fake store
# =============================================================================


class FakeStore:
    """In-memory store exposing the read surface the foundation modules use."""

    def __init__(
        self,
        entities: list[Doc],
        relations: list[Doc],
        vectors: dict[str, list[float]] | None = None,
    ) -> None:
        self._entities = entities
        self._relations = relations
        self._vectors = vectors or {}

    def list_entities(self, _filter: Any = None) -> list[Doc]:
        return list(self._entities)

    def list_relations(self, _filter: Any = None) -> list[Doc]:
        return list(self._relations)

    def get_entity_vectors(self) -> dict[str, list[float]]:
        return dict(self._vectors)

    def read_entity(self, entity_id: str) -> Doc | None:
        return next((e for e in self._entities if e["id"] == entity_id), None)


def _entity(
    entity_id: str, name: str, entity_type: str, observations: list[str] | None = None
) -> Doc:
    return {
        "id": entity_id,
        "name": name,
        "entityType": entity_type,
        "observations": observations or [],
    }


def _relation(from_id: str, to_id: str, relation_type: str) -> Doc:
    return {
        "id": f"{from_id}->{to_id}:{relation_type}",
        "from": from_id,
        "to": to_id,
        "relationType": relation_type,
    }


class FakeEmbeddingManager:
    """Duck-typed embedder returning a fixed vector per text."""

    def __init__(self, table: dict[str, list[float]]) -> None:
        self._table = table

    def generate_embedding(self, text: str) -> list[float]:
        return self._table.get(text, [0.0, 0.0, 0.0])


# =============================================================================
# Interestingness
# =============================================================================


class TestSubjectiveInformationDensity:
    def test_unavailable_returns_neutral(self) -> None:
        assert compute_subjective_information_density(None, []) == 0.5
        assert compute_subjective_information_density([1.0, 0.0, 0.0], []) == 0.5

    def test_identical_is_zero_novelty(self) -> None:
        assert compute_subjective_information_density([1.0, 0.0, 0.0], [[1.0, 0.0, 0.0]]) == 0.0

    def test_orthogonal_is_max_novelty(self) -> None:
        assert compute_subjective_information_density([1.0, 0.0, 0.0], [[0.0, 1.0, 0.0]]) == 1.0

    def test_knn_takes_nearest(self) -> None:
        # Nearest neighbor (k=1) is the identical vector -> similarity 1 -> SI 0.
        existing = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
        assert compute_subjective_information_density([1.0, 0.0, 0.0], existing, k=1) == 0.0
        # k=2 averages 1.0 and 0.0 -> mean 0.5 -> SI 0.5.
        assert compute_subjective_information_density([1.0, 0.0, 0.0], existing, k=2) == 0.5


class TestCompressionProgress:
    def test_no_compression(self) -> None:
        assert compute_compression_progress(0.5, 16) == 0.0

    def test_partial_compression(self) -> None:
        assert compute_compression_progress(-0.5, 16) == pytest.approx(0.125)

    def test_max_compression(self) -> None:
        import math

        assert compute_compression_progress(-math.log2(8), 8) == pytest.approx(1.0)

    def test_degenerate_graph(self) -> None:
        assert compute_compression_progress(-1.0, 1) == 0.0


class TestStructuralNovelty:
    def test_zero_change(self) -> None:
        sim = {
            "centralityDelta": {"data": []},
            "componentCountReduction": {"data": 0},
            "newLoops": {"data": []},
        }
        assert compute_structural_novelty(sim) == 0.0

    def test_bridge_loop_and_centrality(self) -> None:
        sim = {
            "centralityDelta": {"data": [{"entityId": "a", "before": 2, "after": 4}]},
            "componentCountReduction": {"data": 1},
            "newLoops": {"data": [{"name": "L"}]},
        }
        # ncd = min(1, |4-2|/4)=0.5 -> 0.4*0.5 + 0.4*1 + 0.2*1 = 0.8
        assert compute_structural_novelty(sim) == pytest.approx(0.8)

    def test_loop_only(self) -> None:
        sim = {
            "centralityDelta": {"data": []},
            "componentCountReduction": {"data": 0},
            "newLoops": {"data": [{"name": "L"}]},
        }
        assert compute_structural_novelty(sim) == pytest.approx(0.2)

    def test_null_data_defaults(self) -> None:
        sim = {
            "centralityDelta": {"data": None},
            "componentCountReduction": {"data": None},
            "newLoops": {"data": None},
        }
        assert compute_structural_novelty(sim) == 0.0


class TestInterestingness:
    def test_equal_weights_average(self) -> None:
        score = compute_interestingness(
            {"si": 0.6, "structuralNovelty": 0.3, "compressionProgress": 0.9}
        )
        assert score == pytest.approx(0.6)

    def test_excludes_si_when_no_embeddings(self) -> None:
        score = compute_interestingness(
            {
                "si": 0.5,
                "structuralNovelty": 0.8,
                "compressionProgress": 0.4,
                "embeddingsAvailable": False,
            }
        )
        assert score == pytest.approx(0.6)

    def test_custom_weights(self) -> None:
        score = compute_interestingness(
            {
                "si": 0.5,
                "structuralNovelty": 0.9,
                "compressionProgress": 0.3,
                "weights": {"siWeight": 0.2, "structuralWeight": 0.6, "compressionWeight": 0.2},
            }
        )
        # (0.2*0.5 + 0.6*0.9 + 0.2*0.3) / 1.0 = 0.1 + 0.54 + 0.06 = 0.7
        assert score == pytest.approx(0.7)

    def test_negative_weight_raises(self) -> None:
        with pytest.raises(ValueError, match="Interestingness weights must be non-negative"):
            compute_interestingness(
                {
                    "si": 0.5,
                    "structuralNovelty": 0.5,
                    "compressionProgress": 0.5,
                    "weights": {"siWeight": -0.1},
                }
            )

    def test_all_zero_weights(self) -> None:
        score = compute_interestingness(
            {
                "si": 0.5,
                "structuralNovelty": 0.5,
                "compressionProgress": 0.5,
                "weights": {"siWeight": 0.0, "structuralWeight": 0.0, "compressionWeight": 0.0},
            }
        )
        assert score == 0.0


# =============================================================================
# Fingerprint
# =============================================================================


def _sha16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class TestFingerprint:
    def test_depth0_hash_is_entity_type(self) -> None:
        graph = hydrate_graph([_entity("a", "A", "concept")], [])
        assert compute_fingerprint(graph, "a", depth=0) == _sha16("concept")

    def test_depth1_hash_canonical(self) -> None:
        graph = hydrate_graph([_entity("a", "A", "concept")], [])
        expected = _sha16("concept|in:|out:|neighbors:")
        assert compute_fingerprint(graph, "a", depth=1) == expected

    def test_neighborhood_meta(self) -> None:
        entities = [_entity("s", "S", "system"), _entity("p", "P", "concept")]
        relations = [_relation("s", "p", "part_of")]
        graph = hydrate_graph(entities, relations)
        meta = neighborhood_meta(graph, "s")
        assert meta == {
            "incomingRelationTypes": [],
            "outgoingRelationTypes": ["part_of"],
            "neighborEntityTypes": ["concept"],
        }

    def test_describe_fingerprint(self) -> None:
        info = {
            "entityType": "system",
            "incomingRelationTypes": [],
            "outgoingRelationTypes": ["part_of"],
            "neighborEntityTypes": ["concept"],
        }
        assert describe_fingerprint(info) == "system with outgoing [part_of] connected to [concept]"

    def test_describe_isolated(self) -> None:
        info = {
            "entityType": "concept",
            "incomingRelationTypes": [],
            "outgoingRelationTypes": [],
            "neighborEntityTypes": [],
        }
        assert describe_fingerprint(info) == "isolated concept"

    def test_group_isolated_entities(self) -> None:
        entities = [_entity("a", "A", "concept"), _entity("b", "B", "concept")]
        groups = group_by_fingerprint(entities, [], min_occurrences=2)
        assert len(groups) == 1
        group = groups[0]
        assert group["count"] == 2
        assert sorted(group["entityIds"]) == ["a", "b"]
        assert group["description"] == "isolated concept"
        assert set(group.keys()) == {"fingerprint", "description", "info", "entityIds", "count"}

    def test_group_min_occurrences_filter(self) -> None:
        entities = [_entity("a", "A", "concept"), _entity("b", "B", "claim")]
        # Two distinct isolated fingerprints, each count 1 -> filtered out.
        assert group_by_fingerprint(entities, [], min_occurrences=2) == []

    def test_group_sorted_and_limited(self) -> None:
        # Three isolated concepts (count 3) + two isolated claims (count 2).
        entities = [
            _entity("c1", "C1", "concept"),
            _entity("c2", "C2", "concept"),
            _entity("c3", "C3", "concept"),
            _entity("k1", "K1", "claim"),
            _entity("k2", "K2", "claim"),
        ]
        groups = group_by_fingerprint(entities, [], min_occurrences=2, max_patterns=20)
        assert [g["count"] for g in groups] == [3, 2]
        limited = group_by_fingerprint(entities, [], min_occurrences=2, max_patterns=1)
        assert len(limited) == 1
        assert limited[0]["count"] == 3

    def test_shared_structure_groups_together(self) -> None:
        entities = [
            _entity("c1", "C1", "concept"),
            _entity("c2", "C2", "concept"),
            _entity("e1", "E1", "evidence"),
            _entity("e2", "E2", "evidence"),
        ]
        relations = [_relation("c1", "e1", "supports"), _relation("c2", "e2", "supports")]
        graph = hydrate_graph(entities, relations)
        assert compute_fingerprint(graph, "c1") == compute_fingerprint(graph, "c2")
        groups = group_by_fingerprint(entities, relations, min_occurrences=2)
        counts = sorted(g["count"] for g in groups)
        assert counts == [2, 2]  # {c1,c2} and {e1,e2}


# =============================================================================
# Capability spec
# =============================================================================


class TestCapabilitySpec:
    def test_type_completeness_violation(self) -> None:
        store = FakeStore([_entity("a", "A", "concept")], [])
        spec = CapabilitySpec().require_type_completeness(["concept", "claim"])
        result = spec.validate(store)
        assert result["pass"] is False
        assert len(result["violations"]) == 1
        v = result["violations"][0]
        assert v == {
            "capabilityName": "type-completeness(concept,claim)",
            "violationType": "completeness",
            "message": "Entity type 'claim' has no instances in the graph",
            "suggestedAction": "Create at least one entity of type 'claim'",
        }

    def test_type_completeness_pass(self) -> None:
        store = FakeStore([_entity("a", "A", "concept")], [])
        result = CapabilitySpec().require_type_completeness(["concept"]).validate(store)
        assert result["pass"] is True
        assert result["violations"] == []

    def test_test_coverage_violation(self) -> None:
        # A 'system' with no linked procedure violates test-coverage.
        store = FakeStore([_entity("sys", "Sys", "system")], [])
        result = CapabilitySpec().require_test_coverage().validate(store)
        assert result["pass"] is False
        v = result["violations"][0]
        assert v["capabilityName"] == "test-coverage"
        assert v["violationType"] == "test_coverage"
        assert v["elementId"] == "sys"
        assert v["message"] == "Entity 'Sys' (type: system) has no linked test procedure"
        assert "procedure" in v["suggestedAction"]

    def test_test_coverage_satisfied(self) -> None:
        entities = [_entity("sys", "Sys", "system"), _entity("proc", "Proc", "procedure")]
        relations = [_relation("proc", "sys", "related_to")]
        store = FakeStore(entities, relations)
        result = CapabilitySpec().require_test_coverage().validate(store)
        assert result["pass"] is True

    def test_pattern_consistency_violation(self) -> None:
        entities = [
            _entity("c1", "C1", "concept"),
            _entity("c2", "C2", "concept"),
            _entity("c3", "C3", "concept"),
            _entity("e1", "E1", "evidence"),
            _entity("e2", "E2", "evidence"),
        ]
        relations = [_relation("c1", "e1", "supports"), _relation("c2", "e2", "supports")]
        store = FakeStore(entities, relations)
        result = CapabilitySpec().require_pattern_consistency().validate(store)
        assert result["pass"] is False
        assert len(result["violations"]) == 1
        v = result["violations"][0]
        assert v["capabilityName"] == "pattern-consistency(min=2)"
        assert v["violationType"] == "pattern"
        assert v["elementId"] == "c3"
        assert v["message"] == (
            "Entity 'C3' (type: concept) is missing outgoing 'supports' "
            "relation that 2/3 peers have"
        )

    def test_derive_from_graph_adds_capabilities(self) -> None:
        entities = [_entity("a", "A", "concept"), _entity("b", "B", "claim")]
        store = FakeStore(entities, [])
        spec = CapabilitySpec().derive_from_graph(store)
        names = [c["name"] for c in spec.get_capabilities()]
        assert names == ["type-completeness(concept,claim)", "pattern-consistency(min=2)"]
        # Present types all have instances; too few peers for a pattern -> pass.
        result = spec.validate(store)
        assert result["pass"] is True

    def test_validate_result_shape(self) -> None:
        store = FakeStore([_entity("a", "A", "concept")], [])
        result = CapabilitySpec().require_type_completeness(["concept", "claim"]).validate(store)
        assert set(result.keys()) == {
            "pass",
            "totalCapabilities",
            "passedCapabilities",
            "failedCapabilities",
            "violations",
            "capabilities",
        }
        assert result["totalCapabilities"] == 1
        assert result["failedCapabilities"] == 1


# =============================================================================
# Deduplication gate
# =============================================================================


def _proposal(name: str, entity_type: str, confidence: float = 0.8) -> Doc:
    return {
        "entity": {"name": name, "entityType": entity_type, "observations": ["obs"]},
        "relations": [],
        "rationale": "because",
        "confidence": confidence,
        "strategy": "pattern_completion",
    }


class TestDeduplicationGate:
    def test_proposal_to_text(self) -> None:
        text = proposal_to_text(_proposal("Widget", "concept"))
        assert text == "Widget. Type: concept. obs. because"

    def test_name_based_reject(self) -> None:
        store = FakeStore([_entity("x", "Widget", "concept")], [])
        result = deduplicate_proposals([_proposal("widget", "concept")], None, store)
        assert result["accepted"] == []
        assert len(result["rejected"]) == 1
        assert result["matches"][0]["similarity"] == 1.0
        assert result["matches"][0]["existingEntityId"] == "x"
        assert result["mode"] == "reject"
        assert result["threshold"] == 0.85

    def test_name_based_flag_keeps_proposal(self) -> None:
        store = FakeStore([_entity("x", "Widget", "concept")], [])
        result = deduplicate_proposals(
            [_proposal("Widget", "concept")], None, store, {"mode": "flag"}
        )
        assert len(result["accepted"]) == 1
        accepted = result["accepted"][0]
        assert accepted["isDuplicate"] is True
        assert accepted["duplicateOf"]["existingEntityId"] == "x"

    def test_name_based_no_match_accepts(self) -> None:
        store = FakeStore([_entity("x", "Other", "concept")], [])
        result = deduplicate_proposals([_proposal("Widget", "concept")], None, store)
        assert len(result["accepted"]) == 1
        assert result["accepted"][0]["isDuplicate"] is False
        assert result["matches"] == []

    def test_embedding_path_detects_duplicate(self) -> None:
        entities = [_entity("x", "Existing", "concept")]
        vectors = {"x": [1.0, 0.0, 0.0]}
        store = FakeStore(entities, [], vectors)
        proposal = _proposal("Fresh Name", "concept")
        manager = FakeEmbeddingManager({proposal_to_text(proposal): [1.0, 0.0, 0.0]})
        result = deduplicate_proposals([proposal], manager, store)
        assert result["accepted"] == []
        assert len(result["rejected"]) == 1
        assert result["matches"][0]["existingEntityId"] == "x"
        assert result["matches"][0]["similarity"] == pytest.approx(1.0)

    def test_embedding_path_type_filter(self) -> None:
        # Existing entity has a different type -> not a candidate -> accepted.
        entities = [_entity("x", "Existing", "claim")]
        vectors = {"x": [1.0, 0.0, 0.0]}
        store = FakeStore(entities, [], vectors)
        proposal = _proposal("Fresh", "concept")
        manager = FakeEmbeddingManager({proposal_to_text(proposal): [1.0, 0.0, 0.0]})
        result = deduplicate_proposals([proposal], manager, store)
        assert len(result["accepted"]) == 1
        assert result["accepted"][0]["isDuplicate"] is False

    def test_embedding_path_below_threshold_accepts(self) -> None:
        entities = [_entity("x", "Existing", "concept")]
        vectors = {"x": [0.0, 1.0, 0.0]}
        store = FakeStore(entities, [], vectors)
        proposal = _proposal("Fresh", "concept")
        manager = FakeEmbeddingManager({proposal_to_text(proposal): [1.0, 0.0, 0.0]})
        result = deduplicate_proposals([proposal], manager, store)
        assert len(result["accepted"]) == 1
        assert result["accepted"][0]["isDuplicate"] is False

    def test_no_vectors_falls_back_to_name(self) -> None:
        # Embedding manager present but store has no vectors -> name-based path.
        store = FakeStore([_entity("x", "Widget", "concept")], [], vectors={})
        manager = FakeEmbeddingManager({})
        result = deduplicate_proposals([_proposal("Widget", "concept")], manager, store)
        assert len(result["rejected"]) == 1
        assert result["matches"][0]["similarity"] == 1.0

    def test_merge_mode(self) -> None:
        store = FakeStore([_entity("x", "Widget", "concept")], [])
        result = deduplicate_proposals(
            [_proposal("Widget", "concept")], None, store, {"mode": "merge"}
        )
        assert len(result["accepted"]) == 1
        merged = result["accepted"][0]
        assert merged["confidence"] == pytest.approx(0.8 * 0.9)
        observations = merged["entity"]["observations"]
        assert any("Merged with existing entity 'Widget'" in o for o in observations)
        assert merged["relations"][-1] == {
            "targetId": "x",
            "relationType": "related_to",
            "direction": "outgoing",
        }

    def test_threshold_clamped(self) -> None:
        store = FakeStore([], [])
        result = deduplicate_proposals([], None, store, {"similarityThreshold": 2.0})
        assert result["threshold"] == 0.99
        result_low = deduplicate_proposals([], None, store, {"similarityThreshold": 0.1})
        assert result_low["threshold"] == 0.5


# =============================================================================
# Entity proposer
# =============================================================================


class TestEntityProposer:
    def test_empty_graph(self) -> None:
        store = FakeStore([], [])
        result = propose_entities(store)
        assert result["proposals"] == []
        assert result["strategyCounts"] == {"pattern_completion": 0, "llm_reasoning": 0}
        assert result["filteredCount"] == 0
        assert result["violations"] == []
        assert isinstance(result["durationMs"], int)

    def test_pattern_completion_loner(self) -> None:
        # c1,c2 support evidence; c3 does not -> pattern violation on c3 ->
        # a loner proposal that adds a supports->c3 edge with an inferred
        # 'evidence' target type.
        entities = [
            _entity("c1", "C1", "concept"),
            _entity("c2", "C2", "concept"),
            _entity("c3", "C3", "concept"),
            _entity("e1", "E1", "evidence"),
            _entity("e2", "E2", "evidence"),
        ]
        relations = [_relation("c1", "e1", "supports"), _relation("c2", "e2", "supports")]
        store = FakeStore(entities, relations)

        result = propose_entities(store)

        assert result["filteredCount"] == 0
        assert result["strategyCounts"] == {"pattern_completion": 1, "llm_reasoning": 0}
        assert len(result["proposals"]) == 1

        proposal = result["proposals"][0]
        assert proposal["entity"]["name"] == "evidence for C3"
        assert proposal["entity"]["entityType"] == "evidence"
        assert proposal["strategy"] == "pattern_completion"
        assert proposal["confidence"] == pytest.approx(0.8 * 0.85)
        assert proposal["capabilityViolation"] == "pattern-consistency(min=2)"
        assert proposal["relations"] == [
            {"targetId": "c3", "relationType": "supports", "direction": "incoming"}
        ]
        # Exactly one baseline violation informs the proposals.
        assert len(result["violations"]) == 1
        assert result["violations"][0]["elementId"] == "c3"

    def test_completeness_proposal(self) -> None:
        # Explicit capability spec requiring a type with no instances.
        entities = [_entity("a", "A", "concept")]
        store = FakeStore(entities, [])
        spec = CapabilitySpec().require_type_completeness(["concept", "hypothesis"])
        result = propose_entities(store, {"capabilitySpec": spec})

        assert result["strategyCounts"]["pattern_completion"] == 1
        proposal = result["proposals"][0]
        assert proposal["entity"]["name"] == "New hypothesis"
        assert proposal["entity"]["entityType"] == "hypothesis"
        assert proposal["relations"] == []
        assert proposal["confidence"] == pytest.approx(0.8 * 0.9)
        assert proposal["capabilityViolation"] == "type-completeness(concept,hypothesis)"

    def test_limit_and_sorting(self) -> None:
        entities = [_entity("a", "A", "concept")]
        store = FakeStore(entities, [])
        # Two missing types -> two completeness proposals (0.72 each); limit 1.
        spec = CapabilitySpec().require_type_completeness(["concept", "hypothesis", "tension"])
        result = propose_entities(store, {"capabilitySpec": spec, "limit": 1})
        assert len(result["proposals"]) == 1
        assert result["strategyCounts"]["pattern_completion"] == 1

    def test_llm_strategy_dead_without_client(self) -> None:
        entities = [_entity("a", "A", "concept")]
        store = FakeStore(entities, [])
        # llm_reasoning requested but no client -> no llm proposals.
        result = propose_entities(store, {"strategies": ["llm_reasoning"]})
        assert result["strategyCounts"]["llm_reasoning"] == 0
        assert result["proposals"] == []
