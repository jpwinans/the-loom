"""Self-Improvement Loop composite.

The capstone composite chains every preceding feature into a six-section cycle:

1. reconnaissance   -> graph-reconnaissance() structural snapshot
2. capabilityCheck  -> derive a CapabilitySpec + require-test-coverage, validate
3. propose          -> entity-proposer (pattern_completion) against the spec
4. simulate         -> simulate-change() per proposal, tag simulatedImpact
5. rank             -> score by violationsResolved / blastRadius, drop degraders
6. apply            -> (autoApply only) materialize top-N + credit + procedure

Human-in-the-loop by default (autoApply=false), which keeps the whole cycle
deterministic: the apply section returns an empty result and never mutates the
graph. Every section runs through :func:`run_composite`'s
:func:`time_section` for fault isolation. A proposal whose simulation raises
is tagged ``simulation_failed`` and ranks with the degraders — "could not
evaluate" never outranks "evaluated badly" — so it is filtered out before
auto-apply.

Within apply, each proposal is a short saga with no silent resting state.
The entity write is one atomic mutation; its relations go through the same
verification gate as create-relation (CAUSAL_POLARITY_DEFAULTS applies to
causal types), and a relation the gate rejects (e.g. a targetId retracted
between propose and apply) lands in ``failedWrites`` with a reason instead of
the graph. The gate-passing relations then commit as ONE all-or-none store
batch — and if that batch fails to write, the just-created entity is
hard-deleted again and the proposal is reported in ``applyFailures``, so an
applied entity whose proposed relations silently failed to land is not a
reachable state. Credit propagation and procedure-tracking failures are
non-fatal but never suppressed: they surface in ``creditFailures`` and
``applyFailures``. The top-level result is
``{composite, reconnaissance, violations, proposals, applied, failedWrites,
creditFailures, applyFailures, summary}``.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pydantic import Field

from theloom.composites.framework import run_composite
from theloom.composites.graph_reconnaissance import GraphReconInput, graph_reconnaissance
from theloom.composites.simulate_change import (
    SimulateChangeInput,
    SimulationMutation,
    simulate_change,
)
from theloom.model import CAUSAL_POLARITY_DEFAULTS, RelationCreate
from theloom.operations.common import CommandInput
from theloom.operations.entity import CreateEntityInput
from theloom.operations.entity import create_entity as create_entity_op
from theloom.operations.entity_proposal import EntityProposalOptions
from theloom.operations.epistemic import PropagateCreditInput, propagate_credit
from theloom.operations.relations import CreateRelationInput, gated_relation_spec
from theloom.operations.relations import create_relation as create_relation_op
from theloom.semantic.entity_proposer import propose_entities as propose_entities_op
from theloom.store.multigraph import MultiGraph
from theloom.timeutil import iso_now
from theloom.verification.capability_spec import CapabilitySpec

Doc = dict[str, Any]

DEFAULT_MAX_PROPOSALS = 10
DEFAULT_APPLY_TOP_N = 3


class SelfImproveInput(CommandInput):
    """Input for the self-improvement loop."""

    graph: str | None = None
    auto_apply: bool | None = Field(default=False, alias="autoApply")
    max_proposals: int | None = Field(default=None, ge=1, le=100, alias="maxProposals")
    apply_top_n: int | None = Field(default=None, ge=1, le=20, alias="applyTopN")


def _to_fixed(value: float, digits: int) -> str:
    """Format to a fixed number of decimals, ties away from 0."""
    quantum = Decimal(1).scaleb(-digits)
    return str(Decimal(value).quantize(quantum, rounding=ROUND_HALF_UP))


def _build_mutations_for_proposal(proposal: Doc) -> list[SimulationMutation]:
    """Build the SimulationMutation list from an EntityProposal."""
    mutations: list[SimulationMutation] = [
        SimulationMutation(
            type="createEntity",
            payload={
                "name": proposal["entity"]["name"],
                "entityType": proposal["entity"]["entityType"],
                "observations": proposal["entity"]["observations"],
            },
        )
    ]
    for rel in proposal["relations"]:
        if not rel.get("targetId"):
            continue
        evidence = f"Self-improve simulation: {proposal['rationale']}"
        # Mirror the same CAUSAL_POLARITY_DEFAULTS the gated apply-path applies,
        # so the simulated mutation matches what would actually be written.
        polarity = CAUSAL_POLARITY_DEFAULTS.get(rel["relationType"])
        if rel["direction"] == "outgoing":
            payload: Doc = {
                "from": "__LAST_CREATED__",
                "to": rel["targetId"],
                "relationType": rel["relationType"],
                "polarity": polarity,
                "strength": "moderate",
                "evidence": evidence,
            }
        else:
            payload = {
                "from": rel["targetId"],
                "to": "__LAST_CREATED__",
                "relationType": rel["relationType"],
                "polarity": polarity,
                "strength": "moderate",
                "evidence": evidence,
            }
        mutations.append(SimulationMutation(type="createRelation", payload=payload))
    return mutations


def _build_summary(
    violations: list[Doc],
    ranked_proposals: list[Doc],
    applied_proposals: list[Doc],
    failed_writes: list[Doc],
    credit_failures: list[Doc],
    apply_failures: list[Doc],
    auto_apply: bool,
    duration_ms: int,
) -> str:
    """Build the human-readable summary."""
    lines: list[str] = []
    lines.append(f"Self-Improvement Cycle Complete ({duration_ms}ms)")
    lines.append("")

    if len(violations) == 0:
        lines.append("No capability violations detected. Graph is structurally sound.")
    else:
        lines.append(f"Detected {len(violations)} capability violation(s).")
        by_type: dict[str, int] = {}
        for v in violations:
            by_type[v["violationType"]] = by_type.get(v["violationType"], 0) + 1
        for vtype, count in by_type.items():
            lines.append(f"  - {vtype}: {count}")

    lines.append("")

    if len(ranked_proposals) == 0:
        lines.append("No improvement proposals generated.")
    else:
        lines.append(f"Generated {len(ranked_proposals)} ranked proposal(s):")
        for i in range(min(len(ranked_proposals), 5)):
            r = ranked_proposals[i]
            lines.append(
                f"  {i + 1}. {r['proposal']['entity']['name']} "
                f"(score: {_to_fixed(r['score'], 3)}, "
                f"violations: {r['violationsResolved']}, blast: {r['blastRadius']}, "
                f"verdict: {r['simulationVerdict']})"
            )
        if len(ranked_proposals) > 5:
            lines.append(f"  ... and {len(ranked_proposals) - 5} more")

    lines.append("")

    if auto_apply:
        if len(applied_proposals) == 0:
            lines.append("Auto-apply enabled but no proposals were applied.")
        else:
            lines.append(f"Applied {len(applied_proposals)} proposal(s):")
            for ap in applied_proposals:
                lines.append(
                    f"  - {ap['ranked']['proposal']['entity']['name']} "
                    f"-> entity {ap['createdEntityId']}"
                )
        if len(failed_writes) > 0:
            lines.append(f"Failed {len(failed_writes)} relation write(s):")
            for fw in failed_writes:
                lines.append(
                    f"  - {fw['proposalEntityName']} -> {fw['targetId']} "
                    f"({fw['relationType']}): {fw['reason']}"
                )
        if len(apply_failures) > 0:
            lines.append(f"Failed {len(apply_failures)} apply step(s):")
            for af in apply_failures:
                lines.append(f"  - {af['proposalEntityName']} [{af['stage']}]: {af['reason']}")
        if len(credit_failures) > 0:
            lines.append(f"Failed {len(credit_failures)} credit propagation(s):")
            for cf in credit_failures:
                lines.append(f"  - {cf['proposalEntityName']}: {cf['reason']}")
    else:
        lines.append("Auto-apply disabled. Review proposals and apply manually.")

    return "\n".join(lines)


def self_improve(params: SelfImproveInput, multi: MultiGraph) -> Doc:
    """Execute detect -> reason -> propose -> simulate -> rank -> apply."""
    graph = params.graph
    max_proposals = min(
        max(
            params.max_proposals if params.max_proposals is not None else DEFAULT_MAX_PROPOSALS,
            1,
        ),
        100,
    )
    apply_top_n = min(
        max(params.apply_top_n if params.apply_top_n is not None else DEFAULT_APPLY_TOP_N, 1),
        20,
    )
    auto_apply = params.auto_apply if params.auto_apply is not None else False

    # State threaded across sections (sections run sequentially).
    st: dict[str, Any] = {"cap_spec": None, "proposals": [], "ranked": []}

    # -- Section 1: Reconnaissance --------------------------------------------
    def _reconnaissance() -> Doc:
        result: Doc = graph_reconnaissance(GraphReconInput(graph=graph), multi)["result"]
        return result

    # -- Section 2: Capability Check ------------------------------------------
    def _capability_check() -> Doc:
        store = multi.get_store(graph)
        if st["cap_spec"] is None:
            spec = CapabilitySpec()
            spec.derive_from_graph(store)
            spec.require_test_coverage()
            st["cap_spec"] = spec
        cap_result = st["cap_spec"].validate(store)
        result = {
            "pass": cap_result["pass"],
            "totalViolations": len(cap_result["violations"]),
            "violations": cap_result["violations"],
        }
        st["capability_check_data"] = result
        return result

    # -- Section 3: Propose ----------------------------------------------------
    def _propose() -> Doc:
        store = multi.get_store(graph)
        options = EntityProposalOptions.model_validate(
            {
                "limit": max_proposals,
                "simulate": False,  # simulated separately in Section 4
                "strategies": ["pattern_completion"],
                "graph": graph,
                "capabilitySpec": st["cap_spec"],
            }
        )
        propose_result = propose_entities_op(store, options.to_options())
        st["proposals"] = propose_result["proposals"]
        return {
            "totalProposals": len(propose_result["proposals"]),
            "strategyCounts": propose_result["strategyCounts"],
        }

    # -- Section 4: Simulate ---------------------------------------------------
    def _simulate() -> Doc:
        improves = 0
        neutral = 0
        degrades = 0
        for proposal in st["proposals"]:
            try:
                mutations = _build_mutations_for_proposal(proposal)
                sim_result = simulate_change(
                    SimulateChangeInput(mutations=mutations, graph=graph), multi
                )
                verdict = sim_result["result"]["verdict"]["data"]
                blast_radius = sim_result["result"]["blastRadius"]["data"]
                classification = verdict["classification"] if verdict else "neutral"
                proposal["simulatedImpact"] = {
                    "verdict": classification,
                    "reasons": verdict["reasons"] if verdict else [],
                    "blastRadius": blast_radius["affected"] if blast_radius else 0,
                }
                if classification == "improves":
                    improves += 1
                elif classification == "degrades":
                    degrades += 1
                else:
                    neutral += 1
            except Exception as exc:  # noqa: BLE001 — simulation failure is not fatal,
                # but it ranks with the degraders: "could not evaluate" must
                # never outrank "evaluated badly", so the proposal is tagged
                # simulation_failed and filtered out of auto-apply.
                proposal["simulatedImpact"] = {
                    "verdict": "simulation_failed",
                    "reasons": [f"simulation raised: {exc}"],
                    "blastRadius": 0,
                }
                degrades += 1
        return {
            "simulatedCount": len(st["proposals"]),
            "improves": improves,
            "neutral": neutral,
            "degrades": degrades,
        }

    # -- Section 5: Rank -------------------------------------------------------
    def _rank() -> Doc:
        cc_data = st.get("capability_check_data")
        baseline_violations = cc_data["violations"] if cc_data else []
        violations_by_capability: dict[str, int] = {}
        for v in baseline_violations:
            name = v["capabilityName"]
            violations_by_capability[name] = violations_by_capability.get(name, 0) + 1

        ranked: list[Doc] = []
        for proposal in st["proposals"]:
            cap_viol = proposal.get("capabilityViolation")
            violations_resolved = violations_by_capability.get(cap_viol, 0) if cap_viol else 0
            sim_impact = proposal.get("simulatedImpact")
            blast_radius = sim_impact["blastRadius"] if sim_impact else 0
            simulation_verdict = sim_impact["verdict"] if sim_impact else "not_simulated"
            # Degrading proposals are treated as increasing violations, and an
            # un-simulatable proposal ranks no better than one that degrades.
            increases_violations = simulation_verdict in ("degrades", "simulation_failed")
            score = violations_resolved / max(blast_radius, 1)
            ranked.append(
                {
                    "proposal": proposal,
                    "violationsResolved": violations_resolved,
                    "blastRadius": blast_radius,
                    "score": score,
                    "increasesViolations": increases_violations,
                    "simulationVerdict": simulation_verdict,
                }
            )

        filtered = [r for r in ranked if not r["increasesViolations"]]
        filtered_count = len(ranked) - len(filtered)
        filtered.sort(key=lambda r: r["score"], reverse=True)
        st["ranked"] = filtered
        return {"rankedProposals": filtered, "filteredCount": filtered_count}

    # -- Section 6: Apply ------------------------------------------------------
    def _apply() -> Doc:
        if not auto_apply:
            return {
                "applied": [],
                "autoApply": False,
                "failedWrites": [],
                "creditFailures": [],
                "applyFailures": [],
            }

        store = multi.get_store(graph)
        to_apply = st["ranked"][:apply_top_n]
        applied: list[Doc] = []
        failed_writes: list[Doc] = []
        credit_failures: list[Doc] = []
        apply_failures: list[Doc] = []

        for ranked_item in to_apply:
            proposal = ranked_item["proposal"]
            proposal_name = proposal["entity"]["name"]
            now = iso_now()
            try:
                entity_doc = create_entity_op(
                    CreateEntityInput.model_validate(
                        {
                            "name": proposal_name,
                            "entityType": proposal["entity"]["entityType"],
                            "observations": proposal["entity"]["observations"],
                            "confidence": {
                                "score": proposal["confidence"],
                                "basis": "inference",
                                "lastEvaluated": now,
                            },
                            "provenance": {
                                "sourceType": "inference",
                                "sourceId": None,
                                "externalRef": None,
                                "extractionDate": now,
                                "extractor": "self-improve-composite",
                                "extractionMethod": "automated",
                            },
                            "graph": graph,
                        }
                    ),
                    multi,
                )
            except Exception as exc:  # noqa: BLE001 — reported, not swallowed.
                apply_failures.append(
                    {
                        "proposalEntityName": proposal_name,
                        "stage": "createEntity",
                        "reason": str(exc),
                    }
                )
                continue
            entity_id = entity_doc["id"]

            # Same gated path as create-relation: the polarity is left null
            # here and the gate applies CAUSAL_POLARITY_DEFAULTS, and it can
            # reject a relation (e.g. a retracted endpoint) rather than let it
            # through — a gate rejection lands in failedWrites. The specs that
            # pass the gate then commit as ONE all-or-none batch below.
            specs: list[RelationCreate] = []
            for rel in proposal["relations"]:
                if not rel.get("targetId"):
                    continue
                if rel["direction"] == "outgoing":
                    from_id, to_id = entity_id, rel["targetId"]
                else:
                    from_id, to_id = rel["targetId"], entity_id
                try:
                    spec = gated_relation_spec(
                        CreateRelationInput.model_validate(
                            {
                                "from": from_id,
                                "to": to_id,
                                "relationType": rel["relationType"],
                                "polarity": None,
                                "strength": "moderate",
                                "evidence": (
                                    f"Applied by self-improve composite: {proposal['rationale']}"
                                ),
                                "graph": graph,
                            }
                        ),
                        multi,
                    )
                    specs.append(spec)
                except Exception as exc:  # noqa: BLE001 — reported, not swallowed.
                    failed_writes.append(
                        {
                            "proposalEntityName": proposal_name,
                            "targetId": rel["targetId"],
                            "relationType": rel["relationType"],
                            "direction": rel["direction"],
                            "reason": str(exc),
                        }
                    )

            created_relation_ids: list[str] = []
            if specs:
                try:
                    created_relation_ids = [r.id for r in store.create_relations(specs)]
                except Exception as exc:  # noqa: BLE001 — saga rollback, then report.
                    # The batch is all-or-none, so no relation landed — and an
                    # applied entity whose proposed relations silently failed
                    # to write is not a resting state: take the entity back
                    # out and report the whole proposal as failed.
                    failure: Doc = {
                        "proposalEntityName": proposal_name,
                        "stage": "createRelations",
                        "reason": str(exc),
                        "rolledBackEntityId": entity_id,
                    }
                    try:
                        store.delete_entity(entity_id, hard=True)
                    except Exception as rollback_exc:  # noqa: BLE001 — still reported.
                        # The compensating delete failed too: the entity is
                        # stranded in the graph. Keep its id under its own key
                        # (names are not unique) so an operator can clean up;
                        # rolledBackEntityId: None keeps meaning "nothing was
                        # rolled back".
                        failure["rolledBackEntityId"] = None
                        failure["strandedEntityId"] = entity_id
                        failure["rollbackError"] = str(rollback_exc)
                    apply_failures.append(failure)
                    continue

            try:
                propagate_credit(
                    PropagateCreditInput.model_validate(
                        {
                            "entityIds": [entity_id],
                            "delta": 0.1,
                            "dampingFactor": 0.5,
                            "maxDepth": 2,
                            "dryRun": False,
                            "graph": graph,
                        }
                    ),
                    multi,
                )
            except Exception as exc:  # noqa: BLE001 — non-fatal, but never silent.
                credit_failures.append(
                    {
                        "proposalEntityName": proposal_name,
                        "entityId": entity_id,
                        "reason": str(exc),
                    }
                )

            procedure_entity_id: str | None = None
            try:
                procedure_entity_doc = create_entity_op(
                    CreateEntityInput.model_validate(
                        {
                            "name": f"Self-Improve: Applied {proposal_name}",
                            "entityType": "procedure",
                            "observations": [
                                f"Self-improvement loop applied proposal: {proposal_name}",
                                f"Strategy: {proposal['strategy']}",
                                f"Rationale: {proposal['rationale']}",
                                f"Violations addressed: {ranked_item['violationsResolved']}",
                                f"Blast radius: {ranked_item['blastRadius']}",
                                f"Ranking score: {_to_fixed(ranked_item['score'], 4)}",
                                f"Simulation verdict: {ranked_item['simulationVerdict']}",
                                f"Created entity: {entity_id}",
                                (f"Created relations: {', '.join(created_relation_ids) or 'none'}"),
                            ],
                            "provenance": {
                                "sourceType": "inference",
                                "sourceId": entity_id,
                                "externalRef": None,
                                "extractionDate": now,
                                "extractor": "self-improve-composite",
                                "extractionMethod": "automated",
                            },
                            "graph": graph,
                        }
                    ),
                    multi,
                )
                procedure_entity_id = procedure_entity_doc["id"]
                create_relation_op(
                    CreateRelationInput.model_validate(
                        {
                            "from": procedure_entity_id,
                            "to": entity_id,
                            "relationType": "sources",
                            "polarity": None,
                            "strength": "strong",
                            "evidence": "Self-improvement loop procedure tracking",
                            "graph": graph,
                        }
                    ),
                    multi,
                )
            except Exception as exc:  # noqa: BLE001 — non-fatal, but never silent.
                apply_failures.append(
                    {
                        "proposalEntityName": proposal_name,
                        "stage": "procedureTracking",
                        "reason": str(exc),
                    }
                )

            applied.append(
                {
                    "ranked": ranked_item,
                    "createdEntityId": entity_id,
                    "createdRelationIds": created_relation_ids,
                    "procedureEntityId": procedure_entity_id,
                }
            )

        return {
            "applied": applied,
            "autoApply": True,
            "failedWrites": failed_writes,
            "creditFailures": credit_failures,
            "applyFailures": apply_failures,
        }

    # -- Build result ----------------------------------------------------------
    composite = run_composite(
        [
            ("reconnaissance", _reconnaissance),
            ("capabilityCheck", _capability_check),
            ("propose", _propose),
            ("simulate", _simulate),
            ("rank", _rank),
            ("apply", _apply),
        ]
    )
    total_ms = composite["metadata"]["totalDurationMs"]

    reconnaissance_data = composite["result"]["reconnaissance"]["data"]
    cc_data = composite["result"]["capabilityCheck"]["data"]
    violations = cc_data["violations"] if cc_data else []
    apply_data = composite["result"]["apply"]["data"]
    applied_proposals = apply_data["applied"] if apply_data else []
    failed_writes = apply_data["failedWrites"] if apply_data else []
    credit_failures = apply_data["creditFailures"] if apply_data else []
    apply_failures = apply_data["applyFailures"] if apply_data else []
    summary = _build_summary(
        violations,
        st["ranked"],
        applied_proposals,
        failed_writes,
        credit_failures,
        apply_failures,
        auto_apply,
        total_ms,
    )

    return {
        "composite": composite,
        "reconnaissance": reconnaissance_data,
        "violations": violations,
        "proposals": st["ranked"],
        "applied": applied_proposals,
        "failedWrites": failed_writes,
        "creditFailures": credit_failures,
        "applyFailures": apply_failures,
        "summary": summary,
    }
