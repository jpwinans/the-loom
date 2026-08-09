"""TL-472 (Agent Contract, epic TL-477): run-inference's dry-run mode wrote an
``inference_trace`` entity even though it is a preview operation.

Trace creation sat above the ``if not dry_run:`` guard: a ``dryRun: true``
call correctly skipped writing derived relations, but unconditionally created
and persisted an ``inference_trace`` entity recording the (simulated) run.
Confirmed via probe: after a dry run, ``relationCount`` was unchanged at 355
while ``entityTypeDistribution`` showed ``inference_trace: 1`` — a preview
call had mutated the graph.

Fix: trace persistence moved inside the ``not dry_run`` branch alongside
derived-relation persistence, so a dry run writes nothing at all. The would-be
trace payload is still returned — as ``tracePreview``, unpersisted — since it
is useful for a caller previewing a run, but ``traceId`` stays ``null`` (there
is no entity to reference). Every response now carries the shared ``applied``
marker, and a dry run also carries a ``DRY_RUN`` notice.

These tests pin the trace *count* in the store (not just the response shape)
before and after a dry run, per the Phase 7 acceptance test.
"""

from __future__ import annotations

from typing import Any

from theloom.model import EntityFilter
from theloom.operations.entity import CreateEntityInput, create_entity
from theloom.operations.inference import (
    InferenceRuleCreateInput,
    InferenceRuleListInput,
    InferenceTraceGetInput,
    RunInferenceInput,
    inference_rule_create,
    inference_rule_list,
    inference_trace_get,
    run_inference,
)
from theloom.operations.relations import CreateRelationInput, create_relation
from theloom.store.multigraph import MultiGraph

RULE_SPEC = {
    "name": "test-causal-rule",
    "description": "A causes B whenever A relates_to B",
    "conditions": [{"from": "?a", "to": "?b", "relationType": "related_to"}],
    "conclusion": {
        "from": "?a",
        "to": "?b",
        "relationType": "causes",
        "strength": "moderate",
        "evidence": "derived-by-test-rule",
        "polarity": None,
    },
    "enabled": True,
}


def _entity(multi: MultiGraph, name: str) -> dict[str, Any]:
    result = create_entity(
        CreateEntityInput.model_validate(
            {"name": name, "entityType": "concept", "observations": [name]}
        ),
        multi,
    )
    assert isinstance(result, dict)
    return result


def _trace_count(multi: MultiGraph) -> int:
    store = multi.get_store(None)
    return len(store.list_entities(EntityFilter.model_validate({"entityType": "inference_trace"})))


def _relation_count(multi: MultiGraph) -> int:
    return len(multi.get_store(None).list_relations())


def _seed_matching_rule_and_fact(multi: MultiGraph) -> None:
    inference_rule_create(InferenceRuleCreateInput.model_validate({"rule": RULE_SPEC}), multi)
    a = _entity(multi, "Rain")
    b = _entity(multi, "Wet ground")
    create_relation(
        CreateRelationInput.model_validate(
            {
                "from": a["id"],
                "to": b["id"],
                "relationType": "related_to",
                "polarity": None,
                "strength": "moderate",
                "evidence": None,
            }
        ),
        multi,
    )


def test_dry_run_persists_no_trace_entity_and_no_relation(multi: MultiGraph) -> None:
    _seed_matching_rule_and_fact(multi)
    baseline_traces = _trace_count(multi)
    baseline_relations = _relation_count(multi)

    result = run_inference(RunInferenceInput.model_validate({"dryRun": True}), multi)

    # The response still shows what WOULD be derived...
    assert result["derivedRelations"], "expected a matching rule to fire in preview"
    assert result["applied"] is False
    assert result["traceId"] is None
    codes = {n["code"] for n in result["notices"]}
    assert "DRY_RUN" in codes

    # ...but nothing was written to the store: the exact TL-472 regression.
    assert _trace_count(multi) == baseline_traces
    assert _relation_count(multi) == baseline_relations


def test_dry_run_returns_an_unpersisted_trace_preview(multi: MultiGraph) -> None:
    _seed_matching_rule_and_fact(multi)

    result = run_inference(RunInferenceInput.model_validate({"dryRun": True}), multi)

    preview = result.get("tracePreview")
    assert preview is not None
    assert preview["dryRun"] is True
    assert preview["rulesEvaluated"] == 1
    assert preview["derivedFactCount"] == len(result["derivedRelations"])
    assert len(preview["steps"]) == len(result["derivedRelations"])


def test_real_run_persists_exactly_one_trace_and_marks_applied(multi: MultiGraph) -> None:
    _seed_matching_rule_and_fact(multi)
    baseline_traces = _trace_count(multi)
    baseline_relations = _relation_count(multi)

    result = run_inference(RunInferenceInput.model_validate({}), multi)

    assert result["applied"] is True
    assert "notices" not in result
    assert result["traceId"] is not None
    assert "tracePreview" not in result

    assert _trace_count(multi) == baseline_traces + 1
    assert _relation_count(multi) == baseline_relations + 1

    trace = inference_trace_get(
        InferenceTraceGetInput.model_validate({"traceId": result["traceId"]}), multi
    )
    assert trace["dryRun"] is False
    assert trace["derivedFactCount"] == len(result["derivedRelations"])


def test_create_without_enabled_carries_rule_disabled_notice(multi: MultiGraph) -> None:
    spec = dict(RULE_SPEC)
    del spec["enabled"]
    result = inference_rule_create(InferenceRuleCreateInput.model_validate({"rule": spec}), multi)

    assert "enabled" not in result
    codes = {n["code"] for n in result.get("notices", [])}
    assert "RULE_DISABLED" in codes
    notice = next(n for n in result["notices"] if n["code"] == "RULE_DISABLED")
    assert notice["hint"] is not None
    assert "enabled" in notice["hint"].lower()

    # The rule is still stored -- informational only, nothing is rejected.
    stored = inference_rule_list(InferenceRuleListInput.model_validate({}), multi)
    assert any(r["id"] == result["id"] for r in stored["items"])


def test_create_with_enabled_true_has_no_disabled_notice(multi: MultiGraph) -> None:
    result = inference_rule_create(
        InferenceRuleCreateInput.model_validate({"rule": RULE_SPEC}), multi
    )
    codes = {n["code"] for n in result.get("notices", [])}
    assert "RULE_DISABLED" not in codes


def test_dry_run_documented_in_schema() -> None:
    """The evaluator learns behavior only from --schema/--help/COMMANDS.md."""
    field = RunInferenceInput.model_fields["dry_run"]
    assert field.description is not None
    assert "false" in field.description.lower()
    assert "persist" in field.description.lower()
    assert "tracepreview" in field.description.lower()
