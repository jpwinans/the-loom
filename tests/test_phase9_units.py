"""Unit tests for symbolic internals — symbolic timeout/errors, AC-3, cycle
detection, and inference matching that the golden tests can't fully isolate."""

from __future__ import annotations

from theloom.algebra import routing
from theloom.symbolic import core
from theloom.verification import checks, propagation


class TestSymbolicCore:
    def test_solve_list_formatting(self) -> None:
        result = core.run("solve", {"equation": "x**2 - 4", "variables": ["x"]})
        assert result["result"] == "['-2', '2']"
        assert result["latex_result"] == "-2, 2"
        assert result["solution_count"] == 2

    def test_parse_error_envelope(self) -> None:
        result = core.run("solve", {"equation": "@@@bad", "variables": ["x"]})
        assert result["success"] is False
        assert "error" in result

    def test_unknown_operation(self) -> None:
        result = core.run("bogus", {})
        assert result["success"] is False
        assert result["error"].startswith("Unknown operation: bogus")

    def test_factor_and_expand(self) -> None:
        assert core.run("factor", {"expression": "x**2 - 1"})["result"] == "(x - 1)*(x + 1)"
        assert core.run("expand", {"expression": "(x + 1)**2"})["result"] == "x**2 + 2*x + 1"

    def test_verify_true_false(self) -> None:
        t = core.run("verify", {"equation": "x**2 - 4 = 0", "variable": "x", "value": "2"})
        assert t["is_valid"] is True
        f = core.run("verify", {"equation": "x**3 - 8 = 0", "variable": "x", "value": "3"})
        assert f["is_valid"] is False

    def test_evaluate_numeric(self) -> None:
        result = core.run("evaluate", {"expression": "2 + 3*4"})
        assert result["numeric_value"] == 14.0
        assert result["result"] == "14.0"


class TestCycleDetection:
    def test_simple_cycle(self) -> None:
        adjacency = {"a": ["b"], "b": ["c"], "c": ["a"]}
        cycle = checks.find_cycle_nodes(adjacency, ["a", "b", "c"])
        assert cycle == {"a", "b", "c"}

    def test_no_cycle(self) -> None:
        adjacency = {"a": ["b"], "b": ["c"]}
        assert checks.find_cycle_nodes(adjacency, ["a", "b", "c"]) == set()

    def test_path_to_cycle_excluded(self) -> None:
        # x -> a -> b -> a : a,b are in the cycle, x is not.
        adjacency = {"x": ["a"], "a": ["b"], "b": ["a"]}
        cycle = checks.find_cycle_nodes(adjacency, ["x", "a", "b"])
        assert cycle == {"a", "b"}
        assert "x" not in cycle


class TestInvariants:
    def test_claims_need_evidence(self) -> None:
        entities = [
            {"id": "c1", "name": "Claim", "entityType": "claim", "status": "active"},
            {"id": "e1", "name": "Ev", "entityType": "evidence", "status": "active"},
        ]
        relations = [{"from": "e1", "to": "c1", "relationType": "supports"}]
        assert checks.inv_claims_need_evidence(entities, relations, None)["pass"] is True
        assert checks.inv_claims_need_evidence(entities, [], None)["pass"] is False

    def test_part_of_acyclic(self) -> None:
        entities = [{"id": x, "name": x} for x in "abc"]
        acyclic = [
            {"from": "a", "to": "b", "relationType": "part_of"},
            {"from": "b", "to": "c", "relationType": "part_of"},
        ]
        assert checks.inv_part_of_acyclic(entities, acyclic, None)["pass"] is True
        cyclic = [*acyclic, {"from": "c", "to": "a", "relationType": "part_of"}]
        assert checks.inv_part_of_acyclic(entities, cyclic, None)["pass"] is False

    def test_causal_cycle_through_loop_allowed(self) -> None:
        entities = [
            {"id": "v1", "name": "V1", "entityType": "variable"},
            {"id": "v2", "name": "V2", "entityType": "variable"},
            {"id": "L", "name": "Loop", "entityType": "loop"},
        ]
        # v1 -> v2 -> L (loop target excluded from adjacency) : no unintentional cycle
        relations = [
            {"from": "v1", "to": "v2", "relationType": "causes"},
            {"from": "v2", "to": "L", "relationType": "causes"},
            {"from": "L", "to": "v1", "relationType": "causes"},
        ]
        result = checks.inv_no_causal_cycles(entities, relations, None)
        assert result["pass"] is True


class TestAC3:
    def test_single_constraint_prunes_to_singletons(self) -> None:
        variables, constraints = propagation.build_csp(
            [{"sourceType": "evidence", "relationType": "supports", "targetType": "claim"}]
        )
        result = propagation.serialize(propagation.propagate(variables, constraints))
        assert result["consistent"] is True
        # source domain pruned to everything except 'evidence'; target except 'claim'
        source_var = "source:supports:evidence-claim"
        target_var = "target:supports:evidence-claim"
        assert "evidence" not in result["prunedDomains"][source_var]
        assert "claim" not in result["prunedDomains"][target_var]
        assert result["revisionsCount"] >= 2

    def test_pruned_order_matches_entity_type_order(self) -> None:
        variables, constraints = propagation.build_csp(
            [{"sourceType": "claim", "relationType": "supports", "targetType": "concept"}]
        )
        result = propagation.propagate(variables, constraints)
        pruned = result["prunedDomains"]["source:supports:claim-concept"]
        # Pruned in ALL_ENTITY_TYPES order, minus the kept 'claim'.
        from theloom.model import ALL_ENTITY_TYPES

        expected = [t.value for t in ALL_ENTITY_TYPES if t.value != "claim"]
        assert list(pruned) == expected


class TestGuards:
    def test_causal_polarity_missing(self) -> None:
        v = checks.guard_causal_polarity({"relationType": "causes", "from": "a", "to": "b"})
        assert v[0]["code"] == "CAUSAL_MISSING_POLARITY"

    def test_causal_polarity_ok(self) -> None:
        assert checks.guard_causal_polarity({"relationType": "causes", "polarity": "+"}) == []

    def test_code_relations_are_structural(self) -> None:
        """calls/references are non-causal: the polarity guard never fires and
        the algebra router treats them as structural."""
        for relation_type in ("calls", "references"):
            relation = {"relationType": relation_type, "from": "a", "to": "b"}
            assert checks.guard_causal_polarity(relation) == []
            assert routing.relation_category(relation_type) == "structural"

    def test_non_causal_polarity(self) -> None:
        v = checks.guard_non_causal_polarity(
            {"relationType": "calls", "from": "a", "to": "b", "polarity": "+"}
        )
        assert v[0]["code"] == "NON_CAUSAL_POLARITY"
        assert v[0]["severity"] == "error"
        assert checks.guard_non_causal_polarity({"relationType": "calls", "polarity": None}) == []
        assert checks.guard_non_causal_polarity({"relationType": "causes", "polarity": "+"}) == []

    def test_non_causal_polarity_is_a_registered_relation_guard(self) -> None:
        assert "nonCausalPolarity" in checks.RELATION_GUARDS

    def test_self_loop(self) -> None:
        assert checks.guard_no_self_loop({"from": "a", "to": "a"})[0]["code"] == "SELF_LOOP"

    def test_confidence_bounds(self) -> None:
        assert checks.guard_confidence_bounds({"confidence": {"score": 1.5}})[0]["code"] == (
            "CONFIDENCE_OUT_OF_BOUNDS"
        )
        assert checks.guard_confidence_bounds({"confidence": {"score": 0.5}}) == []
