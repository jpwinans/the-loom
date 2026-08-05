"""CEGIS (Counterexample-Guided Inductive Synthesis) over graph structures.

Implements ``quickVerify`` plus the synth/verify/refine loop and the
``cegis-synthesize`` operation wiring (spec, commit).

The loop is ``synthesize -> quickVerify -> verify -> refine``:

  1. iterations >= maxIterations           -> reason 'maxIterations'
  2. elapsed >= timeoutMs                   -> reason 'timeout'
  3. candidate synthesis fails              -> reason 'unrealizable'
  4. candidate matches a known counterexample (O(n) structural quick-verify)
     -> record it, advance, continue
  5. full verify passes                     -> reason 'success' (iterations + 1)
     else extract a counterexample, advance, continue

Verification is lightweight: instead of committing the candidate to a temporary
graph store and running the tiered Verifier, we evaluate the same guards
(``theloom.verification.checks``) and property predicates directly on in-memory
candidate docs. No FalkorDB is touched to verify; the store is only
resolved when ``commit`` persists a successful candidate. Timing uses
``time.perf_counter`` and the seeds are explicit (never a wall-clock seed), so a
given input is reproducible even though the process is non-deterministic by design
across differing seeds.
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import Field

from theloom.operations.common import CommandInput
from theloom.operations.verification import PropertyDefinition
from theloom.synthesis.generator import (
    GenerationResult,
    GenerationSpec,
    TypeCompatibilityGraph,
    TypeConstrainedGenerator,
)
from theloom.verification import checks

if TYPE_CHECKING:
    from theloom.store.multigraph import MultiGraph

Doc = dict[str, Any]

_BASE_SEED = 42
_DEFAULT_MAX_ITERATIONS = 10
_DEFAULT_TIMEOUT_MS = 30000

# Seed spreading constants for the synthesize loop.
_ITER_STRIDE = 7919
_CE_STRIDE = 104729

# The Verifier's default Tier-1 guards (consistency checks).
_ENTITY_GUARDS = ("confidenceBounds", "entityType", "observationsRequired")
_RELATION_GUARDS = ("causalPolarity", "nonCausalPolarity", "noSelfLoop")


# =============================================================================
# Input model
# =============================================================================


class CegisSynthesizeInput(CommandInput):
    """Input for the cegis-synthesize command."""

    properties: list[PropertyDefinition]
    max_entities: Annotated[int, Field(gt=0, le=10000)] = Field(alias="maxEntities")
    max_relations: Annotated[int, Field(ge=0, le=50000)] = Field(alias="maxRelations")
    max_iterations: Annotated[int, Field(gt=0)] = Field(default=10, alias="maxIterations")
    timeout_ms: Annotated[int, Field(gt=0)] = Field(default=30000, alias="timeoutMs")
    commit: bool = False
    graph: str | None = None


# =============================================================================
# Property predicates (mirror validate_spec's node/edge predicates, typed for
# mypy --strict since the operations.verification helpers are intentionally
# untyped and cannot be called from a strict module).
# =============================================================================


def _node_predicate(field_name: str | None, condition: str | None, value: Any) -> Any:
    if not field_name or not condition:
        return lambda _e: True

    def predicate(entity: Doc) -> bool:
        val = entity.get(field_name)
        if condition == "notEmpty":
            if isinstance(val, list | str):
                return len(val) > 0
            return val is not None
        if condition == "exists":
            return val is not None
        if condition == "equals":
            return bool(val == value)
        return True

    return predicate


def _edge_predicate(field_name: str | None, condition: str | None, value: Any) -> Any:
    if not field_name or not condition:
        return lambda _r: True

    def predicate(relation: Doc) -> bool:
        val = relation.get(field_name)
        if condition == "notEmpty":
            if isinstance(val, str):
                return len(val) > 0
            return val is not None
        if condition == "exists":
            return val is not None
        if condition == "equals":
            return bool(val == value)
        return True

    return predicate


# =============================================================================
# In-memory verification of a candidate against the property spec
# =============================================================================


def _candidate_docs(candidate: GenerationResult) -> tuple[list[Doc], list[Doc]]:
    """Build entity/relation wire docs from a candidate (synthetic ids).

    Relations are dropped when they would be self-loops: the commit-to-temp-store
    path skips any relation where ``fromId == toId``.
    """
    entities: list[Doc] = [
        {
            "id": f"e{i}",
            "name": e.name,
            "entityType": e.entity_type,
            "observations": list(e.observations),
        }
        for i, e in enumerate(candidate.entities)
    ]
    relations: list[Doc] = []
    for k, r in enumerate(candidate.relations):
        if not (0 <= r.from_index < len(entities) and 0 <= r.to_index < len(entities)):
            continue
        from_id = entities[r.from_index]["id"]
        to_id = entities[r.to_index]["id"]
        if from_id == to_id:
            continue
        relations.append(
            {
                "id": f"r{k}",
                "from": from_id,
                "to": to_id,
                "relationType": r.relation_type,
                "polarity": r.polarity,
                "strength": r.strength,
                "evidence": None,
            }
        )
    return entities, relations


def _eval_property(
    definition: PropertyDefinition, entities: list[Doc], relations: list[Doc]
) -> list[tuple[str, bool, list[Doc]]]:
    """Evaluate one property definition -> list of (level, pass, violations)."""
    results: list[tuple[str, bool, list[Doc]]] = []

    if definition.type == "default":
        for name in checks.DEFAULT_INVARIANT_NAMES:
            r = checks.BUILTIN_INVARIANTS[name](entities, relations, None)
            results.append((r["level"], r["pass"], r["violations"]))
    elif definition.type == "invariant":
        inv = checks.BUILTIN_INVARIANTS.get(definition.invariant_name or "")
        if inv is not None:
            r = inv(entities, relations, None)
            level = definition.level or r["level"]
            results.append((level, r["pass"], r["violations"]))
    elif definition.type == "forAllNodes":
        predicate = _node_predicate(definition.field, definition.condition, definition.value)
        violations = [
            {
                "elementId": e["id"],
                "message": f"Node '{e['name']}' ({e['id']}) violated property '{definition.name}'",
            }
            for e in entities
            if not predicate(e)
        ]
        results.append(("node", len(violations) == 0, violations))
    elif definition.type == "forAllEdges":
        predicate = _edge_predicate(definition.field, definition.condition, definition.value)
        violations = [
            {
                "elementId": r["id"],
                "message": (
                    f"Edge '{r['id']}' ({r['from']} -> {r['to']}) "
                    f"violated property '{definition.name}'"
                ),
            }
            for r in relations
            if not predicate(r)
        ]
        results.append(("edge", len(violations) == 0, violations))

    return results


def verify_candidate(
    candidate: GenerationResult, properties: list[PropertyDefinition]
) -> tuple[bool, list[Doc]]:
    """Run Tier-1 guards + property predicates on the candidate (fail-fast).

    Returns ``(pass, violations)`` where each violation is ``{elementId, message}``.
    """
    entities, relations = _candidate_docs(candidate)

    guard_violations: list[Doc] = []
    has_error = False
    for entity in entities:
        for name in _ENTITY_GUARDS:
            for v in checks.ENTITY_GUARDS[name](entity):
                if v["severity"] == "error":
                    has_error = True
                guard_violations.append({"elementId": entity["id"], "message": v["message"]})
    for relation in relations:
        for name in _RELATION_GUARDS:
            for v in checks.RELATION_GUARDS[name](relation):
                if v["severity"] == "error":
                    has_error = True
                guard_violations.append(
                    {
                        "elementId": f"{relation['from']}->{relation['to']}",
                        "message": v["message"],
                    }
                )

    tier1: list[tuple[str, bool, list[Doc]]] = []
    tier2: list[tuple[str, bool, list[Doc]]] = []
    for definition in properties:
        for level, passed, violations in _eval_property(definition, entities, relations):
            (tier1 if level in ("node", "edge") else tier2).append((level, passed, violations))

    collected: list[Doc] = list(guard_violations)
    for _level, _passed, violations in tier1:
        collected.extend(violations)

    tier1_consistent = not has_error and all(passed for _l, passed, _v in tier1)
    if not tier1_consistent:
        return False, collected

    for _level, _passed, violations in tier2:
        collected.extend(violations)
    tier2_pass = all(passed for _l, passed, _v in tier2)
    return tier2_pass, collected


# =============================================================================
# quickVerify — O(n) rejection against known counterexamples
# =============================================================================


def _matches_counterexample(
    counterexample: Doc,
    candidate_entity_types: set[str],
    candidate_relation_types: set[str],
) -> bool:
    missing_entity_types = counterexample.get("missingEntityTypes")
    if missing_entity_types and candidate_entity_types == set(missing_entity_types):
        return True

    missing_relation_types = counterexample.get("missingRelationTypes")
    if missing_relation_types and candidate_relation_types == set(missing_relation_types):
        return True

    # Structured data present but not matched -> no regex fallback.
    if (
        counterexample.get("missingEntityTypes") is not None
        or counterexample.get("missingRelationTypes") is not None
    ):
        return False

    # Fallback: parse quoted names out of the violation messages.
    for violation in counterexample["violations"]:
        msg = violation["message"]
        quoted = re.findall(r"['\"](\w+)['\"]", msg)
        if not quoted:
            continue
        msg_lower = msg.lower()
        mentions_entity = "entity type" in msg_lower or (
            "entity" in msg_lower and "missing" in msg_lower
        )
        if mentions_entity and any(name not in candidate_entity_types for name in quoted):
            return True
        mentions_relation = "relation type" in msg_lower or (
            "relation" in msg_lower and "missing" in msg_lower
        )
        if mentions_relation and any(name not in candidate_relation_types for name in quoted):
            return True

    return False


def quick_verify(candidate: GenerationResult, counterexamples: list[Doc]) -> Doc | None:
    """Return the first known counterexample the candidate would re-trigger."""
    if not counterexamples:
        return None
    candidate_entity_types = {e.entity_type for e in candidate.entities}
    candidate_relation_types = {r.relation_type for r in candidate.relations}
    for ce in counterexamples:
        if _matches_counterexample(ce, candidate_entity_types, candidate_relation_types):
            return ce
    return None


def _extract_counterexample(
    candidate: GenerationResult, violations: list[Doc], iteration: int
) -> Doc:
    summary = "; ".join(v["message"] for v in violations[:3])
    suffix = f" (+{len(violations) - 3} more)" if len(violations) > 3 else ""
    entity_types = list(dict.fromkeys(e.entity_type for e in candidate.entities))
    relation_types = list(dict.fromkeys(r.relation_type for r in candidate.relations))
    return {
        "iteration": iteration,
        "violations": violations,
        "description": f"Iteration {iteration}: {summary}{suffix}",
        "missingEntityTypes": entity_types,
        "missingRelationTypes": relation_types,
    }


# =============================================================================
# Candidate serialization
# =============================================================================


def _candidate_to_dict(candidate: GenerationResult) -> Doc:
    return {
        "success": candidate.success,
        "entities": [
            {
                "name": e.name,
                "entityType": e.entity_type,
                "observations": list(e.observations),
            }
            for e in candidate.entities
        ],
        "relations": [
            {
                "fromIndex": r.from_index,
                "toIndex": r.to_index,
                "relationType": r.relation_type,
                "polarity": r.polarity,
                "strength": r.strength,
            }
            for r in candidate.relations
        ],
    }


# =============================================================================
# The CEGIS loop
# =============================================================================


def run_cegis(params: CegisSynthesizeInput) -> Doc:
    """Run the CEGIS loop and return the result dict (no store / no commit)."""
    generator = TypeConstrainedGenerator(TypeCompatibilityGraph.create_default())
    gen_spec = GenerationSpec(max_entities=params.max_entities, max_relations=params.max_relations)
    max_iterations = params.max_iterations
    timeout_ms = params.timeout_ms

    counterexamples: list[Doc] = []
    start = time.perf_counter()
    iterations = 0

    def elapsed_ms() -> float:
        return (time.perf_counter() - start) * 1000

    while True:
        if iterations >= max_iterations:
            return _terminal("maxIterations", iterations, counterexamples, elapsed_ms())
        if elapsed_ms() >= timeout_ms:
            return _terminal("timeout", iterations, counterexamples, elapsed_ms())

        adjusted_seed = _BASE_SEED + iterations * _ITER_STRIDE + len(counterexamples) * _CE_STRIDE
        candidate = generator.generate(gen_spec, adjusted_seed)

        if not candidate.success:
            return _terminal("unrealizable", iterations, counterexamples, elapsed_ms())

        quick_match = quick_verify(candidate, counterexamples)
        if quick_match is not None:
            counterexamples.append(
                {
                    "iteration": iterations,
                    "violations": quick_match["violations"],
                    "description": f"Quick-verify match from iteration {quick_match['iteration']}",
                }
            )
            iterations += 1
            continue

        passed, violations = verify_candidate(candidate, params.properties)
        if passed:
            return {
                "success": True,
                "reason": "success",
                "iterations": iterations + 1,
                "counterexamples": counterexamples,
                "durationMs": elapsed_ms(),
                "candidate": _candidate_to_dict(candidate),
            }

        counterexamples.append(_extract_counterexample(candidate, violations, iterations))
        iterations += 1


def _terminal(reason: str, iterations: int, counterexamples: list[Doc], duration_ms: float) -> Doc:
    return {
        "success": False,
        "reason": reason,
        "iterations": iterations,
        "counterexamples": counterexamples,
        "durationMs": duration_ms,
    }


# =============================================================================
# Operation entry point
# =============================================================================


def cegis_synthesize(params: CegisSynthesizeInput, multi: MultiGraph) -> Doc:
    """cegis-synthesize op: run the loop, optionally committing a success."""
    result = run_cegis(params)

    if params.commit and result.get("success") and result.get("candidate") is not None:
        store = multi.get_store(params.graph)
        candidate = result["candidate"]
        entity_ids, skipped = _commit_generated_graph(
            candidate["entities"], candidate["relations"], store
        )
        result = {**result, "committedEntityIds": entity_ids, "skippedRelations": skipped}

    return result


def _commit_generated_graph(
    entities: list[Doc], relations: list[Doc], store: Any
) -> tuple[list[str], int]:
    """Persist a successful candidate."""
    from theloom.model import EntityCreate, RelationCreate

    entity_ids: list[str] = []
    for entity in entities:
        created = store.create_entity(
            EntityCreate.model_validate(
                {
                    "name": entity["name"],
                    "entityType": entity["entityType"],
                    "observations": entity["observations"],
                }
            )
        )
        entity_ids.append(created.id)

    skipped = 0
    for relation in relations:
        from_index = relation["fromIndex"]
        to_index = relation["toIndex"]
        if not (0 <= from_index < len(entity_ids) and 0 <= to_index < len(entity_ids)):
            continue
        from_id = entity_ids[from_index]
        to_id = entity_ids[to_index]
        if from_id == to_id:
            continue
        try:
            store.create_relation(
                RelationCreate.model_validate(
                    {
                        "from": from_id,
                        "to": to_id,
                        "relationType": relation["relationType"],
                        "polarity": relation["polarity"],
                        "strength": relation["strength"],
                        "evidence": "Auto-generated by CEGIS synthesis",
                    }
                )
            )
        except Exception:  # noqa: BLE001 -- count skipped relations, keep going
            skipped += 1

    return entity_ids, skipped
