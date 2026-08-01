"""Symbolic-math operations. Thin adapters over theloom.symbolic.core.

These NEVER throw: parse/eval failures come back as
``{success: false, error}`` with exit 0. The CLI input schemas expose only
the fields below (a subset of what the underlying script accepts)."""

from __future__ import annotations

from typing import Any

from theloom.operations.common import CommandInput
from theloom.store.multigraph import MultiGraph
from theloom.symbolic import core

Doc = dict[str, Any]


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
    return core.run("solve", _payload(params))


def symbolic_simplify(params: ExpressionInput, _multi: MultiGraph) -> Doc:
    return core.run("simplify", _payload(params))


def symbolic_verify(params: VerifyInput, _multi: MultiGraph) -> Doc:
    return core.run("verify", _payload(params))


def symbolic_factor(params: ExpressionInput, _multi: MultiGraph) -> Doc:
    return core.run("factor", _payload(params))


def symbolic_expand(params: ExpressionInput, _multi: MultiGraph) -> Doc:
    return core.run("expand", _payload(params))


def symbolic_evaluate(params: EvaluateInput, _multi: MultiGraph) -> Doc:
    return core.run("evaluate", _payload(params))


def symbolic_latex(params: ExpressionInput, _multi: MultiGraph) -> Doc:
    return core.run("latex", _payload(params))
