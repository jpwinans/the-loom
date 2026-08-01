"""Unit tests for the CEGIS synthesis foundation (theloom/synthesis).

Non-golden by design: the loop is seeded (mulberry32) but non-deterministic
across differing seeds, so these tests assert (a) mulberry32 is bit-exact and
deterministic, (b) generator determinism given a fixed seed, and (c) the
structural invariants of the CEGIS result — never byte-for-byte output, and
never touching FalkorDB (verification is in-memory).
"""

from __future__ import annotations

from typing import Any

import pytest

from theloom.synthesis.cegis import (
    CegisSynthesizeInput,
    quick_verify,
    run_cegis,
)
from theloom.synthesis.generator import (
    _CAUSAL_VALUES,
    _ENTITY_TYPE_VALUES,
    GeneratedEntity,
    GeneratedRelation,
    GenerationResult,
    GenerationSpec,
    TypeCompatibilityGraph,
    TypeConstrainedGenerator,
    mulberry32,
)

# Expected mulberry32 output streams, keyed by seed: the first N values
# produced by mulberry32(SEED).
_REFERENCE_MULBERRY32 = {
    42: [
        0.6011037519201636,
        0.44829055899754167,
        0.8524657934904099,
        0.6697340414393693,
        0.17481389874592423,
        0.5265925421845168,
    ],
    0: [
        0.26642920868471265,
        0.0003297457005828619,
        0.2232720274478197,
        0.1462021479383111,
        0.46732782293111086,
        0.5450490827206522,
    ],
    1: [
        0.6270739405881613,
        0.002735721180215478,
        0.5274470399599522,
        0.9810509674716741,
        0.9683778982143849,
        0.281103502959013,
    ],
    12345: [
        0.9797282677609473,
        0.3067522644996643,
        0.484205421525985,
        0.817934412509203,
        0.5094283693470061,
        0.34747186047025025,
    ],
}

_REASONS = {"success", "unrealizable", "maxIterations", "timeout"}


def _default_graph() -> TypeCompatibilityGraph:
    return TypeCompatibilityGraph.create_default()


# =============================================================================
# (a) mulberry32 bit-exact determinism
# =============================================================================


class TestMulberry32:
    @pytest.mark.parametrize("seed", sorted(_REFERENCE_MULBERRY32))
    def test_matches_reference_stream(self, seed: int) -> None:
        rng = mulberry32(seed)
        got = [rng() for _ in range(len(_REFERENCE_MULBERRY32[seed]))]
        assert got == _REFERENCE_MULBERRY32[seed]

    def test_outputs_in_unit_interval(self) -> None:
        rng = mulberry32(999)
        for _ in range(1000):
            value = rng()
            assert 0.0 <= value < 1.0

    def test_reseed_is_reproducible(self) -> None:
        assert [mulberry32(7)() for _ in range(3)] == [mulberry32(7)() for _ in range(3)]


# =============================================================================
# Type compatibility graph — insertion order == valid-relations order
# =============================================================================


class TestTypeCompatibilityGraph:
    def test_default_ordering_concept_concept(self) -> None:
        graph = _default_graph()
        assert graph.get_valid_relations("concept", "concept") == [
            "related_to",
            "contradicts",
            "supersedes",
            "causes",
            "enables",
            "requires",
            "inhibits",
            "amplifies",
            "dampens",
        ]

    def test_default_ordering_evidence_claim(self) -> None:
        graph = _default_graph()
        assert graph.get_valid_relations("evidence", "claim") == [
            "related_to",
            "supports",
            "contradicts",
        ]

    def test_supports_is_directional(self) -> None:
        graph = _default_graph()
        assert graph.is_valid("evidence", "supports", "claim")
        assert not graph.is_valid("claim", "supports", "evidence")


# =============================================================================
# (b) generator determinism + type validity
# =============================================================================


def _assert_relations_valid(result: GenerationResult, graph: TypeCompatibilityGraph) -> None:
    seen: set[tuple[int, int, str]] = set()
    for rel in result.relations:
        source = result.entities[rel.from_index].entity_type
        target = result.entities[rel.to_index].entity_type
        assert graph.is_valid(source, rel.relation_type, target)
        assert rel.from_index != rel.to_index  # no self-loop
        key = (rel.from_index, rel.to_index, rel.relation_type)
        assert key not in seen  # no duplicate
        seen.add(key)
        if rel.relation_type in _CAUSAL_VALUES:
            assert rel.polarity in ("+", "-")
        else:
            assert rel.polarity is None
        assert rel.strength in ("weak", "moderate", "strong")


class TestGenerator:
    def test_determinism_same_seed(self) -> None:
        gen = TypeConstrainedGenerator(_default_graph())
        spec = GenerationSpec(max_entities=8, max_relations=12)
        assert gen.generate(spec, 12345) == gen.generate(spec, 12345)

    def test_fills_to_max_entities(self) -> None:
        graph = _default_graph()
        gen = TypeConstrainedGenerator(graph)
        spec = GenerationSpec(max_entities=10, max_relations=15)
        result = gen.generate(spec, 42)
        assert result.success
        assert len(result.entities) == 10
        assert len(result.relations) <= 15
        _assert_relations_valid(result, graph)

    def test_all_entity_types_valid(self) -> None:
        gen = TypeConstrainedGenerator(_default_graph())
        result = gen.generate(GenerationSpec(max_entities=12, max_relations=20), 5)
        assert result.success
        for entity in result.entities:
            assert entity.entity_type in _ENTITY_TYPE_VALUES

    def test_zero_entities_ok(self) -> None:
        gen = TypeConstrainedGenerator(_default_graph())
        result = gen.generate(GenerationSpec(max_entities=0, max_relations=0), 1)
        assert result.success
        assert result.entities == []
        assert result.relations == []

    def test_zero_entities_with_required_types_fails(self) -> None:
        gen = TypeConstrainedGenerator(_default_graph())
        result = gen.generate(
            GenerationSpec(max_entities=0, max_relations=0, required_types=("concept",)), 1
        )
        assert not result.success
        assert result.failure_reason is not None

    def test_required_types_exceed_capacity_fails(self) -> None:
        gen = TypeConstrainedGenerator(_default_graph())
        result = gen.generate(
            GenerationSpec(max_entities=1, max_relations=0, required_types=("concept", "claim")),
            1,
        )
        assert not result.success
        assert "maxEntities" in (result.failure_reason or "")

    def test_required_types_placed_first(self) -> None:
        gen = TypeConstrainedGenerator(_default_graph())
        result = gen.generate(
            GenerationSpec(max_entities=5, max_relations=0, required_types=("system", "loop")),
            3,
        )
        assert result.success
        assert result.entities[0].entity_type == "system"
        assert result.entities[1].entity_type == "loop"


# =============================================================================
# quickVerify — short-circuits repeat candidates
# =============================================================================


def _candidate(entity_types: list[str], relation_types: list[str]) -> GenerationResult:
    entities = [GeneratedEntity(f"e{i}", t, [t]) for i, t in enumerate(entity_types)]
    relations = [GeneratedRelation(0, 1, rt, None, "weak") for rt in relation_types]
    return GenerationResult(True, entities, relations)


class TestQuickVerify:
    def test_empty_counterexamples_returns_none(self) -> None:
        cand = _candidate(["concept", "claim"], [])
        assert quick_verify(cand, []) is None

    def test_matching_entity_type_set_matches(self) -> None:
        cand = _candidate(["concept", "claim"], [])
        ce: dict[str, Any] = {
            "iteration": 0,
            "violations": [],
            "missingEntityTypes": ["claim", "concept"],
            "missingRelationTypes": [],
        }
        assert quick_verify(cand, [ce]) is ce

    def test_non_matching_entity_type_set_returns_none(self) -> None:
        cand = _candidate(["concept", "claim"], [])
        ce: dict[str, Any] = {
            "iteration": 0,
            "violations": [],
            "missingEntityTypes": ["source"],
            "missingRelationTypes": [],
        }
        assert quick_verify(cand, [ce]) is None

    def test_regex_fallback_on_unstructured_counterexample(self) -> None:
        cand = _candidate(["concept", "claim"], [])
        ce: dict[str, Any] = {
            "iteration": 0,
            "violations": [{"elementId": "x", "message": "Missing entity type 'source'"}],
        }
        assert quick_verify(cand, [ce]) is ce


# =============================================================================
# (c) CEGIS loop structural invariants
# =============================================================================


def _input(**over: Any) -> CegisSynthesizeInput:
    payload: dict[str, Any] = {
        "properties": [
            {"name": "has-name", "type": "forAllNodes", "field": "name", "condition": "notEmpty"}
        ],
        "maxEntities": 10,
        "maxRelations": 15,
    }
    payload.update(over)
    return CegisSynthesizeInput.model_validate(payload)


class TestCegisLoop:
    def test_reason_in_four_set(self) -> None:
        assert run_cegis(_input())["reason"] in _REASONS

    def test_satisfiable_spec_succeeds(self) -> None:
        result = run_cegis(_input())
        assert result["success"] is True
        assert result["reason"] == "success"
        assert result["counterexamples"] == []

    def test_success_candidate_is_structurally_valid(self) -> None:
        params = _input(maxEntities=10, maxRelations=15)
        result = run_cegis(params)
        assert result["reason"] == "success"
        candidate = result["candidate"]
        entities = candidate["entities"]
        relations = candidate["relations"]

        assert len(entities) == params.max_entities
        assert len(relations) <= params.max_relations

        graph = _default_graph()
        seen: set[tuple[int, int, str]] = set()
        for rel in relations:
            source = entities[rel["fromIndex"]]["entityType"]
            target = entities[rel["toIndex"]]["entityType"]
            assert graph.is_valid(source, rel["relationType"], target)
            assert rel["fromIndex"] != rel["toIndex"]
            key = (rel["fromIndex"], rel["toIndex"], rel["relationType"])
            assert key not in seen
            seen.add(key)
            if rel["relationType"] in _CAUSAL_VALUES:
                assert rel["polarity"] in ("+", "-")
            else:
                assert rel["polarity"] is None
            assert rel["strength"] in ("weak", "moderate", "strong")

    def test_impossible_spec_hits_max_iterations(self) -> None:
        params = _input(
            properties=[
                {
                    "name": "impossible",
                    "type": "forAllNodes",
                    "field": "name",
                    "condition": "equals",
                    "value": "___never___",
                }
            ],
            maxIterations=10,
        )
        result = run_cegis(params)
        assert result["success"] is False
        assert result["reason"] == "maxIterations"
        assert result["iterations"] == 10
        assert len(result["counterexamples"]) == 10
        # Contiguous CE indices 0..maxIterations-1.
        assert [c["iteration"] for c in result["counterexamples"]] == list(range(10))
        assert "candidate" not in result

    def test_custom_max_iterations_is_respected(self) -> None:
        params = _input(
            properties=[
                {
                    "name": "impossible",
                    "type": "forAllNodes",
                    "field": "name",
                    "condition": "equals",
                    "value": "___never___",
                }
            ],
            maxIterations=4,
        )
        result = run_cegis(params)
        assert result["reason"] == "maxIterations"
        assert result["iterations"] == 4
        assert [c["iteration"] for c in result["counterexamples"]] == [0, 1, 2, 3]

    def test_quick_verify_short_circuits_repeat_candidates(self) -> None:
        # maxEntities=1 forces single-type candidates -> type-set collisions ->
        # quick-verify matches (recorded as "Quick-verify match" counterexamples).
        params = _input(
            properties=[
                {
                    "name": "impossible",
                    "type": "forAllNodes",
                    "field": "name",
                    "condition": "equals",
                    "value": "___never___",
                }
            ],
            maxEntities=1,
            maxRelations=0,
        )
        result = run_cegis(params)
        assert result["reason"] == "maxIterations"
        quick = [
            c
            for c in result["counterexamples"]
            if c["description"].startswith("Quick-verify match")
        ]
        assert quick  # at least one short-circuit occurred

    def test_loop_is_deterministic(self) -> None:
        params = _input()
        first = run_cegis(params)
        second = run_cegis(params)
        first.pop("durationMs")
        second.pop("durationMs")
        assert first == second

    def test_duration_present_and_numeric(self) -> None:
        result = run_cegis(_input())
        assert isinstance(result["durationMs"], float)
        assert result["durationMs"] >= 0.0


# =============================================================================
# Input model validation
# =============================================================================


class TestCegisInput:
    def test_defaults(self) -> None:
        params = _input()
        assert params.max_iterations == 10
        assert params.timeout_ms == 30000
        assert params.commit is False
        assert params.graph is None

    def test_max_entities_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            _input(maxEntities=0)

    def test_max_relations_may_be_zero(self) -> None:
        assert _input(maxRelations=0).max_relations == 0

    def test_max_relations_upper_bound(self) -> None:
        with pytest.raises(ValueError):
            _input(maxRelations=50001)
