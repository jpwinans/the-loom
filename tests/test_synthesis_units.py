"""Unit tests for synthesis internals (behavior pinned by the golden cases;
these cover the pieces goldens can't isolate)."""

from __future__ import annotations

import pytest

from theloom.synthesis.decomposer import (
    decompose_query,
    estimate_complexity,
    has_dependency_cycle,
    needs_decomposition,
)
from theloom.synthesis.fidelity import (
    classify_fidelity,
    compute_composite_index,
    is_entity_mentioned,
)
from theloom.synthesis.linearizer import topological_sort
from theloom.synthesis.llm import create_synthesis_client
from theloom.synthesis.orderer import compute_core_numbers
from theloom.synthesis.prompts import sanitize_for_prompt, strip_code_fences
from theloom.synthesis.realizer import chunk_text, parse_proposal_output
from theloom.synthesis.selector import find_anchors
from theloom.synthesis.traverser import _to_fixed_3


class _ListStore:
    def __init__(self, entities, relations=()):  # type: ignore[no-untyped-def]
        self._entities = list(entities)
        self._relations = list(relations)

    def list_entities(self):  # type: ignore[no-untyped-def]
        return self._entities

    def list_relations(self):  # type: ignore[no-untyped-def]
        return self._relations

    def read_entity(self, entity_id):  # type: ignore[no-untyped-def]
        return next((e for e in self._entities if e["id"] == entity_id), None)


def _rel(rid: str, from_: str, to: str, rtype: str = "causes") -> dict[str, object]:
    return {"id": rid, "from": from_, "to": to, "relationType": rtype}


class TestCoreNumbers:
    def test_triangle_plus_pendant(self) -> None:
        entities = [{"id": x} for x in "abcd"]
        relations = [
            _rel("1", "a", "b"),
            _rel("2", "b", "c"),
            _rel("3", "c", "a"),
            _rel("4", "c", "d"),
        ]
        cores = compute_core_numbers(entities, relations)
        assert cores == {"d": 1, "a": 2, "b": 2, "c": 2}

    def test_empty(self) -> None:
        assert compute_core_numbers([], []) == {}

    def test_multi_edges_collapse(self) -> None:
        entities = [{"id": "a"}, {"id": "b"}]
        relations = [_rel("1", "a", "b"), _rel("2", "a", "b"), _rel("3", "b", "a")]
        assert compute_core_numbers(entities, relations) == {"a": 1, "b": 1}


class TestTopologicalSort:
    def test_causal_order_with_core_tiebreak(self) -> None:
        entities = [{"id": x} for x in "abc"]
        relations = [_rel("1", "a", "b")]
        # c is isolated; zero in-degree = [a, c]; higher core first — equal
        # cores keep insertion order.
        assert topological_sort(entities, relations, {}) == ["a", "c", "b"]
        assert topological_sort(entities, relations, {"c": 5}) == ["c", "a", "b"]

    def test_cycle_appends_by_core_desc(self) -> None:
        entities = [{"id": x} for x in "ab"]
        relations = [_rel("1", "a", "b"), _rel("2", "b", "a")]
        assert topological_sort(entities, relations, {"b": 2, "a": 1}) == ["b", "a"]

    def test_non_causal_relations_ignored(self) -> None:
        entities = [{"id": "a"}, {"id": "b"}]
        relations = [_rel("1", "b", "a", "supports")]
        assert topological_sort(entities, relations, {}) == ["a", "b"]


class TestAnchors:
    def test_name_hits_weigh_double(self) -> None:
        store = _ListStore(
            [
                {"id": "1", "name": "feedback loops", "observations": []},
                {"id": "2", "name": "other", "observations": ["feedback", "loops here"]},
            ]
        )
        assert find_anchors("feedback loops", store) == ["1", "2"]

    def test_short_terms_fall_back_to_all_terms(self) -> None:
        store = _ListStore([{"id": "1", "name": "ab", "observations": []}])
        assert find_anchors("ab", store) == ["1"]

    def test_hybrid_results_validated_against_store(self) -> None:
        store = _ListStore([{"id": "1", "name": "x", "observations": []}])
        anchors = find_anchors("q", store, lambda q, k: [{"entityId": "stale", "score": 1.0}])
        # all hybrid hits invalid -> keyword fallback (no match -> empty)
        assert anchors == []


class TestDecomposer:
    def test_complexity_thresholds(self) -> None:
        assert estimate_complexity(20, 1) == "simple"
        assert estimate_complexity(21, 1) == "moderate"
        assert estimate_complexity(50, 3) == "moderate"
        assert estimate_complexity(51, 1) == "complex"
        assert estimate_complexity(10, 4) == "complex"

    def test_needs_decomposition(self) -> None:
        assert not needs_decomposition(20, 1)
        assert needs_decomposition(21, 1)
        assert needs_decomposition(5, 2)

    def test_no_llm_passthrough(self) -> None:
        result = decompose_query(
            {"query": "big?", "entityCount": 100, "clusterCount": 5, "entityNames": []}, None
        )
        assert result["wasDecomposed"] is False
        assert result["estimatedComplexity"] == "complex"
        assert [sq["text"] for sq in result["subQuestions"]] == ["big?"]

    def test_cycle_detection(self) -> None:
        sqs = [
            {"id": "a", "text": "", "dependsOn": ["b"], "assignedRegionIds": []},
            {"id": "b", "text": "", "dependsOn": ["a"], "assignedRegionIds": []},
        ]
        assert has_dependency_cycle(sqs)
        sqs[1]["dependsOn"] = []
        assert not has_dependency_cycle(sqs)


class TestFidelityMath:
    def test_composite_is_weighted_harmonic_mean(self) -> None:
        assert compute_composite_index(1.0, 1.0) == pytest.approx(1.0)
        assert compute_composite_index(0.5, 1.0) == pytest.approx(1 / (0.6 / 0.5 + 0.4))
        assert compute_composite_index(0.0, 1.0) == 0
        assert compute_composite_index(1.0, 1e-11) == 0

    def test_classify(self) -> None:
        assert classify_fidelity(0.8) == "high"
        assert classify_fidelity(0.79) == "moderate"
        assert classify_fidelity(0.5) == "moderate"
        assert classify_fidelity(0.49) == "low"

    def test_entity_mention_partial_word_boundary(self) -> None:
        assert is_entity_mentioned("the thermostat runs", "thermostat study")
        assert not is_entity_mentioned("thermostats run", "thermostat study")
        assert not is_entity_mentioned("a cat ran", "cat nap")  # words < 4 chars skipped


class TestPromptUtils:
    def test_sanitize_strips_controls_and_angles(self) -> None:
        assert sanitize_for_prompt("a\x00b<c>d\x1fe") == "a bcd e"

    def test_sanitize_hard_cut(self) -> None:
        assert sanitize_for_prompt("x" * 250) == "x" * 200
        assert sanitize_for_prompt("x" * 250, 10) == "x" * 10

    def test_sanitize_non_strings(self) -> None:
        assert sanitize_for_prompt(None) == ""
        assert sanitize_for_prompt({"content": "y"}) == "[object Object]"
        assert sanitize_for_prompt(12) == "12"

    def test_strip_code_fences(self) -> None:
        assert strip_code_fences("```json\n[1]\n```") == "[1]"
        assert strip_code_fences("  [1]  ") == "[1]"


class TestChunkText:
    def test_short_text_single_chunk(self) -> None:
        assert chunk_text("hello") == ["hello"]

    def test_sentence_boundary(self) -> None:
        text = ("a" * 300 + ". ") + ("b" * 300)
        chunks = chunk_text(text)
        assert chunks[0] == "a" * 300 + "."
        assert chunks[1] == "b" * 300

    def test_hard_cut_without_boundaries(self) -> None:
        text = "x" * 1200
        chunks = chunk_text(text)
        assert [len(c) for c in chunks] == [500, 500, 200]


class TestProposalParsing:
    def test_template_text_falls_back(self) -> None:
        out = parse_proposal_output("PROPOSAL: [modify] stuff\n  Expected impact: things\n")
        assert out["proposals"][0]["action"] == "modify"
        assert out["proposals"][0]["expectedImpact"] == "See rationale for details"

    def test_valid_json_extracted(self) -> None:
        text = (
            'Here you go:\n```json\n{"proposals": [{"action": "add_entity", '
            '"rationale": "r", "expectedImpact": "e"}]}\n```'
        )
        out = parse_proposal_output(text)
        assert out["proposals"] == [
            {"action": "add_entity", "rationale": "r", "expectedImpact": "e"}
        ]

    def test_invalid_actions_dropped(self) -> None:
        text = '{"proposals": [{"action": "explode", "rationale": "r", "expectedImpact": "e"}]}'
        out = parse_proposal_output(text)
        assert out["proposals"][0]["action"] == "modify"  # fallback


class TestToFixed3:
    def test_matches_js_tofixed(self) -> None:
        assert _to_fixed_3(1.0) == "1.000"
        assert _to_fixed_3(0.9) == "0.900"
        assert _to_fixed_3(0.63) == "0.630"
        assert _to_fixed_3(0.5 * 0.7) == "0.350"
        assert _to_fixed_3(0.0005) == "0.001"  # exact-tie rounds up like JS


class TestLlmClient:
    def _isolate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LOOM_CONFIG", "/nonexistent/loom-config.json")
        for var in (
            "ANTHROPIC_API_KEY",
            "LOOM_LLM_PROVIDER",
            "LOOM_LLM_BASE_URL",
            "LOOM_LLM_MODEL",
            "LOOM_LLM_API_KEY",
        ):
            monkeypatch.delenv(var, raising=False)

    def test_no_key_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._isolate(monkeypatch)
        assert create_synthesis_client() is None

    def test_key_builds_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._isolate(monkeypatch)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
        client = create_synthesis_client()
        assert client is not None
        assert client.get_model() == "claude-haiku-4-5-20251001"
