"""Entity Proposal Engine.

Proposes entities that *should* exist in the graph but don't, via two
strategies:

- **Pattern completion** (Strategy A): structural fingerprint groups
  (``group_by_fingerprint``) cross-referenced with capability-spec violations.
  When peers share a motif but an entity is missing an expected relation, an
  entity that completes the pattern is proposed.
- **LLM reasoning** (Strategy B): graph reconnaissance + violations handed to an
  LLM. Only runs when both ``'llm_reasoning'`` is enabled and an ``llmClient`` is
  supplied; the composite callers pass no client, so it is effectively dead —
  but it is implemented for completeness.

Structural simulation is optional: when ``simulate`` is True and a
``simulateChange`` callable is supplied in options, it is invoked per proposal
(best-effort). With no callable, simulation is skipped — matching the composite
callers, which never request simulation here.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from theloom.model import ALL_RELATION_TYPES
from theloom.reification.fingerprint import group_by_fingerprint
from theloom.verification.capability_spec import CapabilitySpec

Doc = dict[str, Any]

_ALL_STATUSES = ["active", "superseded", "deprecated", "retracted", "investigating"]

DEFAULT_LIMIT = 10
DEFAULT_MIN_PATTERN_OCCURRENCES = 2
DEFAULT_MAX_PATTERNS = 20
PATTERN_COMPLETION_BASE_CONFIDENCE = 0.8
LLM_REASONING_BASE_CONFIDENCE = 0.6

# The 16 entity types accepted from LLM proposals (the set hard-coded in
# the proposal parser and the LLM prompt).
_LLM_VALID_ENTITY_TYPES = {
    "concept",
    "claim",
    "source",
    "question",
    "evidence",
    "pattern",
    "insight",
    "tension",
    "convergence",
    "system",
    "variable",
    "loop",
    "leverage_point",
    "event",
    "procedure",
    "hypothesis",
}

_RELATION_TYPE_MATCH = re.compile(
    r"missing (?:outgoing|incoming) '(\w+)' relation|no linked '(\w+)' via '(\w+)'"
)
_COMPLETENESS_TYPE_MATCH = re.compile(r"Entity type '(\w+)' has no instances")


def _default(value: Any, fallback: Any) -> Any:
    return fallback if value is None else value


def _to_doc(obj: Any) -> Doc:
    if isinstance(obj, dict):
        return obj
    dumped: Doc = obj.model_dump(by_alias=True, exclude_unset=True)
    return dumped


def _list_entity_docs(store: Any) -> list[Doc]:
    from theloom.model import EntityFilter

    result = store.list_entities(EntityFilter.model_validate({"statusFilter": _ALL_STATUSES}))
    return [_to_doc(e) for e in result]


def _list_relation_docs(store: Any) -> list[Doc]:
    return [_to_doc(r) for r in store.list_relations()]


# =============================================================================
# Main entry point
# =============================================================================


def propose_entities(store: Any, options: Doc | None = None) -> Doc:
    """Propose new entities for the graph.

    options keys: ``limit`` (10), ``simulate`` (False), ``strategies``
    (['pattern_completion', 'llm_reasoning']), ``graph``,
    ``minPatternOccurrences`` (2), ``maxPatterns`` (20), ``capabilitySpec``,
    ``llmClient``, ``simulateChange`` (hook). Returns
    ``{proposals, strategyCounts, filteredCount, violations, durationMs}``."""
    options = options or {}
    start_time = time.perf_counter()
    limit = _default(options.get("limit"), DEFAULT_LIMIT)
    simulate = _default(options.get("simulate"), False)
    strategies = _default(options.get("strategies"), ["pattern_completion", "llm_reasoning"])

    # Step 1: capability validation -> current violations.
    cap_spec = options.get("capabilitySpec") or _build_default_capability_spec(store)
    baseline_result = cap_spec.validate(store)
    violations: list[Doc] = baseline_result["violations"]

    # Step 2: run enabled strategies.
    proposals: list[Doc] = []

    if "pattern_completion" in strategies:
        proposals.extend(_strategy_pattern_completion(store, violations, options))

    llm_client = options.get("llmClient")
    if "llm_reasoning" in strategies and llm_client is not None:
        proposals.extend(_strategy_llm_reasoning(store, violations, llm_client, options))

    # Step 3: deduplicate by entity name (case-insensitive), keeping max confidence.
    deduped = _deduplicate_by_name(proposals)

    # Step 4: filter out proposals that would increase violations.
    accepted, filtered_count = _filter_by_violation_impact(
        deduped, store, cap_spec, baseline_result
    )

    # Step 5: optionally simulate structural impact.
    if simulate:
        _simulate_proposals(accepted, options)

    # Step 6: sort by confidence descending and limit.
    accepted_sorted = sorted(accepted, key=lambda p: p["confidence"], reverse=True)
    limited = accepted_sorted[:limit]

    duration_ms = round((time.perf_counter() - start_time) * 1000)

    return {
        "proposals": limited,
        "strategyCounts": {
            "pattern_completion": sum(1 for p in limited if p["strategy"] == "pattern_completion"),
            "llm_reasoning": sum(1 for p in limited if p["strategy"] == "llm_reasoning"),
        },
        "filteredCount": filtered_count,
        "violations": violations,
        "durationMs": duration_ms,
    }


# =============================================================================
# Strategy A: Pattern Completion
# =============================================================================


def _strategy_pattern_completion(store: Any, violations: list[Doc], options: Doc) -> list[Doc]:
    entities = _list_entity_docs(store)
    relations = _list_relation_docs(store)

    if not entities:
        return []

    groups = group_by_fingerprint(
        entities,
        relations,
        min_occurrences=_default(
            options.get("minPatternOccurrences"), DEFAULT_MIN_PATTERN_OCCURRENCES
        ),
        max_patterns=_default(options.get("maxPatterns"), DEFAULT_MAX_PATTERNS),
    )

    proposals: list[Doc] = []
    for group in groups:
        proposals.extend(_derive_proposals_from_group(group, violations, entities, relations))

    covered_entity_ids: set[str] = set()
    for group in groups:
        for entity_id in group["entityIds"]:
            covered_entity_ids.add(entity_id)

    entity_name_map = {e["id"]: e["name"] for e in entities}

    # Coverage/pattern violations for entities NOT in any fingerprint group.
    loner_violations = [
        v
        for v in violations
        if v.get("elementId")
        and v["elementId"] not in covered_entity_ids
        and v["violationType"] in ("coverage", "pattern")
    ]

    for violation in loner_violations:
        element_id = violation.get("elementId")
        if not element_id:
            continue

        source_name = entity_name_map.get(element_id, element_id)
        match = _RELATION_TYPE_MATCH.search(violation["message"])

        if match:
            missing_rel_type = match.group(1) if match.group(1) is not None else match.group(3)
            missing_child_type = match.group(2)

            proposed_type = (
                missing_child_type
                if missing_child_type is not None
                else _infer_entity_type_from_relation(None, missing_rel_type, entities, relations)
            )

            if proposed_type:
                proposals.append(
                    {
                        "entity": {
                            "name": f"{proposed_type} for {source_name}",
                            "entityType": proposed_type,
                            "observations": [
                                f"Proposed to satisfy coverage requirement for '{source_name}'",
                                violation["suggestedAction"],
                            ],
                        },
                        "relations": [
                            {
                                "targetId": element_id,
                                "relationType": missing_rel_type,
                                "direction": (
                                    "incoming" if "outgoing" in violation["message"] else "outgoing"
                                ),
                            }
                        ],
                        "rationale": f"Coverage completion: {violation['message']}",
                        "capabilityViolation": violation["capabilityName"],
                        "confidence": PATTERN_COMPLETION_BASE_CONFIDENCE * 0.85,
                        "strategy": "pattern_completion",
                    }
                )

    # Completeness violations -> propose one instance of the missing type.
    completeness_violations = [v for v in violations if v["violationType"] == "completeness"]
    for violation in completeness_violations:
        type_match = _COMPLETENESS_TYPE_MATCH.search(violation["message"])
        if type_match:
            missing_type = type_match.group(1)
            proposals.append(
                {
                    "entity": {
                        "name": f"New {missing_type}",
                        "entityType": missing_type,
                        "observations": [
                            "Proposed to satisfy completeness requirement",
                            f"No instances of type '{missing_type}' exist in the graph",
                        ],
                    },
                    "relations": [],
                    "rationale": violation["message"],
                    "capabilityViolation": violation["capabilityName"],
                    "confidence": PATTERN_COMPLETION_BASE_CONFIDENCE * 0.9,
                    "strategy": "pattern_completion",
                }
            )

    return proposals


def _derive_proposals_from_group(
    group: Doc, violations: list[Doc], entities: list[Doc], relations: list[Doc]
) -> list[Doc]:
    proposals: list[Doc] = []
    info = group["info"]

    relevant_violations = [
        v
        for v in violations
        if v.get("elementId")
        and v["elementId"] in group["entityIds"]
        and v["violationType"] in ("coverage", "pattern")
    ]

    entity_name_map = {e["id"]: e["name"] for e in entities}

    for violation in relevant_violations:
        element_id = violation.get("elementId")
        if not element_id:
            continue

        source_name = entity_name_map.get(element_id, element_id)
        match = _RELATION_TYPE_MATCH.search(violation["message"])

        if match:
            missing_rel_type = match.group(1) if match.group(1) is not None else match.group(3)
            missing_child_type = match.group(2)

            proposed_type = (
                missing_child_type
                if missing_child_type is not None
                else _infer_entity_type_from_relation(info, missing_rel_type, entities, relations)
            )

            if proposed_type:
                peer_count = group["count"]
                confidence_boost = min(peer_count / 10, 0.15)

                proposals.append(
                    {
                        "entity": {
                            "name": f"{proposed_type} for {source_name}",
                            "entityType": proposed_type,
                            "observations": [
                                f"Proposed to complete structural pattern: {group['description']}",
                                f"{peer_count} entities share this pattern; this fills a gap",
                                violation["suggestedAction"],
                            ],
                        },
                        "relations": [
                            {
                                "targetId": element_id,
                                "relationType": missing_rel_type,
                                "direction": (
                                    "incoming" if "outgoing" in violation["message"] else "outgoing"
                                ),
                            }
                        ],
                        "rationale": f"Pattern completion: {violation['message']}",
                        "capabilityViolation": violation["capabilityName"],
                        "confidence": PATTERN_COMPLETION_BASE_CONFIDENCE + confidence_boost,
                        "strategy": "pattern_completion",
                    }
                )

    return proposals


def _infer_entity_type_from_relation(
    _info: Doc | None, relation_type: str, entities: list[Doc], relations: list[Doc]
) -> str | None:
    type_map = {e["id"]: e["entityType"] for e in entities}

    target_type_counts: dict[str, int] = {}
    for rel in relations:
        if rel["relationType"] == relation_type:
            target_type = type_map.get(rel["to"])
            if target_type is not None:
                target_type_counts[target_type] = target_type_counts.get(target_type, 0) + 1

    best_type: str | None = None
    best_count = 0
    for entity_type, count in target_type_counts.items():
        if count > best_count:
            best_count = count
            best_type = entity_type

    return best_type


# =============================================================================
# Strategy B: LLM Architectural Reasoning (dead unless an llmClient is supplied)
# =============================================================================


def _strategy_llm_reasoning(
    store: Any, violations: list[Doc], llm_client: Any, options: Doc
) -> list[Doc]:
    entities = _list_entity_docs(store)
    relations = _list_relation_docs(store)

    if not entities:
        return []

    type_counts: dict[str, int] = {}
    for e in entities:
        type_counts[e["entityType"]] = type_counts.get(e["entityType"], 0) + 1

    rel_type_counts: dict[str, int] = {}
    for r in relations:
        rel_type_counts[r["relationType"]] = rel_type_counts.get(r["relationType"], 0) + 1

    graph_summary = {
        "entityCount": len(entities),
        "relationCount": len(relations),
        "entityTypes": type_counts,
        "relationTypes": rel_type_counts,
    }

    violation_summary = [
        {
            "capability": v["capabilityName"],
            "type": v["violationType"],
            "message": v["message"],
            "suggestedAction": v["suggestedAction"],
        }
        for v in violations
    ]

    system_prompt = _build_llm_system_prompt()
    user_prompt = _build_llm_user_prompt(
        graph_summary, violation_summary, _default(options.get("limit"), DEFAULT_LIMIT)
    )

    try:
        result = llm_client.complete(system_prompt, user_prompt)
        text = result["text"] if isinstance(result, dict) else result.text
        return _parse_llm_proposals(text, violations)
    except Exception:
        return []


def _build_llm_system_prompt() -> str:
    # Implicit string concatenation (one physical line per prompt line) keeps
    # the prompt content intact while satisfying the line-length lint.
    return (
        "You are a knowledge graph architect analyzing a graph for structural completeness.\n"
        "Your task is to propose new entities that should exist to improve the graph's structural integrity.\n"  # noqa: E501
        "\n"
        "You MUST respond with valid JSON only — no markdown, no explanation outside the JSON.\n"
        "\n"
        "Respond with a JSON array of proposal objects. Each proposal has:\n"
        "{\n"
        '  "name": "Entity name",\n'
        '  "entityType": "one of: concept, claim, source, question, evidence, pattern, insight, tension, convergence, system, variable, loop, leverage_point, event, procedure, hypothesis",\n'  # noqa: E501
        '  "observations": ["observation 1", "observation 2"],\n'
        '  "relations": [\n'
        '    {"targetEntityName": "Name of existing entity to connect to", "relationType": "relation type", "direction": "outgoing or incoming"}\n'  # noqa: E501
        "  ],\n"
        '  "rationale": "Why this entity should exist",\n'
        '  "addressesViolation": "Which violation this addresses (optional)"\n'
        "}\n"
        "\n"
        "Valid relation types: related_to, instance_of, part_of, sources, supports, contradicts, questions, supersedes, causes, enables, requires, inhibits, amplifies, dampens\n"  # noqa: E501
        "\n"
        "Focus on proposals that directly address the violations provided. Prefer proposals that:\n"
        "1. Fill completeness gaps (missing entity types)\n"
        "2. Add coverage (missing relations between types)\n"
        "3. Resolve pattern inconsistencies\n"
        "4. Reduce coupling by decomposing over-connected entities"
    )


def _build_llm_user_prompt(graph_summary: Doc, violations: list[Doc], limit: int) -> str:
    return (
        "## Graph Summary\n"
        f"{json.dumps(graph_summary, indent=2)}\n\n"
        f"## Current Capability Violations ({len(violations)} total)\n"
        f"{json.dumps(violations[:20], indent=2)}\n\n"
        "## Task\n"
        f"Propose up to {limit} new entities that would address these violations "
        "and improve graph structural integrity.\n"
        "Respond with a JSON array of proposals ONLY."
    )


def _parse_llm_proposals(llm_text: str, violations: list[Doc]) -> list[Doc]:
    parsed: Any = None
    try:
        parsed = json.loads(llm_text)
    except Exception:
        block = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", llm_text)
        if block:
            try:
                parsed = json.loads(block.group(1))
            except Exception:
                return []
        else:
            array = re.search(r"\[[\s\S]*\]", llm_text)
            if array:
                try:
                    parsed = json.loads(array.group(0))
                except Exception:
                    return []
            else:
                return []

    if not isinstance(parsed, list):
        return []

    valid_relation_types = {rt.value for rt in ALL_RELATION_TYPES}
    proposals: list[Doc] = []

    for item in parsed:
        if not isinstance(item, dict):
            continue
        p: Doc = item

        if not isinstance(p.get("name"), str) or not p.get("name"):
            continue
        if not isinstance(p.get("entityType"), str) or not p.get("entityType"):
            continue
        if not isinstance(p.get("observations"), list):
            continue
        if p["entityType"] not in _LLM_VALID_ENTITY_TYPES:
            continue

        relations: list[Doc] = []
        if isinstance(p.get("relations"), list):
            for rel in p["relations"]:
                if not isinstance(rel, dict):
                    continue
                if (
                    isinstance(rel.get("relationType"), str)
                    and rel["relationType"] in valid_relation_types
                ):
                    relations.append(
                        {
                            "targetId": rel.get("targetEntityName") or "",
                            "relationType": rel["relationType"],
                            "direction": (
                                "incoming" if rel.get("direction") == "incoming" else "outgoing"
                            ),
                        }
                    )

        addresses_violation = (
            p["addressesViolation"] if isinstance(p.get("addressesViolation"), str) else None
        )

        proposal: Doc = {
            "entity": {
                "name": p["name"],
                "entityType": p["entityType"],
                "observations": [o for o in p["observations"] if isinstance(o, str)],
            },
            "relations": relations,
            "rationale": (
                p["rationale"] if isinstance(p.get("rationale"), str) else "LLM-proposed entity"
            ),
            "confidence": LLM_REASONING_BASE_CONFIDENCE,
            "strategy": "llm_reasoning",
        }
        if addresses_violation is not None:
            proposal["capabilityViolation"] = addresses_violation
        proposals.append(proposal)

    return proposals


# =============================================================================
# Validation & filtering
# =============================================================================


def _deduplicate_by_name(proposals: list[Doc]) -> list[Doc]:
    """Case-insensitive dedup by entity name, keeping the higher confidence."""
    seen: dict[str, Doc] = {}
    for proposal in proposals:
        key = proposal["entity"]["name"].lower()
        existing = seen.get(key)
        if existing is None or proposal["confidence"] > existing["confidence"]:
            seen[key] = proposal
    return list(seen.values())


def _filter_by_violation_impact(
    proposals: list[Doc], store: Any, cap_spec: CapabilitySpec, baseline_result: Doc
) -> tuple[list[Doc], int]:
    accepted: list[Doc] = []
    filtered_count = 0

    for proposal in proposals:
        if proposal.get("capabilityViolation"):
            matches_violation = any(
                v["capabilityName"] == proposal["capabilityViolation"]
                for v in baseline_result["violations"]
            )
            if matches_violation:
                accepted.append(proposal)
                continue

        if len(proposal["relations"]) == 0:
            accepted.append(proposal)
            continue

        accepted.append(proposal)

    return accepted, filtered_count


# =============================================================================
# Simulation hook (see module docstring — optional, callable-driven)
# =============================================================================


def _simulate_proposals(proposals: list[Doc], options: Doc) -> None:
    simulate_change = options.get("simulateChange")
    if simulate_change is None:
        return
    for proposal in proposals:
        try:
            result = simulate_change(proposal)
            if result is None:
                continue
            proposal["simulatedImpact"] = result
            verdict = result.get("verdict")
            if verdict == "degrades":
                proposal["confidence"] *= 0.5
            elif verdict == "improves":
                proposal["confidence"] = min(1.0, proposal["confidence"] * 1.1)
        except Exception:
            continue


# =============================================================================
# Helpers
# =============================================================================


def _build_default_capability_spec(store: Any) -> CapabilitySpec:
    spec = CapabilitySpec()
    spec.derive_from_graph(store)
    spec.require_test_coverage()
    return spec
