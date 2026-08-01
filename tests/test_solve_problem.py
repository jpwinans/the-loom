"""Verification for solve-problem.

Because the pipeline pings a local LLM server directly, its output is
non-deterministic and cannot be golden-tested. Instead we mock the OpenAI-compat
endpoint with ``httpx.MockTransport`` (the unified client already accepts a
``transport=`` kwarg) and assert the classify→route→verify→format pipeline plus
the deterministic no-LLM / empty-question envelopes.
"""

from __future__ import annotations

import json
from typing import Any, cast

import httpx
import pytest

from theloom.operations import solve
from theloom.operations.prompt_loader import load_prompt
from theloom.operations.solve import SolveProblemInput, solve_problem
from theloom.synthesis.llm import OpenAICompatSynthesisClient

_CLASSIFY_PROMPT = load_prompt("classify-and-translate", solve.LOCAL_MODEL)
_FALLBACK_PROMPT = load_prompt("solve-fallback", solve.LOCAL_MODEL)


def _mock_client(classify: str, fallback: str = "ANSWER: 42") -> OpenAICompatSynthesisClient:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        system = body["messages"][0]["content"]
        content = fallback if system == _FALLBACK_PROMPT else classify
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}}], "usage": {}}
        )

    return OpenAICompatSynthesisClient(
        base_url="http://mock", model="mock", transport=httpx.MockTransport(handler)
    )


def _run(question: str) -> dict[str, Any]:
    return solve_problem(SolveProblemInput(question=question), cast(Any, None))


def test_empty_question_envelope() -> None:
    result = _run("   ")
    assert result == {
        "success": False,
        "answer": None,
        "category": None,
        "method": None,
        "sympy_result": None,
        "verified": None,
        "reasoning": None,
        "error": "Empty question",
    }


def test_no_llm_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(solve, "create_synthesis_client", lambda: None)
    result = _run("What is 2 + 2?")
    assert result["success"] is False
    assert result["error"] == "No LLM available (Ollama offline, no API key)"


def test_pipeline_solve(monkeypatch: pytest.MonkeyPatch) -> None:
    classify = json.dumps(
        {
            "category": "algebra",
            "solvable_by_sympy": True,
            "key_operation": "solve",
            "equations": ["x**2 - 4"],
            "variables": ["x"],
            "expression": None,
            "substitutions": None,
            "answer_format": "list",
        }
    )
    monkeypatch.setattr(solve, "create_synthesis_client", lambda: _mock_client(classify))
    result = _run("solve x^2 - 4 = 0")
    assert result["success"] is True
    assert result["category"] == "algebra"
    assert result["method"] == "sympy.solve"
    assert result["sympy_result"] == "['-2', '2']"
    assert result["answer"] == "-2, 2"


def test_pipeline_simplify(monkeypatch: pytest.MonkeyPatch) -> None:
    classify = json.dumps(
        {
            "category": "algebra",
            "solvable_by_sympy": True,
            "key_operation": "factor",
            "equations": None,
            "variables": None,
            "expression": "x**2 - 1",
            "substitutions": None,
            "answer_format": "expression",
        }
    )
    monkeypatch.setattr(solve, "create_synthesis_client", lambda: _mock_client(classify))
    result = _run("factor x^2 - 1")
    assert result["success"] is True
    assert result["method"] == "sympy.factor"
    # formatAnswer prefers latex_result over the plain result.
    assert result["answer"] == r"\left(x - 1\right) \left(x + 1\right)"
    assert result["sympy_result"] == "(x - 1)*(x + 1)"


def test_fallback_when_not_solvable(monkeypatch: pytest.MonkeyPatch) -> None:
    classify = json.dumps(
        {"category": "wordproblem", "solvable_by_sympy": False, "key_operation": "none"}
    )
    monkeypatch.setattr(
        solve,
        "create_synthesis_client",
        lambda: _mock_client(classify, fallback="Reasoning here.\nANSWER: 42"),
    )
    result = _run("A train leaves...")
    assert result["success"] is True
    assert result["method"] == "llm_fallback"
    assert result["answer"] == "42"
    assert result["category"] == "wordproblem"


def test_invalid_json_pipeline_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(solve, "create_synthesis_client", lambda: _mock_client("not json at all"))
    result = _run("solve something")
    assert result["success"] is False
    assert result["error"] is not None
    assert result["error"].startswith("solve_problem pipeline error")
