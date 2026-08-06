"""solve-problem.

Natural-language math solver: classify + translate the question via an LLM, route
to a native SymPy op, verify, and format. A unified LLM client
(:func:`theloom.synthesis.llm.create_synthesis_client`) handles provider routing,
``<think>`` stripping, and the OpenAI-compat protocol.

External-service dependency: this command REQUIRES a configured LLM
(an ``llm`` config section — provider ollama|mlx|openai|anthropic — or
``ANTHROPIC_API_KEY``). With none configured it returns a soft ``{success: false,
error: "No LLM available ...", errorCode: "CONFIG_ERROR"}`` envelope, never
raising. Every failure envelope carries an ``errorCode`` from
:mod:`theloom.errors`' typed taxonomy alongside the human-readable ``error``
prose, so callers never need to substring-match the message. The local model /
prompt profile default is pinned and env-overridable via ``LOOM_LOCAL_MODEL``;
no personal filesystem paths are baked in (the decision-graph engine is disabled).
"""

from __future__ import annotations

import json
import math
import os
import re
from typing import Any

from theloom.errors import ErrorCode
from theloom.operations.common import CommandInput
from theloom.operations.prompt_loader import load_prompt
from theloom.store.multigraph import MultiGraph
from theloom.symbolic import core
from theloom.synthesis.llm import SynthesisLlmClient, create_synthesis_client

# Pinned default (F19); override with LOOM_LOCAL_MODEL (F20). Only selects the
# prompt profile — the actual endpoint/model come from the llm config.
LOCAL_MODEL = os.environ.get("LOOM_LOCAL_MODEL", "mlx-community/Qwen3.5-9B-8bit")

_THINK_RE = re.compile(r"<think>.*?</think>", re.S)
_CODE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
_OBJ_RE = re.compile(r"\{.*\}", re.S)
_ANSWER_RE = re.compile(r"ANSWER:\s*(.+)", re.I)
_QUOTE_RE = re.compile(r"^['\"]|['\"]$")


class SolveProblemInput(CommandInput):
    question: str


def _envelope(
    success: bool,
    *,
    answer: str | None = None,
    category: str | None = None,
    method: str | None = None,
    sympy_result: str | None = None,
    verified: bool | None = None,
    reasoning: str | None = None,
    error: str | None = None,
    error_code: ErrorCode | None = None,
) -> dict[str, Any]:
    """Build the soft-fail envelope. Never raises; ``errorCode`` (present only
    on failure) carries theloom.errors' typed taxonomy so callers never have
    to substring-match ``error``'s prose."""
    return {
        "success": success,
        "answer": answer,
        "category": category,
        "method": method,
        "sympy_result": sympy_result,
        "verified": verified,
        "reasoning": reasoning,
        "error": error,
        "errorCode": error_code if not success else None,
    }


def _extract_json(text: str) -> str:
    cleaned = _THINK_RE.sub("", text.strip()).strip()
    code = _CODE_RE.search(cleaned)
    if code:
        return code.group(1).strip()
    obj = _OBJ_RE.search(cleaned)
    if obj:
        return obj.group(0)
    return cleaned


def _llm_json(client: SynthesisLlmClient, system: str, user: str) -> dict[str, Any]:
    result = client.complete(system, user)
    text = result["text"]
    json_str = _extract_json(text)
    try:
        parsed = json.loads(json_str)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM returned invalid JSON: {json_str[:200]}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"LLM returned invalid JSON: {json_str[:200]}")
    return parsed


def _js_number(num: float) -> str:
    """String(parseFloat(x)) — integral doubles render without a trailing .0."""
    if num.is_integer():
        return str(int(num))
    return repr(num)


def _format_answer(raw: str, fmt: str | None) -> str:
    answer = raw.strip()
    if answer.startswith("[") and answer.endswith("]"):
        inner = answer[1:-1].strip()
        if inner.startswith("'") and inner.endswith("'"):
            answer = inner[1:-1]
        elif "," not in inner:
            answer = inner
    answer = _QUOTE_RE.sub("", answer)
    if fmt == "fraction":
        return answer
    if fmt == "numeric":
        try:
            num = float(answer)
        except ValueError:
            num = None
        if num is not None and math.isfinite(num):
            return _js_number(num)
    return answer


def _route_to_solver(operation: str, t: dict[str, Any]) -> dict[str, Any]:
    equations = t.get("equations")
    variables = t.get("variables")
    expr = t.get("expression") or (equations[0] if equations else "") or ""
    variable = variables[0] if variables else None

    if operation == "solve":
        return core.run(
            "solve",
            {
                "equations": equations,
                "equation": equations[0] if equations else None,
                "variables": variables,
                "variable": variable,
            },
        )
    if operation == "simplify":
        return core.run("simplify", {"expression": expr})
    if operation == "factor":
        return core.run("factor", {"expression": expr})
    if operation == "expand":
        return core.run("expand", {"expression": expr})
    if operation == "evaluate":
        return core.run("evaluate", {"expression": expr, "substitutions": t.get("substitutions")})
    if operation == "verify":
        verify_res = core.run(
            "verify",
            {
                "lhs": t.get("lhs"),
                "rhs": t.get("rhs"),
                "substitutions": t.get("substitutions"),
                "sub_operation": t.get("sub_operation"),
                "expression": expr or None,
                "args": t.get("args"),
            },
        )
        if verify_res.get("success") and isinstance(verify_res.get("is_valid"), bool):
            answer_str = "True" if verify_res["is_valid"] else "False"
            return {**verify_res, "result": answer_str, "latex_result": answer_str}
        return verify_res
    if operation == "integrate":
        return core.run(
            "integrate", {"expression": expr, "variable": variable, "bounds": t.get("bounds")}
        )
    if operation == "diff":
        return core.run("diff", {"expression": expr, "variable": variable, "order": t.get("order")})
    if operation == "limit":
        return core.run(
            "limit",
            {
                "expression": expr,
                "variable": variable,
                "point": t.get("point") or "0",
                "direction": t.get("direction"),
            },
        )
    if operation == "series":
        return core.run(
            "series",
            {
                "expression": expr,
                "variable": variable,
                "point": t.get("point"),
                "order": t.get("order"),
            },
        )
    if operation == "summation":
        return core.run(
            "summation",
            {
                "expression": expr,
                "variable": variable or "n",
                "bounds": t.get("bounds") or {"lower": "0", "upper": "oo"},
            },
        )
    if operation == "product":
        return core.run(
            "product",
            {
                "expression": expr,
                "variable": variable or "n",
                "bounds": t.get("bounds") or {"lower": "1", "upper": "oo"},
            },
        )
    if operation == "trigsimp":
        return core.run("trigsimp", {"expression": expr})
    if operation == "apart":
        return core.run("apart", {"expression": expr, "variable": variable})
    if operation == "matrix":
        if not t.get("matrix"):
            return {"success": False, "result": None}
        return core.run(
            "matrix",
            {
                "matrix": t.get("matrix"),
                "sub_operation": t.get("sub_operation") or "det",
                "variable": variable,
            },
        )
    if operation == "number_theory":
        return core.run(
            "number_theory",
            {
                "sub_operation": t.get("sub_operation") or "factorint",
                "expression": expr or None,
                "args": t.get("args"),
            },
        )
    if operation == "combinatorics":
        return core.run(
            "combinatorics",
            {
                "sub_operation": t.get("sub_operation") or "binomial",
                "expression": expr or None,
                "n": t.get("n"),
                "k": t.get("k"),
            },
        )
    if operation == "dsolve":
        return core.run(
            "dsolve",
            {
                "equation": equations[0] if equations else None,
                "expression": expr or None,
                "variable": variable,
                "function": t.get("function"),
            },
        )
    if operation == "geometry":
        return core.run(
            "geometry",
            {
                "sub_operation": t.get("sub_operation") or "distance",
                "points": t.get("points"),
                "sides": t.get("sides"),
                "radius": t.get("radius"),
                "angle": t.get("angle"),
                "n_sides": t.get("n_sides"),
                "side_length": t.get("side_length"),
            },
        )
    if operation == "chain":
        if t.get("steps"):
            return core.run("chain", {"steps": t["steps"]})
        return {"success": False, "result": None}
    # default: try solve, then simplify.
    if equations:
        return core.run("solve", {"equations": equations, "variables": variables})
    if t.get("expression"):
        return core.run("simplify", {"expression": t["expression"]})
    return {"success": False, "result": None}


def _solve_fallback(
    client: SynthesisLlmClient | None, question: str, category: str | None
) -> dict[str, Any]:
    try:
        if client is None:
            return _envelope(
                False,
                category=category,
                method="llm_fallback",
                error="No LLM available for fallback",
                error_code="CONFIG_ERROR",
            )
        result = client.complete(load_prompt("solve-fallback", LOCAL_MODEL), question)
        text = result["text"]
        match = _ANSWER_RE.search(text)
        answer = match.group(1).strip() if match else None
        return _envelope(
            answer is not None,
            answer=answer,
            category=category,
            method="llm_fallback",
            reasoning=text,
            error=None if answer is not None else "Could not extract answer from LLM response",
            error_code=None if answer is not None else "OPERATION_ERROR",
        )
    except Exception as err:  # noqa: BLE001 — soft-fail envelope, never raise.
        return _envelope(
            False,
            category=category,
            method="llm_fallback",
            error=f"LLM fallback error: {err}",
            error_code="OPERATION_ERROR",
        )


def solve_problem(params: SolveProblemInput, _multi: MultiGraph) -> dict[str, Any]:
    if not params.question.strip():
        return _envelope(False, error="Empty question", error_code="VALIDATION_ERROR")

    client = create_synthesis_client()
    if client is None:
        return _envelope(
            False,
            error="No LLM available (Ollama offline, no API key)",
            error_code="CONFIG_ERROR",
        )

    try:
        # Step 0 (decision graph) is disabled by default (F20) — skipped.
        combined = _llm_json(
            client, load_prompt("classify-and-translate", LOCAL_MODEL), params.question
        )
        category = combined.get("category")
        key_operation = combined.get("key_operation")

        if not combined.get("solvable_by_sympy"):
            return _solve_fallback(client, params.question, category)

        sympy_result = _route_to_solver(str(key_operation), combined)
        if not sympy_result.get("success"):
            return _solve_fallback(client, params.question, category)

        verified: bool | None = None
        equations = combined.get("equations")
        variables = combined.get("variables")
        if (
            equations
            and len(equations) == 1
            and variables
            and len(variables) == 1
            and sympy_result.get("result")
        ):
            try:
                verify_res = core.run(
                    "verify",
                    {
                        "equation": equations[0],
                        "variable": variables[0],
                        "value": sympy_result["result"],
                    },
                )
                verified = (
                    verify_res["is_valid"] if isinstance(verify_res.get("is_valid"), bool) else None
                )
            except Exception:  # noqa: BLE001 — verification is best-effort.
                verified = None

        latex_result = sympy_result.get("latex_result")
        raw_result = sympy_result.get("result") or ""
        answer = _format_answer(latex_result or raw_result, combined.get("answer_format"))

        return _envelope(
            True,
            answer=answer,
            category=category,
            method=f"sympy.{key_operation}",
            sympy_result=sympy_result.get("result"),
            verified=verified,
            reasoning=f"Classified as {category}, solved via SymPy {key_operation}",
        )
    except Exception as err:  # noqa: BLE001 — soft-fail envelope, never raise.
        return _envelope(
            False,
            error=f"solve_problem pipeline error: {err}",
            error_code="OPERATION_ERROR",
        )
