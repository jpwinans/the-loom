"""Field descriptions as tested contract (desire 7): a registry-walking test
asserting every documented default in a command's input schema matches the
code path that actually resolves it.

Why this is its own layer, distinct from ``COMMANDS.md``: a field's
*mechanical* default (``Field(default=X, ...)``) is generated straight from
the Pydantic model into the JSON Schema, so it can never drift from the
model by construction -- ``theloom.cli.schema.field_rows`` reads it, not a
human. The risk desire 7 is grounded in is a *semantic* default layered on
top in prose: several fields are wire-optional (``bool | None = None``) but
the handler resolves the omitted value to a concrete default itself (e.g.
``params.persist or False``, ``options.get("dryRun", False)``), and the
Field description states that resolved value in English ("Defaults to
false", "(default 500)") because the JSON Schema's own ``default: null``
would be actively misleading here. Nothing mechanically keeps that sentence
in sync with the handler once written -- exactly the shape of the surface
bug this desire is grounded in (an integration arbiter finding "the surface
promised what the code didn't do").

The mechanism mirrors ``notices-catalog`` (desire 3): ``_documented_default_claims``
walks every command's schema (via ``field_rows``, the same walk
``COMMANDS.md`` uses) and regex-extracts a claim wherever a description uses
one of three established phrasings (see ``_parse_claim``). ``DEFAULT_VERIFIERS``
is a small hand-written registry mapping each currently-known claim to a
function that actually checks it against the running code -- behaviorally,
by calling the real handler with the field omitted and again with the field
set to the claimed value and comparing, or (where behavioral setup would be
disproportionate, e.g. manufacturing 501 raw events to observe a paging
cutoff) by reading the literal constant the handler resolves against.
``test_every_documented_default_has_a_verifier`` is the forcing function:
a new prose claim with no matching verifier entry fails the suite, the same
way an uncataloged notice code does -- it cannot go live silently.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest

from theloom.cli.registry import COMMANDS
from theloom.cli.schema import field_rows
from theloom.operations import receipts as receipts_ops
from theloom.operations.algebra import SemiringDistancesInput, semiring_distances
from theloom.operations.analysis import DetectLoopsInput, detect_loops
from theloom.operations.entity import CreateEntityInput, create_entity
from theloom.operations.epistemic import PropagateCreditInput, propagate_credit
from theloom.operations.inference import (
    InferenceRuleCreateInput,
    RunInferenceInput,
    inference_rule_create,
    run_inference,
)
from theloom.operations.relations import (
    CreateRelationInput,
    CreateRelationsInput,
    ListRelationsInput,
    create_relation,
    create_relations,
    list_relations,
)
from theloom.operations.worlds import (
    AbandonWorldInput,
    ForkWorldInput,
    ListWorldsInput,
    abandon_world,
    fork_world,
    list_worlds,
)
from theloom.store.multigraph import MultiGraph

# =============================================================================
# Extraction: registry-walk every schema, regex-parse claims out of prose
# =============================================================================


@dataclass(frozen=True)
class _Claim:
    value: bool | int | str


_DEFAULTS_TO_RE = re.compile(r"\bDefaults to (true|false|\d+)\b")
_PAREN_DEFAULT_RE = re.compile(r"\(default:? (true|false|\d+)\)")
_QUOTED_THEN_MARKER_RE = re.compile(r"'([^']+)'[^']*the default when omitted")
_FALLBACK_FIELD_RE = re.compile(r"^Default \S.*for any item that omits its own `(\w+)`")


def _coerce(token: str) -> bool | int:
    if token == "true":
        return True
    if token == "false":
        return False
    return int(token)


def _parse_claim(description: str) -> _Claim | None:
    """The three phrasings this repo currently uses to state a semantic
    default in prose. Not general NLP -- a deliberately narrow, documented
    convention (mirroring how ``field_rows`` reads a *mechanical* default
    from one fixed JSON-Schema key rather than free text). A future
    phrasing that doesn't match one of these simply isn't extracted, which
    is a gap in the extractor, not a false pass -- widen it here if that
    happens."""
    m = _DEFAULTS_TO_RE.search(description)
    if m:
        return _Claim(_coerce(m.group(1)))
    m = _PAREN_DEFAULT_RE.search(description)
    if m:
        return _Claim(_coerce(m.group(1)))
    m = _QUOTED_THEN_MARKER_RE.search(description)
    if m:
        return _Claim(m.group(1))
    m = _FALLBACK_FIELD_RE.search(description)
    if m:
        return _Claim(f"falls back to `{m.group(1)}`")
    return None


def _documented_default_claims() -> dict[tuple[str, str], _Claim]:
    claims: dict[tuple[str, str], _Claim] = {}
    for descriptor in COMMANDS:
        for row in field_rows(descriptor.input_model):
            if not row.description:
                continue
            claim = _parse_claim(row.description)
            if claim is not None:
                claims[(descriptor.name, row.path)] = claim
    return claims


# =============================================================================
# Verifiers: one per currently-known claim, checked against the real code
# =============================================================================


def _concept(multi: MultiGraph, name: str) -> str:
    result = create_entity(
        CreateEntityInput.model_validate(
            {"name": name, "entityType": "concept", "observations": [name]}
        ),
        multi,
    )
    return str(result["id"])


def _causes(multi: MultiGraph, from_id: str, to_id: str) -> None:
    create_relation(
        CreateRelationInput.model_validate(
            {
                "from": from_id,
                "to": to_id,
                "relationType": "causes",
                "polarity": "+",
                "strength": "moderate",
                "evidence": "test fixture",
            }
        ),
        multi,
    )


def _verify_detect_loops_persist(multi: MultiGraph) -> None:
    a, b = _concept(multi, "DDA"), _concept(multi, "DDB")
    _causes(multi, a, b)
    _causes(multi, b, a)
    omitted = detect_loops(DetectLoopsInput(graph=multi.default_graph), multi)
    explicit = detect_loops(DetectLoopsInput(graph=multi.default_graph, persist=False), multi)
    assert omitted == explicit


def _claim_entity_pair(multi: MultiGraph, suffix: str) -> tuple[str, str]:
    conf = {"score": 0.5, "basis": "direct_observation"}
    trigger = create_entity(
        CreateEntityInput.model_validate(
            {
                "name": f"Trigger {suffix}",
                "entityType": "claim",
                "observations": ["o"],
                "confidence": conf,
            }
        ),
        multi,
    )
    target = create_entity(
        CreateEntityInput.model_validate(
            {
                "name": f"Target {suffix}",
                "entityType": "claim",
                "observations": ["o"],
                "confidence": conf,
            }
        ),
        multi,
    )
    create_relation(
        CreateRelationInput.model_validate(
            {
                "from": trigger["id"],
                "to": target["id"],
                "relationType": "supports",
                "polarity": None,
                "strength": "moderate",
                "evidence": None,
            }
        ),
        multi,
    )
    return str(trigger["id"]), str(target["id"])


def _verify_propagate_credit_dry_run(multi: MultiGraph) -> None:
    trigger_a, _ = _claim_entity_pair(multi, "Omitted")
    omitted = propagate_credit(
        PropagateCreditInput.model_validate({"entityIds": [trigger_a], "delta": 0.2}), multi
    )["items"][0]

    trigger_b, _ = _claim_entity_pair(multi, "Explicit")
    explicit = propagate_credit(
        PropagateCreditInput.model_validate(
            {"entityIds": [trigger_b], "delta": 0.2, "dryRun": False}
        ),
        multi,
    )["items"][0]

    assert omitted["applied"] is True, "documented default (false) must persist by default"
    assert explicit["applied"] is True
    assert omitted["changes"][0]["newConfidence"] == pytest.approx(
        explicit["changes"][0]["newConfidence"]
    )


_INFERENCE_RULE = {
    "description": "test-fixture rule for the documented-default check",
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


def _seed_rule_and_fact(multi: MultiGraph, suffix: str) -> str:
    """A fresh rule (its own name) plus a fact it matches, scoped to itself
    via ``ruleId`` on the call so two seedings in the same graph never
    interfere with each other's evaluation."""
    rule = inference_rule_create(
        InferenceRuleCreateInput.model_validate(
            {"rule": {**_INFERENCE_RULE, "name": f"documented-default-rule-{suffix}"}}
        ),
        multi,
    )
    a, b = _concept(multi, f"Rain {suffix}"), _concept(multi, f"Ground {suffix}")
    create_relation(
        CreateRelationInput.model_validate(
            {
                "from": a,
                "to": b,
                "relationType": "related_to",
                "polarity": None,
                "strength": "moderate",
                "evidence": None,
            }
        ),
        multi,
    )
    return str(rule["id"])


def _verify_run_inference_dry_run(multi: MultiGraph) -> None:
    rule_id_omitted = _seed_rule_and_fact(multi, "omitted")
    omitted = run_inference(RunInferenceInput.model_validate({"ruleId": rule_id_omitted}), multi)

    rule_id_explicit = _seed_rule_and_fact(multi, "explicit")
    explicit = run_inference(
        RunInferenceInput.model_validate({"ruleId": rule_id_explicit, "dryRun": False}), multi
    )

    assert omitted["applied"] is True, "documented default (false) must persist by default"
    assert omitted["traceId"] is not None
    assert explicit["applied"] is True
    assert explicit["traceId"] is not None
    assert omitted["derivedRelations"] and explicit["derivedRelations"]


def _verify_semiring_distances_direction(multi: MultiGraph) -> None:
    source, target = _concept(multi, "SemSource"), _concept(multi, "SemTarget")
    _causes(multi, source, target)

    omitted = semiring_distances(
        SemiringDistancesInput.model_validate({"source": source, "semiring": "viterbi"}), multi
    )
    explicit = semiring_distances(
        SemiringDistancesInput.model_validate(
            {"source": source, "semiring": "viterbi", "direction": "out"}
        ),
        multi,
    )
    assert omitted["distances"], "expected real reach along the documented default direction"
    assert omitted["distances"] == explicit["distances"]


def _verify_create_relations_graph_fallback(multi: MultiGraph) -> None:
    scratch = "docdefault-scratch"
    multi.create_graph(scratch)
    a = create_entity(
        CreateEntityInput.model_validate(
            {"name": "GA", "entityType": "concept", "observations": ["a"], "graph": scratch}
        ),
        multi,
    )["id"]
    b = create_entity(
        CreateEntityInput.model_validate(
            {"name": "GB", "entityType": "concept", "observations": ["b"], "graph": scratch}
        ),
        multi,
    )["id"]

    result = create_relations(
        CreateRelationsInput.model_validate(
            {
                "graph": scratch,
                "relations": [
                    {
                        "from": a,
                        "to": b,
                        "relationType": "supports",
                        "polarity": None,
                        "strength": "moderate",
                        "evidence": None,
                    }
                ],
            }
        ),
        multi,
    )
    assert result["applied"] == 1, "item lacking its own graph must fall back to the top-level one"
    pairs = [
        (r["from"], r["to"])
        for r in list_relations(ListRelationsInput.model_validate({"graph": scratch}), multi)[
            "items"
        ]
    ]
    assert (a, b) in pairs


def _verify_what_changed_limit(_multi: MultiGraph) -> None:
    """Manufacturing 501 raw events to observe the paging cutoff behaviorally
    is disproportionate for what this claim actually asserts; instead this
    reads the literal constant ``what_changed`` resolves an omitted ``limit``
    against, which is exactly the code path the description describes."""
    assert receipts_ops._DEFAULT_LIMIT == 500


def _verify_list_worlds_include_reaped(multi: MultiGraph) -> None:
    forked = fork_world(ForkWorldInput.model_validate({"graph": "default"}), multi)
    world_id = forked["worldId"]
    abandon_world(AbandonWorldInput.model_validate({"worldId": world_id}), multi)

    omitted = list_worlds(ListWorldsInput.model_validate({}), multi)
    explicit_true = list_worlds(ListWorldsInput.model_validate({"includeReaped": True}), multi)

    assert world_id not in {w["worldId"] for w in omitted["items"]}, (
        "documented default (false) must hide reaped/abandoned/merged worlds"
    )
    assert world_id in {w["worldId"] for w in explicit_true["items"]}, (
        "includeReaped: true must still find it -- reaping never forgets a world"
    )


DEFAULT_VERIFIERS: dict[tuple[str, str], tuple[Any, Callable[[MultiGraph], None]]] = {
    ("create-relations", "graph"): (
        "falls back to `graph`",
        _verify_create_relations_graph_fallback,
    ),
    ("detect-loops", "persist"): (False, _verify_detect_loops_persist),
    ("list-worlds", "includeReaped"): (False, _verify_list_worlds_include_reaped),
    ("propagate-credit", "dryRun"): (False, _verify_propagate_credit_dry_run),
    ("run-inference", "dryRun"): (False, _verify_run_inference_dry_run),
    ("semiring-distances", "direction"): ("out", _verify_semiring_distances_direction),
    ("what-changed", "limit"): (500, _verify_what_changed_limit),
}


# =============================================================================
# The tests
# =============================================================================


def test_every_documented_default_has_a_verifier() -> None:
    """Registry-walking coverage: every prose default claim found anywhere in
    the CLI's schemas must have a registered verifier with a matching
    expected value -- a claim discovered here with no verifier entry (a new
    one landed, or an existing one's claimed value changed) fails the suite
    until someone adds or updates one, the same forcing function as an
    uncataloged notice code."""
    claims = _documented_default_claims()
    assert claims, "expected to find at least the known documented-default claims"
    missing = set(claims) - set(DEFAULT_VERIFIERS)
    assert not missing, (
        f"documented default claim(s) with no registered verifier: {sorted(missing)}"
    )
    stale = set(DEFAULT_VERIFIERS) - set(claims)
    assert not stale, f"verifier(s) registered for a claim no longer in the schema: {sorted(stale)}"
    for key, claim in claims.items():
        expected_value, _ = DEFAULT_VERIFIERS[key]
        assert claim.value == expected_value, (
            f"{key}: schema prose claims default {claim.value!r} but the registered "
            f"verifier expects {expected_value!r} -- update whichever one is stale"
        )


@pytest.mark.parametrize("key", sorted(DEFAULT_VERIFIERS))
def test_documented_default_matches_the_code_path(key: tuple[str, str], multi: MultiGraph) -> None:
    _, verify = DEFAULT_VERIFIERS[key]
    verify(multi)
