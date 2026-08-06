"""Typed error codes on the symbolic-math soft-fail envelope.

symbolic-* commands never raise: theloom.symbolic.core returns plain
``{success, error}`` dicts. operations/symbolic.py stamps a typed
``errorCode`` onto any failure so callers never have to substring-match the
``error`` prose.
"""

from __future__ import annotations

from typing import cast

from theloom.operations.symbolic import (
    ExpressionInput,
    VerifyInput,
    symbolic_factor,
    symbolic_verify,
)
from theloom.store.multigraph import MultiGraph


def test_factor_failure_carries_error_code() -> None:
    result = symbolic_factor(
        ExpressionInput.model_validate({"expression": "this is not valid sympy $$$"}),
        cast(MultiGraph, None),
    )
    assert result["success"] is False
    assert result["errorCode"] == "OPERATION_ERROR"


def test_verify_success_has_no_error_code() -> None:
    result = symbolic_verify(
        VerifyInput.model_validate({"equation": "x - 2", "variable": "x", "value": "2"}),
        cast(MultiGraph, None),
    )
    assert result["success"] is True
    assert "errorCode" not in result or result.get("errorCode") is None
