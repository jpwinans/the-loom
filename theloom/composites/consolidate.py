"""``consolidate`` (desire 13): the dreaming pass. A composite over
Part 5's branchable belief worlds (``theloom.store.worlds`` /
``theloom.operations.worlds``) plus six read passes, each REUSING an
existing operations-layer function rather than reimplementing its
detection logic:

- **contradiction** -- ``theloom.operations.epistemic.contested_claims``
  run before AND after ``theloom.operations.inference.run_inference``
  (every enabled rule) inside the dream, so a contradiction that only
  exists once inference derives a new ``contradicts`` edge is caught and
  marked ``transitive``. This is the one pass that WRITES beyond its own
  findings (the derived relations + an ``inference_trace`` entity) --
  legitimate because it all lands in the dream's own segment, never main.
- **staleness** -- ``theloom.operations.epistemic.stale_beliefs``.
- **motifs** -- ``theloom.operations.analysis.find_frequent_subgraphs_op``,
  unrestricted over the whole graph (The Loom's graph IS the externalized
  reasoning trace this build maps onto, not one narrower entity type).
- **hypothesis** -- structural gaps, deterministically: claims under-
  evidenced (``theloom.operations.epistemic.needs_evidence``) and entities
  with zero relations (isolated). Deliberately NOT ``hypothesis-engine``'s
  own ``gaps`` section, which ranks by entity vectors -- vectors are not
  forked into a world (``theloom.store.worlds.WorldGraphStore``'s own
  docstring), so a vector-dependent gap pass run inside a fresh dream would
  see nothing. This is the "deterministic structural subset" the spec's
  determinism rule asks for, not a stub.
- **analogy** -- ``theloom.operations.analysis.cross_domain_mapping_op``
  (structural: degree + relation-type + neighbor-type profiles, no
  vectors), auto-selecting the two most populous non-finding entity types
  present as the source/target domains. Skipped with a notice when the
  graph doesn't have two such types.
- **credit** -- replays ``theloom.operations.epistemic.propagate_credit``
  for every entity whose confidence changed since the previous
  consolidation report for this graph (bi-temporal read at that report's
  ``generatedAt``, compared to now). Skipped with a notice on a graph's
  first-ever consolidation (nothing to diff against yet).

Every finding this command writes lands in the dream world's own segment
(never main -- the fork is Part 5's copy-on-write overlay: writing to an
inherited id materializes a local copy first, the parent's own graph is
never touched). Confidence is capped low and the basis is always
``inference`` or ``speculation`` (see ``DREAM_MAX_CONFIDENCE`` /
``_FINDING_BASIS``) -- dreams don't get to be confident. Provenance's
``extractor`` is ``"consolidation/<pass>"`` and every finding's ``session``
is left unset ("sessionless": this is autonomous machinery output, not a
live session's work product).

No LLM call happens anywhere in this module -- every pass above is plain
deterministic graph computation. ``articulate: true`` only tags the report
(``report["articulate"]``) as a candidate for a future harness-layer LLM
articulation pass; it does not trigger one here.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Literal

from pydantic import Field

from theloom.errors import NotFoundError, OperationError
from theloom.model import Entity, EntityCreate, EntityFilter, RelationCreate
from theloom.operations import analysis as analysis_ops
from theloom.operations import epistemic as epistemic_ops
from theloom.operations import inference as inference_ops
from theloom.operations.common import CommandInput
from theloom.operations.notices import Doc, notice, with_notices
from theloom.store import worldctx
from theloom.store.falkor import FalkorGraphStore
from theloom.store.multigraph import MultiGraph
from theloom.timeutil import iso_now

DREAM_WORLD_NAME_PREFIX = "dream-"
DREAM_WORLD_TTL_SECONDS = 7 * 24 * 3600

DEFAULT_BUDGET = 20
MAX_BUDGET = 200

#: Dreams don't get to be confident: every finding this command writes is
#: capped at this score, regardless of how strong the underlying structural
#: signal is.
DREAM_MAX_CONFIDENCE = 0.35
_FINDING_BASE_SCORE: dict[str, float] = {"tension": 0.3, "insight": 0.25, "hypothesis": 0.2}
_FINDING_BASIS: dict[str, str] = {
    "tension": "inference",
    "insight": "inference",
    "hypothesis": "speculation",
}

#: Entity types this command itself writes -- excluded from every pass's own
#: signal-gathering (an isolated hypothesis from a prior, unmerged dream must
#: never be re-flagged as its OWN structural gap, and a report/finding is
#: never a legitimate "domain" for the analogy pass).
_FINDING_ENTITY_TYPES = frozenset({"insight", "hypothesis", "tension", "consolidation_report"})

CONSOLIDATION_REPORT_DATA_PREFIX = "__consolidation_report_json:"
CONSOLIDATION_FINDING_DATA_PREFIX = "__consolidation_finding_json:"

PASS_NAMES: tuple[str, ...] = (
    "contradiction",
    "staleness",
    "motifs",
    "hypothesis",
    "analogy",
    "credit",
)
PassName = Literal["contradiction", "staleness", "motifs", "hypothesis", "analogy", "credit"]

_CREDIT_EPSILON = 0.01


# =============================================================================
# Input
# =============================================================================


class ConsolidateInput(CommandInput):
    graph: str | None = None
    # `world` (fork-from) is inherited from CommandInput: the belief world to
    # fork the dream from, omitted/"main" for the graph's live tip -- exactly
    # the same convention belief-blast-radius reuses (theloom.composites.
    # belief_blast_radius), not a second, parallel "world" concept.
    budget: int | None = Field(
        default=None,
        ge=1,
        le=MAX_BUDGET,
        description="Max findings a single pass writes (and the size of the report's "
        "topFindings). Defaults to 20.",
    )
    passes: list[PassName] | None = Field(
        default=None,
        description="Which passes to run, in any order (canonical order is always used when "
        "running them). Omitted runs all six.",
    )
    articulate: bool | None = Field(
        default=None,
        description="Marks the report as a candidate for a future LLM articulation pass. No LLM "
        "call happens here either way -- this composite is pure deterministic graph computation.",
    )


# =============================================================================
# Shared finding/report helpers (since-last-session reads these back)
# =============================================================================


def _create_finding(
    store: FalkorGraphStore,
    *,
    entity_type: str,
    name: str,
    summary: str,
    data: dict[str, Any],
    related_entity_ids: Sequence[str],
    pass_name: str,
) -> Entity:
    now = iso_now()
    score = min(DREAM_MAX_CONFIDENCE, _FINDING_BASE_SCORE.get(entity_type, 0.25))
    basis = _FINDING_BASIS.get(entity_type, "speculation")
    entity = store.create_entity(
        EntityCreate.model_validate(
            {
                "name": name,
                "entityType": entity_type,
                "observations": [
                    summary,
                    f"{CONSOLIDATION_FINDING_DATA_PREFIX}{json.dumps(data)}",
                ],
                "confidence": {"score": score, "basis": basis, "lastEvaluated": now},
                "provenance": {
                    "sourceType": "inference",
                    "sourceId": related_entity_ids[0] if related_entity_ids else None,
                    "externalRef": None,
                    "extractionDate": now,
                    "extractor": f"consolidation/{pass_name}",
                    "extractionMethod": "automated",
                },
            }
        )
    )
    seen: set[str] = set()
    for target_id in related_entity_ids:
        if target_id in seen or target_id == entity.id:
            continue
        seen.add(target_id)
        try:
            store.create_relation(
                RelationCreate.model_validate(
                    {"from": entity.id, "to": target_id, "relationType": "related_to"}
                )
            )
        except NotFoundError:
            continue  # the related entity vanished between read and write -- don't crash the pass
    return entity


def _decode_report(entity: Entity) -> Doc | None:
    for raw in entity.observations:
        if raw.startswith(CONSOLIDATION_REPORT_DATA_PREFIX):
            try:
                decoded: Doc = json.loads(raw[len(CONSOLIDATION_REPORT_DATA_PREFIX) :])
                return decoded
            except json.JSONDecodeError:
                return None
    return None


def read_report(multi: MultiGraph, world_id: str) -> Doc | None:
    """The consolidation_report entity's decoded payload for ``world_id``, or
    ``None`` if it has none (a world that isn't a dream, or one whose
    consolidate call never got as far as persisting its report). Public --
    ``theloom.composites.since_last_session`` reads dream reports back
    through this, the same decode path ``consolidate`` itself writes with."""
    store = multi.get_store(None, world=world_id)  # explicit override: works from any context
    candidates = store.list_entities(
        EntityFilter.model_validate({"entityType": "consolidation_report"})
    )
    if not candidates:
        return None
    latest = max(candidates, key=lambda e: e.created_at)
    return _decode_report(latest)


def find_reports(
    multi: MultiGraph, base_graph: str, *, exclude_world_id: str | None = None
) -> list[Doc]:
    """Every dream world's decoded report for ``base_graph``, oldest first --
    ``worldId``/``worldStatus``/``worldName``/``expiresAt`` folded on top of
    each report's own fields. Includes reaped (merged/abandoned) worlds: a
    merged dream's report is still the right boundary for the credit pass's
    "since the last consolidation" diff."""
    reports: list[Doc] = []
    for record in multi.list_worlds(include_reaped=True):
        if record["baseGraph"] != base_graph:
            continue
        if exclude_world_id is not None and record["worldId"] == exclude_world_id:
            continue
        if not str(record.get("name") or "").startswith(DREAM_WORLD_NAME_PREFIX):
            continue
        report = read_report(multi, record["worldId"])
        if report is None:
            continue
        reports.append(
            {
                **report,
                "worldId": record["worldId"],
                "worldName": record["name"],
                "worldStatus": record["status"],
                "expiresAt": record["expiresAt"],
            }
        )
    reports.sort(key=lambda r: str(r.get("generatedAt") or ""))
    return reports


def _most_recent_prior_report(
    multi: MultiGraph, base_graph: str, *, exclude_world_id: str
) -> Doc | None:
    reports = find_reports(multi, base_graph, exclude_world_id=exclude_world_id)
    return reports[-1] if reports else None


# =============================================================================
# Passes
# =============================================================================


def _pass_contradiction(base_graph: str, multi: MultiGraph, budget: int) -> Doc:
    store = multi.get_store(base_graph)
    query = epistemic_ops.EpistemicQueryInput.model_validate(
        {"graph": base_graph, "includeAllStatuses": True}
    )
    before = epistemic_ops.contested_claims(query, multi)
    before_ids = {row["entity"]["id"] for row in before["items"]}

    inference_ops.run_inference(
        inference_ops.RunInferenceInput.model_validate({"graph": base_graph}), multi
    )

    after = epistemic_ops.contested_claims(query, multi)

    findings: list[Doc] = []
    for row in after["items"][:budget]:
        entity = row["entity"]
        transitive = entity["id"] not in before_ids
        tension = _create_finding(
            store,
            entity_type="tension",
            name=f"Tension: {entity['name']}",
            summary=(
                f"'{entity['name']}' has {row['supportCount']} supporting and "
                f"{row['contradictCount']} contradicting relation(s)"
                + (
                    " -- surfaced only after running inference (transitive)."
                    if transitive
                    else " (direct)."
                )
            ),
            data={
                "kind": "contradiction",
                "transitive": transitive,
                "targetEntityId": entity["id"],
                "targetEntityName": entity["name"],
                "supportCount": row["supportCount"],
                "contradictCount": row["contradictCount"],
            },
            related_entity_ids=[entity["id"]],
            pass_name="contradiction",
        )
        findings.append(
            {
                "entityId": tension.id,
                "entityName": tension.name,
                "targetEntityId": entity["id"],
                "targetEntityName": entity["name"],
                "transitive": transitive,
            }
        )

    after_ids = {row["entity"]["id"] for row in after["items"]}
    return {
        "ran": True,
        "count": len(findings),
        "findings": findings,
        "directCount": len(before_ids),
        "transitiveCount": len(after_ids - before_ids),
    }


def _pass_staleness(base_graph: str, multi: MultiGraph, budget: int) -> Doc:
    store = multi.get_store(base_graph)
    result = epistemic_ops.stale_beliefs(
        epistemic_ops.StaleBeliefsInput.model_validate(
            {"graph": base_graph, "includeAllStatuses": True}
        ),
        multi,
    )
    items = result["items"]
    if not items:
        return {"ran": True, "count": 0, "findings": [], "staleCount": 0}

    top = items[:budget]
    insight = _create_finding(
        store,
        entity_type="insight",
        name=f"Staleness sweep: {len(items)} stale belief(s)",
        summary=(
            f"{len(items)} entities have not been evaluated recently (oldest: "
            f"'{top[0]['entity']['name']}', {top[0]['daysSinceEvaluation']} days)."
        ),
        data={
            "kind": "staleness",
            "staleCount": len(items),
            "sample": [
                {
                    "entityId": r["entity"]["id"],
                    "entityName": r["entity"]["name"],
                    "daysSinceEvaluation": r["daysSinceEvaluation"],
                }
                for r in top
            ],
        },
        related_entity_ids=[r["entity"]["id"] for r in top],
        pass_name="staleness",
    )
    return {
        "ran": True,
        "count": 1,
        "staleCount": len(items),
        "findings": [
            {"entityId": insight.id, "entityName": insight.name, "staleCount": len(items)}
        ],
    }


def _pass_motifs(base_graph: str, multi: MultiGraph, budget: int) -> Doc:
    store = multi.get_store(base_graph)
    result = analysis_ops.find_frequent_subgraphs_op(
        analysis_ops.FindFrequentSubgraphsInput.model_validate(
            {"graph": base_graph, "maxInstances": budget}
        ),
        multi,
    )
    motifs = result["motifs"]
    if not motifs:
        return {"ran": True, "count": 0, "findings": [], "motifCount": 0}

    top = motifs[: min(budget, 5)]
    insight = _create_finding(
        store,
        entity_type="insight",
        name=f"Motif mining: {len(motifs)} recurring pattern(s)",
        summary=(
            f"{len(motifs)} recurring structural motif(s) found; most frequent: "
            f"'{top[0]['patternDescription']}' ({top[0]['occurrenceCount']} occurrences)."
        ),
        data={
            "kind": "motifs",
            "motifCount": len(motifs),
            "top": [
                {
                    "patternId": m["patternId"],
                    "description": m["patternDescription"],
                    "occurrenceCount": m["occurrenceCount"],
                }
                for m in top
            ],
        },
        related_entity_ids=[],
        pass_name="motifs",
    )
    return {
        "ran": True,
        "count": 1,
        "motifCount": len(motifs),
        "findings": [
            {"entityId": insight.id, "entityName": insight.name, "motifCount": len(motifs)}
        ],
    }


def _isolated_entities(store: FalkorGraphStore) -> list[Entity]:
    relations = store.list_relations()
    touched = {r.from_ for r in relations} | {r.to for r in relations}
    return [
        e
        for e in store.list_entities()
        if e.id not in touched and e.entity_type.value not in _FINDING_ENTITY_TYPES
    ]


def _pass_hypothesis(base_graph: str, multi: MultiGraph, budget: int) -> Doc:
    store = multi.get_store(base_graph)
    evidence_gaps = epistemic_ops.needs_evidence(
        epistemic_ops.NeedsEvidenceInput.model_validate(
            {"graph": base_graph, "includeAllStatuses": True}
        ),
        multi,
    )["items"]
    isolated = _isolated_entities(store)

    findings: list[Doc] = []
    for row in evidence_gaps[:budget]:
        entity = row["entity"]
        hyp = _create_finding(
            store,
            entity_type="hypothesis",
            name=f"Hypothesis: why is '{entity['name']}' under-evidenced?",
            summary=(
                f"Claim '{entity['name']}' has {row['supportCount']} supporting relation(s); "
                "investigate whether more evidence exists or the claim should be revised."
            ),
            data={
                "kind": "structural_gap",
                "gapType": "under_evidenced",
                "targetEntityId": entity["id"],
                "targetEntityName": entity["name"],
                "supportCount": row["supportCount"],
            },
            related_entity_ids=[entity["id"]],
            pass_name="hypothesis",
        )
        findings.append(
            {
                "entityId": hyp.id,
                "entityName": hyp.name,
                "targetEntityId": entity["id"],
                "gapType": "under_evidenced",
            }
        )

    remaining = max(budget - len(findings), 0)
    for entity in isolated[:remaining]:
        hyp = _create_finding(
            store,
            entity_type="hypothesis",
            name=f"Hypothesis: how does '{entity.name}' connect to the graph?",
            summary=(
                f"'{entity.name}' has zero relations; investigate whether it should be linked "
                "to the rest of the graph or is genuinely isolated."
            ),
            data={
                "kind": "structural_gap",
                "gapType": "isolated",
                "targetEntityId": entity.id,
                "targetEntityName": entity.name,
            },
            related_entity_ids=[entity.id],
            pass_name="hypothesis",
        )
        findings.append(
            {
                "entityId": hyp.id,
                "entityName": hyp.name,
                "targetEntityId": entity.id,
                "gapType": "isolated",
            }
        )

    return {
        "ran": True,
        "count": len(findings),
        "findings": findings,
        "underEvidencedCount": len(evidence_gaps),
        "isolatedCount": len(isolated),
    }


def _skipped(reason: str) -> Doc:
    return {"ran": False, "skipped": True, "skipReason": reason, "count": 0, "findings": []}


def _pass_analogy(base_graph: str, multi: MultiGraph, budget: int) -> Doc:
    store = multi.get_store(base_graph)
    counts: dict[str, int] = {}
    for e in store.list_entities():
        if e.entity_type.value in _FINDING_ENTITY_TYPES:
            continue
        counts[e.entity_type.value] = counts.get(e.entity_type.value, 0) + 1
    eligible = sorted((t for t, n in counts.items() if n >= 2), key=lambda t: -counts[t])
    if len(eligible) < 2:
        return _skipped(
            "fewer than two distinct entity types with >= 2 entities each -- nothing to compare "
            "across domains"
        )
    source_type, target_type = eligible[0], eligible[1]

    try:
        result = analysis_ops.cross_domain_mapping_op(
            analysis_ops.CrossDomainMappingInput.model_validate(
                {
                    "graph": base_graph,
                    "sourceDomain": {"entityType": source_type},
                    "targetDomain": {"entityType": target_type},
                }
            ),
            multi,
        )
    except OperationError as exc:
        return _skipped(str(exc))

    mappings = result.get("mappings", [])
    if not mappings:
        return {
            "ran": True,
            "count": 0,
            "findings": [],
            "sourceType": source_type,
            "targetType": target_type,
        }

    findings: list[Doc] = []
    for m in mappings[:budget]:
        insight = _create_finding(
            store,
            entity_type="insight",
            name=f"Analogy: '{m['sourceName']}' <-> '{m['targetName']}'",
            summary=(
                f"Structural analogy between '{m['sourceName']}' ({source_type}) and "
                f"'{m['targetName']}' ({target_type}) -- similarity {m['similarity']:.2f}."
            ),
            data={
                "kind": "analogy",
                "sourceType": source_type,
                "targetType": target_type,
                "sourceId": m["sourceId"],
                "targetId": m["targetId"],
                "similarity": m["similarity"],
            },
            related_entity_ids=[m["sourceId"], m["targetId"]],
            pass_name="analogy",
        )
        findings.append(
            {
                "entityId": insight.id,
                "entityName": insight.name,
                "sourceId": m["sourceId"],
                "targetId": m["targetId"],
                "similarity": m["similarity"],
            }
        )
    return {
        "ran": True,
        "count": len(findings),
        "findings": findings,
        "sourceType": source_type,
        "targetType": target_type,
    }


def _confidence_changed_since(store: FalkorGraphStore, since: str) -> list[Doc]:
    changed: list[Doc] = []
    for entity in store.list_entities():
        if entity.confidence is None:
            continue
        previous = store.read_entity_as_of(entity.id, since)
        if previous is None or previous.confidence is None:
            continue
        delta = entity.confidence.score - previous.confidence.score
        if abs(delta) >= _CREDIT_EPSILON:
            changed.append({"entityId": entity.id, "entityName": entity.name, "delta": delta})
    changed.sort(key=lambda r: -abs(float(r["delta"])))
    return changed


def _pass_credit(base_graph: str, multi: MultiGraph, budget: int, world_id: str) -> Doc:
    prior = _most_recent_prior_report(multi, base_graph, exclude_world_id=world_id)
    if prior is None:
        return _skipped(
            "no prior consolidation report found for this graph -- nothing to replay since"
        )
    since = str(prior["generatedAt"])
    store = multi.get_store(base_graph)
    changed = _confidence_changed_since(store, since)
    if not changed:
        return {"ran": True, "count": 0, "findings": [], "since": since, "changedCount": 0}

    findings: list[Doc] = []
    replayed: list[Doc] = []
    for item in changed[:budget]:
        trigger_id, trigger_name, delta = item["entityId"], item["entityName"], float(item["delta"])
        propagation = epistemic_ops.propagate_credit(
            epistemic_ops.PropagateCreditInput.model_validate(
                {"graph": base_graph, "entityIds": [trigger_id], "delta": delta, "dryRun": False}
            ),
            multi,
        )
        outcome = propagation["items"][0]
        affected = int(outcome["totalEntitiesAffected"])
        replayed.append(
            {
                "triggerId": trigger_id,
                "triggerName": trigger_name,
                "delta": delta,
                "affected": affected,
            }
        )
        if affected > 0:
            changes = outcome["changes"][:5]
            insight = _create_finding(
                store,
                entity_type="insight",
                name=f"Credit replay: '{trigger_name}' rippled to {affected} entit(y/ies)",
                summary=(
                    f"'{trigger_name}' confidence changed by {delta:+.3f} since the last "
                    f"consolidation; replaying credit propagation shifted {affected} downstream "
                    "entit(y/ies)."
                ),
                data={
                    "kind": "credit_replay",
                    "triggerId": trigger_id,
                    "triggerName": trigger_name,
                    "delta": delta,
                    "affected": affected,
                    "changes": changes,
                },
                related_entity_ids=[trigger_id, *(c["entityId"] for c in changes)],
                pass_name="credit",
            )
            findings.append(
                {
                    "entityId": insight.id,
                    "entityName": insight.name,
                    "triggerId": trigger_id,
                    "affected": affected,
                }
            )

    return {
        "ran": True,
        "count": len(findings),
        "findings": findings,
        "since": since,
        "changedCount": len(changed),
        "replayed": replayed,
    }


# =============================================================================
# Report assembly + persistence
# =============================================================================

# Fixed, deterministic priority (not a fabricated numeric score): a
# contradiction or a structural-gap hypothesis is directly actionable by a
# waking session; an insight is context. Each pass's own findings keep their
# internal order (already ranked by the underlying operation).
_FINDING_PRIORITY: dict[str, int] = {
    "contradiction": 0,
    "hypothesis": 1,
    "credit": 2,
    "analogy": 3,
    "staleness": 4,
    "motifs": 5,
}


def _build_report(
    *,
    base_graph: str,
    world_id: str,
    parent_label: str,
    pass_results: dict[str, Doc],
    budget: int,
    articulate: bool,
) -> Doc:
    counts = {name: result.get("count", 0) for name, result in pass_results.items()}
    top_findings: list[Doc] = []
    for pass_name, result in pass_results.items():
        for finding in result.get("findings", []):
            top_findings.append({"pass": pass_name, **finding})
    top_findings.sort(key=lambda f: _FINDING_PRIORITY.get(str(f["pass"]), 9))
    top_findings = top_findings[: max(budget, 1)]

    passes_summary: dict[str, Doc] = {}
    for name, result in pass_results.items():
        summary = {k: v for k, v in result.items() if k != "findings"}
        summary["findingCount"] = result.get("count", 0)
        passes_summary[name] = summary

    return {
        "graph": base_graph,
        "worldId": world_id,
        "parentWorld": parent_label,
        "generatedAt": iso_now(),
        "budget": budget,
        "articulate": articulate,
        "passes": passes_summary,
        "counts": counts,
        "totalFindings": sum(counts.values()),
        "topFindings": top_findings,
        "diffWorldsHandle": {"a": parent_label, "b": world_id},
    }


def _persist_report(store: FalkorGraphStore, report: Doc) -> Entity:
    now = iso_now()
    return store.create_entity(
        EntityCreate.model_validate(
            {
                "name": f"Consolidation report: {report['graph']} @ {report['generatedAt']}",
                "entityType": "consolidation_report",
                "observations": [
                    f"Consolidation report for '{report['graph']}': {report['totalFindings']} "
                    f"finding(s) across {len(report['passes'])} pass(es).",
                    f"{CONSOLIDATION_REPORT_DATA_PREFIX}{json.dumps(report)}",
                ],
                "confidence": {
                    "score": DREAM_MAX_CONFIDENCE,
                    "basis": "inference",
                    "lastEvaluated": now,
                },
                "provenance": {
                    "sourceType": "inference",
                    "sourceId": None,
                    "externalRef": None,
                    "extractionDate": now,
                    "extractor": "consolidation/report",
                    "extractionMethod": "automated",
                },
            }
        )
    )


# =============================================================================
# consolidate
# =============================================================================

_PASS_RUNNERS: dict[str, Any] = {
    "contradiction": _pass_contradiction,
    "staleness": _pass_staleness,
    "motifs": _pass_motifs,
    "hypothesis": _pass_hypothesis,
    "analogy": _pass_analogy,
    # "credit" takes an extra arg (world_id) -- dispatched separately below.
}


def _clamp_budget(budget: int | None) -> int:
    if budget is None:
        return DEFAULT_BUDGET
    return max(1, min(budget, MAX_BUDGET))


def _resolve_passes(passes: Sequence[str] | None) -> list[str]:
    if passes is None:
        return list(PASS_NAMES)
    selected = set(passes)
    return [p for p in PASS_NAMES if p in selected]


def consolidate(params: ConsolidateInput, multi: MultiGraph) -> Doc:
    budget = _clamp_budget(params.budget)
    passes = _resolve_passes(params.passes)
    parent_label = params.world or "main"
    articulate = bool(params.articulate)

    dream_name = f"{DREAM_WORLD_NAME_PREFIX}{iso_now()[:10]}"
    fork = multi.fork_world(
        name=dream_name,
        graph=params.graph,
        from_world=params.world,
        as_of=None,
        ttl_seconds=DREAM_WORLD_TTL_SECONDS,
    )
    world_id = str(fork["worldId"])
    base_graph = str(fork["baseGraph"])

    pass_results: dict[str, Doc] = {}
    notices: list[Doc] = []
    with worldctx.active(world_id):
        for pass_name in passes:
            if pass_name == "credit":
                outcome = _pass_credit(base_graph, multi, budget, world_id)
            else:
                outcome = _PASS_RUNNERS[pass_name](base_graph, multi, budget)
            pass_results[pass_name] = outcome
            if outcome.get("skipped"):
                notices.append(
                    notice(
                        "CONSOLIDATION_PASS_SKIPPED",
                        f"Pass '{pass_name}' skipped: {outcome['skipReason']}",
                    )
                )

        report = _build_report(
            base_graph=base_graph,
            world_id=world_id,
            parent_label=parent_label,
            pass_results=pass_results,
            budget=budget,
            articulate=articulate,
        )
        report_entity = _persist_report(multi.get_store(base_graph), report)

    return with_notices(
        {
            "worldId": world_id,
            "dreamWorld": dream_name,
            "graph": base_graph,
            "parentWorld": parent_label,
            "expiresAt": fork["expiresAt"],
            "reportEntityId": report_entity.id,
            "report": report,
            "diffWorldsHandle": {"a": parent_label, "b": world_id},
        },
        notices,
        applied=True,
    )
