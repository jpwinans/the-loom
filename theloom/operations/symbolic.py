"""Symbolic-math operations. Thin adapters over theloom.symbolic.core.

These NEVER throw: parse/eval failures come back as
``{success: false, error, errorCode: "OPERATION_ERROR"}`` with exit 0 —
``errorCode`` carries theloom.errors' typed taxonomy so callers never need to
substring-match ``error``'s prose. The CLI input schemas expose only the
fields below (a subset of what the underlying script accepts)."""

from __future__ import annotations

from typing import Any

from theloom.operations.common import CommandInput
from theloom.store.multigraph import MultiGraph
from theloom.symbolic import core

Doc = dict[str, Any]


def _run(operation: str, payload: Doc) -> Doc:
    """Call ``core.run`` and stamp a typed ``errorCode`` onto failures.

    ``theloom.symbolic.core`` is a thin adapter over an external computation
    script and returns plain ``{success, error}`` dicts with no typed code of
    its own; every failure it can produce (unknown operation, bad input,
    timeout, unexpected exception) is a genuine operation failure, so
    ``OPERATION_ERROR`` applies uniformly.
    """
    result = core.run(operation, payload)
    if not result.get("success"):
        result.setdefault("errorCode", "OPERATION_ERROR")
    return result


class SolveInput(CommandInput):
    equation: str | None = None
    equations: list[str] | None = None
    variable: str | None = None
    variables: list[str] | None = None


class ExpressionInput(CommandInput):
    expression: str


class VerifyInput(CommandInput):
    equation: str
    variable: str
    value: str | float


class EvaluateInput(CommandInput):
    expression: str
    substitutions: dict[str, str | float] | None = None


def _payload(params: CommandInput) -> Doc:
    return params.model_dump(by_alias=True, exclude_none=True)


def symbolic_solve(params: SolveInput, _multi: MultiGraph) -> Doc:
    return _run("solve", _payload(params))


def symbolic_simplify(params: ExpressionInput, _multi: MultiGraph) -> Doc:
    return _run("simplify", _payload(params))


def symbolic_verify(params: VerifyInput, _multi: MultiGraph) -> Doc:
    return _run("verify", _payload(params))


def symbolic_factor(params: ExpressionInput, _multi: MultiGraph) -> Doc:
    return _run("factor", _payload(params))


def symbolic_expand(params: ExpressionInput, _multi: MultiGraph) -> Doc:
    return _run("expand", _payload(params))


def symbolic_evaluate(params: EvaluateInput, _multi: MultiGraph) -> Doc:
    return _run("evaluate", _payload(params))


def symbolic_latex(params: ExpressionInput, _multi: MultiGraph) -> Doc:
    return _run("latex", _payload(params))
