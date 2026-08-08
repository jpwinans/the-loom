"""Inference engine. Pure, deterministic forward chaining — no LLM.

Rules and traces are graph entities: a rule is an `inference_rule` entity
named ``[Rule] <name>`` with observations ``[__rule_json:<json>, "Rule: <desc>"]``
(name excluded from the JSON, recovered from the prefix); a trace is an
`inference_trace` entity named ``[Trace] inference-run-<ISO>``. run-inference
is a single snapshot pass with self-relation skip and two-level dedup;
derived-relation polarity comes only from CAUSAL_POLARITY_DEFAULTS (the
conclusion's own polarity is stored but never applied). explain-inference is
deterministic template rendering.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import Field

from theloom.errors import NotFoundError, OperationError
from theloom.model import (
    CAUSAL_POLARITY_DEFAULTS,
    EntityCreate,
    EntityFilter,
    RelationType,
)
from theloom.operations.common import CommandInput
from theloom.operations.notices import notice, with_notices
from theloom.store.falkor import FalkorGraphStore
from theloom.store.multigraph import MultiGraph
from theloom.timeutil import iso_now

Doc = dict[str, Any]

RULE_DATA_PREFIX = "__rule_json:"
TRACE_DATA_PREFIX = "__trace_json:"


# =============================================================================
# Input models
# =============================================================================


class RuleCondition(CommandInput):
    from_: str = Field(alias="from")
    to: str
    relation_type: RelationType = Field(alias="relationType")


class RuleConclusion(CommandInput):
    from_: str = Field(alias="from")
    to: str
    relation_type: RelationType = Field(alias="relationType")
    strength: str
    evidence: str
    polarity: str | None = None


class RuleSpec(CommandInput):
    name: str = Field(min_length=1)
    description: str
    conditions: list[RuleCondition] = Field(min_length=1)
    conclusion: RuleConclusion
    enabled: bool | None = None


class InferenceRuleCreateInput(CommandInput):
    rule: RuleSpec
    graph: str | None = None


class InferenceRuleListInput(CommandInput):
    graph: str | None = None


class InferenceRuleDeleteInput(CommandInput):
    rule_id: str = Field(alias="ruleId")
    graph: str | None = None


_RUN_INFERENCE_DRY_RUN_DESC = (
    "Preview a run without persisting anything. Defaults to false: a call "
    "with no dryRun (or dryRun: false) matches rules AND PERSISTS the "
    "derived relations plus an inference_trace entity recording the run. "
    "Pass dryRun: true to preview the derived relations without writing "
    "an inference_trace entity or any derived relations — the would-be "
    "trace payload is still returned, unpersisted, as `tracePreview` "
    "(traceId stays null since nothing was written). Either way the "
    "response carries an `applied` marker (true only on a real, persisted "
    "run) and, on a simulated run, a DRY_RUN notice."
)


class RunInferenceInput(CommandInput):
    dry_run: bool | None = Field(
        default=None, alias="dryRun", description=_RUN_INFERENCE_DRY_RUN_DESC
    )
    rule_id: str | None = Field(default=None, alias="ruleId")
    graph: str | None = None


class InferenceTraceListInput(CommandInput):
    rule_id: str | None = Field(default=None, alias="ruleId")
    limit: int | None = None
    graph: str | None = None


class InferenceTraceGetInput(CommandInput):
    trace_id: str = Field(alias="traceId")
    graph: str | None = None


class InferenceTraceForFactInput(CommandInput):
    relation_id: str = Field(alias="relationId")
    graph: str | None = None


class ExplainInferenceInput(CommandInput):
    relation_id: str = Field(alias="relationId")
    graph: str | None = None


# =============================================================================
# Encode / decode
# =============================================================================


def _rule_to_dict(rule: RuleSpec) -> Doc:
    return {
        "conditions": [c.model_dump(by_alias=True) for c in rule.conditions],
        "conclusion": rule.conclusion.model_dump(by_alias=True, exclude_unset=True),
        "enabled": rule.enabled,
        "description": rule.description,
    }


def _entity_to_rule(entity: Doc) -> Doc | None:
    data_obs = next(
        (
            o
            for o in entity.get("observations", [])
            if isinstance(o, str) and o.startswith(RULE_DATA_PREFIX)
        ),
        None,
    )
    if data_obs is None:
        return None
    try:
        data = json.loads(data_obs[len(RULE_DATA_PREFIX) :])
    except json.JSONDecodeError:
        return None
    name = entity["name"]
    if name.startswith("[Rule] "):
        name = name[len("[Rule] ") :]
    return {
        "id": entity["id"],
        "name": name,
        "description": data.get("description", ""),
        "conditions": data.get("conditions", []),
        "conclusion": data.get("conclusion", {}),
        "enabled": data.get("enabled"),
    }


def _entity_to_trace(entity: Doc) -> Doc | None:
    data_obs = next(
        (
            o
            for o in entity.get("observations", [])
            if isinstance(o, str) and o.startswith(TRACE_DATA_PREFIX)
        ),
        None,
    )
    if data_obs is None:
        return None
    try:
        data = json.loads(data_obs[len(TRACE_DATA_PREFIX) :])
    except json.JSONDecodeError:
        return None
    return {"traceId": entity["id"], **data}


# =============================================================================
# Handlers
# =============================================================================


def _store(multi: MultiGraph, graph: str | None) -> FalkorGraphStore:
    return multi.get_store(graph)


def _rules(store: FalkorGraphStore) -> list[Doc]:
    docs = [
        e.model_dump(by_alias=True, exclude_unset=True)
        for e in store.list_entities(EntityFilter.model_validate({"entityType": "inference_rule"}))
    ]
    return [r for r in (_entity_to_rule(d) for d in docs) if r is not None]


def inference_rule_create(params: InferenceRuleCreateInput, multi: MultiGraph) -> Doc:
    store = _store(multi, params.graph)
    rule = params.rule

    condition_vars = {v for c in rule.conditions for v in (c.from_, c.to) if v.startswith("?")}
    for var in (rule.conclusion.from_, rule.conclusion.to):
        if var.startswith("?") and var not in condition_vars:
            raise OperationError(
                f"Conclusion references unbound variable '{var}'. "
                f"Available variables: {', '.join(condition_vars)}"
            )

    encoded = RULE_DATA_PREFIX + json.dumps(_rule_to_dict(rule))
    entity = store.create_entity(
        EntityCreate.model_validate(
            {
                "name": f"[Rule] {rule.name}",
                "entityType": "inference_rule",
                "observations": [encoded, f"Rule: {rule.description}"],
            }
        )
    )
    result: Doc = {
        "id": entity.id,
        "name": rule.name,
        "description": rule.description,
        "conditions": [c.model_dump(by_alias=True) for c in rule.conditions],
        "conclusion": rule.conclusion.model_dump(by_alias=True, exclude_unset=True),
    }
    if rule.enabled is not None:
        result["enabled"] = rule.enabled
    return result


def inference_rule_list(params: InferenceRuleListInput, multi: MultiGraph) -> list[Doc]:
    return _rules(_store(multi, params.graph))


def inference_rule_delete(params: InferenceRuleDeleteInput, multi: MultiGraph) -> Doc:
    store = _store(multi, params.graph)
    entity = store.read_entity(params.rule_id)
    if entity is None:
        # Custom handler wraps: "Error deleting inference rule: ..." -> NOT_FOUND
        raise NotFoundError(
            f"Error deleting inference rule: Inference rule not found: {params.rule_id}"
        )
    if entity.entity_type.value != "inference_rule":
        raise OperationError(
            f"Error deleting inference rule: Entity {params.rule_id} is not an "
            f"inference rule (type: {entity.entity_type.value})"
        )
    # A rule is machinery, not a knowledge claim: deleting one is a config
    # change, so it is erased rather than left behind as a retracted entity.
    store.delete_entity(params.rule_id, hard=True)
    return {"deleted": True, "ruleId": params.rule_id}


# =============================================================================
# Forward chaining
# =============================================================================


def _unify(pattern: str, value: str, bindings: dict[str, str]) -> dict[str, str] | None:
    if pattern.startswith("?"):
        if pattern in bindings:
            return bindings if bindings[pattern] == value else None
        return {**bindings, pattern: value}
    return bindings if pattern == value else None


def _match_conditions(
    conditions: list[Doc], relations: list[Doc]
) -> list[tuple[dict[str, str], list[str]]]:
    """All (bindings, matched relation ids) satisfying the condition conjunction."""

    def recurse(
        index: int, bindings: dict[str, str], matched: list[str]
    ) -> list[tuple[dict[str, str], list[str]]]:
        if index >= len(conditions):
            return [(bindings, matched)]
        condition = conditions[index]
        results: list[tuple[dict[str, str], list[str]]] = []
        for rel in relations:
            if rel["relationType"] != condition["relationType"]:
                continue
            b1 = _unify(condition["from"], rel["from"], bindings)
            if b1 is None:
                continue
            b2 = _unify(condition["to"], rel["to"], b1)
            if b2 is None:
                continue
            results.extend(recurse(index + 1, b2, [*matched, rel["id"]]))
        return results

    return recurse(0, {}, [])


def _resolve(pattern: str, bindings: dict[str, str]) -> str:
    if pattern.startswith("?"):
        if pattern not in bindings:
            raise OperationError(f"Unbound variable in conclusion: {pattern}")
        return bindings[pattern]
    return pattern


def _dry_run_notice() -> Doc:
    return notice(
        "DRY_RUN",
        "This was a preview run; no inference_trace entity or derived relations were persisted.",
        hint="Omit dryRun, or pass dryRun: false (the default), to persist the run.",
    )


def run_inference(params: RunInferenceInput, multi: MultiGraph) -> Doc:
    store = _store(multi, params.graph)
    dry_run = params.dry_run or False
    timestamp = iso_now()

    rules = _rules(store)
    if params.rule_id is not None:
        rules = [r for r in rules if r["id"] == params.rule_id]
    enabled_rules = [r for r in rules if r["enabled"]]

    all_relations = [
        r.model_dump(by_alias=True, exclude_unset=True) for r in store.list_relations()
    ]
    existing = {f"{r['from']}|{r['to']}|{r['relationType']}" for r in all_relations}

    derived: list[Doc] = []
    trace_steps: list[Doc] = []
    skipped = 0
    seen_this_run: set[str] = set()

    for rule in enabled_rules:
        conclusion = rule["conclusion"]
        for bindings, matched_ids in _match_conditions(rule["conditions"], all_relations):
            from_id = _resolve(conclusion["from"], bindings)
            to_id = _resolve(conclusion["to"], bindings)
            if from_id == to_id:
                continue
            key = f"{from_id}|{to_id}|{conclusion['relationType']}"
            if key in existing or key in seen_this_run:
                skipped += 1
                continue
            seen_this_run.add(key)
            derived.append(
                {
                    "from": from_id,
                    "to": to_id,
                    "relationType": conclusion["relationType"],
                    "strength": conclusion["strength"],
                    "evidence": conclusion["evidence"],
                    "ruleName": rule["name"],
                    "ruleId": rule["id"],
                }
            )
            trace_steps.append(
                {
                    "ruleId": rule["id"],
                    "ruleName": rule["name"],
                    "bindings": dict(bindings),
                    "inputRelationIds": matched_ids,
                    "derivedFact": {
                        "from": from_id,
                        "to": to_id,
                        "relationType": conclusion["relationType"],
                        "relationId": None,
                    },
                }
            )

    trace_id: str | None = None
    trace_preview: Doc | None = None
    if trace_steps or enabled_rules:
        trace_data = {
            "timestamp": timestamp,
            "rulesEvaluated": len(enabled_rules),
            "dryRun": dry_run,
            "filteredRuleId": params.rule_id,
            "steps": trace_steps,
            "derivedFactCount": len(trace_steps),
            "skippedDuplicates": skipped,
        }
        if dry_run:
            # A preview run must not mutate the graph at all — not even to
            # record that it happened (TL-472: trace creation used to sit
            # above this guard). Return the payload the entity would have
            # held, without ever writing it.
            trace_preview = trace_data
        else:
            trace_entity = store.create_entity(
                EntityCreate.model_validate(
                    {
                        "name": f"[Trace] inference-run-{timestamp}",
                        "entityType": "inference_trace",
                        "observations": [
                            TRACE_DATA_PREFIX + json.dumps(trace_data),
                            (
                                f"Inference run: {len(enabled_rules)} rules evaluated, "
                                f"{len(trace_steps)} facts derived, {skipped} duplicates skipped"
                            ),
                        ],
                    }
                )
            )
            trace_id = trace_entity.id

            from theloom.model import RelationCreate

            wrote_ids = False
            for entry, step in zip(derived, trace_steps, strict=True):
                rel_type = entry["relationType"]
                polarity = CAUSAL_POLARITY_DEFAULTS.get(RelationType(rel_type))
                created = store.create_relation(
                    RelationCreate.model_validate(
                        {
                            "from": entry["from"],
                            "to": entry["to"],
                            "relationType": rel_type,
                            "polarity": polarity,
                            "strength": entry["strength"],
                            "evidence": entry["evidence"],
                            "provenance": {
                                "sourceType": "inference",
                                "sourceId": entry["ruleId"],
                                "externalRef": trace_id,
                                "extractionDate": timestamp,
                                "extractor": "inference-engine",
                                "extractionMethod": "inference_rule_derivation",
                            },
                        }
                    )
                )
                step["derivedFact"]["relationId"] = created.id
                wrote_ids = True
            if wrote_ids:
                trace_data["steps"] = trace_steps
                store.update_entity(
                    trace_id,
                    {
                        "observations": [
                            TRACE_DATA_PREFIX + json.dumps(trace_data),
                            (
                                f"Inference run: {len(enabled_rules)} rules evaluated, "
                                f"{len(trace_steps)} facts derived, {skipped} duplicates skipped"
                            ),
                        ]
                    },
                )

    result: Doc = {
        "rulesEvaluated": len(enabled_rules),
        "derivedRelations": derived,
        "skippedDuplicates": skipped,
        "dryRun": dry_run,
        "traceId": trace_id,
    }
    if trace_preview is not None:
        result["tracePreview"] = trace_preview

    # `applied` tracks whether this call actually persisted a trace entity
    # (and, with it, any derived relations) — true exactly when trace_id was
    # assigned, which only happens on the non-dry-run branch above.
    dry_notices = [_dry_run_notice()] if dry_run else []
    return with_notices(result, dry_notices, applied=trace_id is not None)


# =============================================================================
# Traces
# =============================================================================


def _traces(store: FalkorGraphStore) -> list[Doc]:
    docs = [
        e.model_dump(by_alias=True, exclude_unset=True)
        for e in store.list_entities(EntityFilter.model_validate({"entityType": "inference_trace"}))
    ]
    return [t for t in (_entity_to_trace(d) for d in docs) if t is not None]


def inference_trace_list(params: InferenceTraceListInput, multi: MultiGraph) -> list[Doc]:
    traces = _traces(_store(multi, params.graph))
    if params.rule_id is not None:
        traces = [
            t
            for t in traces
            if any(s["ruleId"] == params.rule_id for s in t["steps"])
            or t.get("filteredRuleId") == params.rule_id
        ]
    traces.sort(key=lambda t: t["timestamp"], reverse=True)
    limit = params.limit if params.limit is not None else 50
    return traces[:limit]


def _get_trace(store: FalkorGraphStore, trace_id: str) -> Doc | None:
    entity = store.read_entity(trace_id)
    if entity is None or entity.entity_type.value != "inference_trace":
        return None
    return _entity_to_trace(entity.model_dump(by_alias=True, exclude_unset=True))


def inference_trace_get(params: InferenceTraceGetInput, multi: MultiGraph) -> Doc:
    trace = _get_trace(_store(multi, params.graph), params.trace_id)
    if trace is None:
        raise NotFoundError(f"Inference trace not found: {params.trace_id}")
    return trace


def inference_trace_for_fact(params: InferenceTraceForFactInput, multi: MultiGraph) -> Doc:
    store = _store(multi, params.graph)
    relations = [r.model_dump(by_alias=True, exclude_unset=True) for r in store.list_relations()]
    relation = next((r for r in relations if r["id"] == params.relation_id), None)
    provenance = relation.get("provenance") if relation else None
    if (
        relation is None
        or not provenance
        or provenance.get("sourceType") != "inference"
        or not provenance.get("externalRef")
    ):
        raise NotFoundError(f"No inference trace found for relation: {params.relation_id}")
    trace = _get_trace(store, provenance["externalRef"])
    if trace is None:
        raise NotFoundError(f"No inference trace found for relation: {params.relation_id}")
    return trace


def explain_inference(params: ExplainInferenceInput, multi: MultiGraph) -> Doc:
    store = _store(multi, params.graph)
    relations = [r.model_dump(by_alias=True, exclude_unset=True) for r in store.list_relations()]
    relation = next((r for r in relations if r["id"] == params.relation_id), None)
    if relation is None:
        raise NotFoundError(f"Relation not found: {params.relation_id}")
    provenance = relation.get("provenance")
    if (
        not provenance
        or provenance.get("sourceType") != "inference"
        or not provenance.get("externalRef")
    ):
        raise NotFoundError(
            f"No inference trace found for relation {params.relation_id}. "
            "This relation may not have been derived by the inference engine."
        )
    trace = _get_trace(store, provenance["externalRef"])
    if trace is None:
        raise NotFoundError(
            f"No inference trace found for relation {params.relation_id}. "
            "This relation may not have been derived by the inference engine."
        )

    steps = trace["steps"]
    relations_by_id = {r["id"]: r for r in relations}
    entity_name_cache: dict[str, str] = {}

    def name_of(entity_id: str) -> str:
        if entity_id not in entity_name_cache:
            entity = store.read_entity(entity_id)
            entity_name_cache[entity_id] = entity.name if entity else entity_id
        return entity_name_cache[entity_id]

    all_entity_ids: dict[str, None] = {}
    explanation_steps: list[Doc] = []
    for i, step in enumerate(steps):
        resolved_bindings: Doc = {}
        for var, entity_id in step["bindings"].items():
            all_entity_ids.setdefault(entity_id)
            resolved_bindings[var] = {"entityId": entity_id, "entityName": name_of(entity_id)}

        input_facts: list[Doc] = []
        for rel_id in step["inputRelationIds"]:
            rel = relations_by_id.get(rel_id)
            if rel is None:
                input_facts.append(
                    {
                        "relationId": rel_id,
                        "fromId": "unknown",
                        "fromName": "unknown",
                        "toId": "unknown",
                        "toName": "unknown",
                        "relationType": "unknown",
                    }
                )
            else:
                all_entity_ids.setdefault(rel["from"])
                all_entity_ids.setdefault(rel["to"])
                input_facts.append(
                    {
                        "relationId": rel_id,
                        "fromId": rel["from"],
                        "fromName": name_of(rel["from"]),
                        "toId": rel["to"],
                        "toName": name_of(rel["to"]),
                        "relationType": rel["relationType"],
                    }
                )

        df = step["derivedFact"]
        all_entity_ids.setdefault(df["from"])
        all_entity_ids.setdefault(df["to"])
        derived_fact = {
            "fromId": df["from"],
            "fromName": name_of(df["from"]),
            "toId": df["to"],
            "toName": name_of(df["to"]),
            "relationType": df["relationType"],
            "relationId": df.get("relationId"),
        }
        input_facts_text = " AND ".join(
            f'"{f["fromName"]}" --[{f["relationType"]}]--> "{f["toName"]}"' for f in input_facts
        )
        step_text = (
            f'Step {i + 1}: Rule "{step["ruleName"]}" matched: {input_facts_text}. '
            f'Derived: "{derived_fact["fromName"]}" --[{df["relationType"]}]--> '
            f'"{derived_fact["toName"]}".'
        )
        explanation_steps.append(
            {
                "stepNumber": i + 1,
                "ruleId": step["ruleId"],
                "ruleName": step["ruleName"],
                "bindings": resolved_bindings,
                "inputFacts": input_facts,
                "derivedFact": derived_fact,
                "text": step_text,
            }
        )

    unique_rules = {s["ruleId"] for s in steps}
    from_name = name_of(relation["from"])
    to_name = name_of(relation["to"])
    header_line = (
        f'Inference Explanation for: "{from_name}" --[{relation["relationType"]}]--> "{to_name}"'
    )
    trace_line = (
        f"Trace ID: {trace['traceId']} "
        f"({'dry run' if trace['dryRun'] else 'applied'}, {trace['timestamp']})"
    )
    summary_line = (
        f"{len(steps)} step(s) involving {len(unique_rules)} rule(s) and "
        f"{len(all_entity_ids)} entity/entities."
    )
    steps_text = "\n".join(s["text"] for s in explanation_steps)
    full_text = "\n".join([header_line, trace_line, summary_line, "", steps_text])

    return {
        "text": full_text,
        "structured": {
            "relationId": params.relation_id,
            "traceId": trace["traceId"],
            "timestamp": trace["timestamp"],
            "dryRun": trace["dryRun"],
            "steps": explanation_steps,
            "summary": {
                "totalSteps": len(steps),
                "rulesInvolved": len(unique_rules),
                "entitiesInvolved": len(all_entity_ids),
            },
        },
    }
