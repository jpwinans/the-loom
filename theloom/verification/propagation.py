"""AC-3 constraint propagation.

Builds a CSP from typed relation constraints (each constraint yields a
source and target variable over the 19 entity types) and runs AC-3 with a
LIFO worklist — the stack order is load-bearing because it drives
revisionsCount and which variable is reported empty on a conflict.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from theloom.model import ALL_ENTITY_TYPES

Doc = dict[str, Any]
_ALL_TYPE_NAMES = [t.value for t in ALL_ENTITY_TYPES]


class _Constraint:
    __slots__ = ("name", "variable1", "variable2", "check")

    def __init__(
        self, name: str, variable1: str, variable2: str, check: Callable[[str, str], bool]
    ) -> None:
        self.name = name
        self.variable1 = variable1
        self.variable2 = variable2
        self.check = check


def build_csp(type_constraints: list[Doc]) -> tuple[dict[str, list[str]], list[_Constraint]]:
    """Branch B (the propagate-constraints path): two variables per constraint."""
    domain_values = list(_ALL_TYPE_NAMES)
    variables: dict[str, list[str]] = {}
    constraints: list[_Constraint] = []
    for tc in type_constraints:
        source_type, target_type, relation_type = (
            tc["sourceType"],
            tc["targetType"],
            tc["relationType"],
        )
        source_var = f"source:{relation_type}:{source_type}-{target_type}"
        target_var = f"target:{relation_type}:{source_type}-{target_type}"
        variables.setdefault(source_var, list(domain_values))
        variables.setdefault(target_var, list(domain_values))

        def _check(v1: str, v2: str, st: str = source_type, tt: str = target_type) -> bool:
            return v1 == st and v2 == tt

        constraints.append(
            _Constraint(
                name=f"{relation_type}:{source_type}->{target_type}",
                variable1=source_var,
                variable2=target_var,
                check=_check,
            )
        )
    return variables, constraints


def _revise(
    xi: str,
    xj: str,
    constraint: _Constraint,
    is_var1: bool,
    domains: dict[str, dict[str, None]],
    pruned: dict[str, list[str]],
) -> bool:
    # Iterate the domain in insertion order (= ALL_ENTITY_TYPES order) so the
    # pruned list follows a stable, deterministic order.
    removed = []
    for val_i in list(domains[xi]):
        supported = any(
            (constraint.check(val_i, val_j) if is_var1 else constraint.check(val_j, val_i))
            for val_j in domains[xj]
        )
        if not supported:
            removed.append(val_i)
    for val in removed:
        del domains[xi][val]
        pruned.setdefault(xi, []).append(val)
    return bool(removed)


def propagate(variables: dict[str, list[str]], constraints: list[_Constraint]) -> Doc:
    domains: dict[str, dict[str, None]] = {v: dict.fromkeys(vals) for v, vals in variables.items()}
    pruned: dict[str, list[str]] = {}
    revisions = 0

    # Full worklist: each constraint pushes both arcs, in order.
    worklist: list[tuple[str, _Constraint]] = []
    for constraint in constraints:
        worklist.append((constraint.variable1, constraint))
        worklist.append((constraint.variable2, constraint))

    # Index of constraints by variable, for re-enqueueing neighbours.
    by_var: dict[str, list[_Constraint]] = {}
    for constraint in constraints:
        by_var.setdefault(constraint.variable1, []).append(constraint)
        by_var.setdefault(constraint.variable2, []).append(constraint)

    while worklist:
        var_id, constraint = worklist.pop()  # LIFO — contract
        if var_id not in domains:
            continue
        other_var = constraint.variable2 if constraint.variable1 == var_id else constraint.variable1
        if other_var not in domains:
            continue
        revisions += 1
        is_var1 = constraint.variable1 == var_id
        if _revise(var_id, other_var, constraint, is_var1, domains, pruned):
            if not domains[var_id]:
                return {
                    "consistent": False,
                    "prunedDomains": pruned,
                    "conflictingConstraint": constraint.name,
                    "emptyVariable": var_id,
                    "revisionsCount": revisions,
                }
            for other in by_var.get(var_id, []):
                if other is constraint:
                    continue
                neighbor = other.variable2 if other.variable1 == var_id else other.variable1
                worklist.append((neighbor, other))

    return {"consistent": True, "prunedDomains": pruned, "revisionsCount": revisions}


def serialize(result: Doc) -> Doc:
    out: Doc = {
        "consistent": result["consistent"],
        "prunedDomains": {var: list(vals) for var, vals in result["prunedDomains"].items()},
        "revisionsCount": result["revisionsCount"],
    }
    if not result["consistent"]:
        out["conflictingConstraint"] = result["conflictingConstraint"]
        out["emptyVariable"] = result["emptyVariable"]
    return out
