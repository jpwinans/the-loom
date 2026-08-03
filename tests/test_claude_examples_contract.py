"""The `loom` invocations in `.claude/` must stay valid against the CLI's schemas.

`.claude/` holds worked examples of driving The Loom — the deep-research and
hyper-research commands and the agents they spawn. Those examples are prose, so
nothing stops them drifting out of contract with the registry as input models
change. They had: `create-relation` omitted three required keys in all twenty of
its examples, and five commands used field names the CLI does not accept.

This harvests every ``loom <command> '<json>'`` in `.claude/` and validates the
payload against that command's `input_model`. It is pure validation — no
FalkorDB, no side effects, no network.

Placeholders are substituted by *field name* rather than by their text, since a
doc writes things like ``"from": "<evidence_id>"`` and only the key reliably says
what shape the value takes.
"""

from __future__ import annotations

import json
import re
from enum import Enum
from pathlib import Path
from typing import Any, Literal, get_args, get_origin

import pytest
from pydantic import BaseModel, ValidationError

from theloom.cli.registry import COMMANDS as COMMAND_DESCRIPTORS

REPO_ROOT = Path(__file__).resolve().parents[1]
CLAUDE_DIR = REPO_ROOT / ".claude"

# `loom <cmd> '<json>'` — non-greedy so consecutive examples don't merge.
INVOCATION_RE = re.compile(r"loom ([a-z][a-z0-9-]+) '(\{.*?\})'", re.S)

# Keys whose values are entity/relation UUIDs.
ID_KEYS = frozenset(
    {
        "id",
        "entityId",
        "source",
        "target",
        "sourceId",
        "targetId",
        "from",
        "to",
        "startId",
        "leveragePointId",
        "loopId",
        "nodeId",
        "relationId",
        "seed",
        "primary",
        "secondary",
    }
)
GRAPH_KEYS = frozenset({"graph", "sourceGraph", "targetGraph"})

PLACEHOLDER_UUID = "00000000-0000-4000-8000-000000000000"
PLACEHOLDER_GRAPH = "contract-test-graph"
PLACEHOLDER_TEXT = "contract test value"

_PLACEHOLDER_RE = re.compile(r"__\w+__|<[^>]*>")


def _normalise(raw: str) -> str:
    """Turn shell interpolation and <angle placeholders> into inert tokens."""
    s = raw.replace("\n", " ")
    s = re.sub(r"'\"\$\{(\w+)\}\"'", r"__\1__", s)  # '"${VAR}"' -> __VAR__
    s = re.sub(r"\$\{(\w+)\}", r"__\1__", s)  # ${VAR}     -> __VAR__
    return re.sub(r"<[^>]*>", "__PH__", s)  # <thing>    -> __PH__


def _allowed_values(model: type[BaseModel] | None, key: str) -> tuple[Any, ...] | None:
    """Valid literals for `key` on `model`, when the field is an enum or Literal.

    A doc may write a shell variable where an enum belongs (``"relationType":
    "${REL_TYPE}"``). Guessing a string there would fail validation for a reason
    that says nothing about the documentation, so ask the model what it accepts.
    """
    if model is None:
        return None
    for name, field in model.model_fields.items():
        if key not in (field.alias, name):
            continue
        for annotation in (field.annotation, *get_args(field.annotation)):
            if get_origin(annotation) is Literal:
                return get_args(annotation)
            if isinstance(annotation, type) and issubclass(annotation, Enum):
                return tuple(m.value for m in annotation)
        return None
    return None


def _substitute(value: Any, key: str | None = None, model: type[BaseModel] | None = None) -> Any:
    """Replace placeholder tokens with values of the shape the field implies."""
    if isinstance(value, dict):
        return {k: _substitute(v, k, model) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, key, model) for v in value]
    if isinstance(value, str) and _PLACEHOLDER_RE.search(value):
        if key in GRAPH_KEYS:
            return PLACEHOLDER_GRAPH
        if key in ID_KEYS:
            return PLACEHOLDER_UUID
        if key is not None and (allowed := _allowed_values(model, key)):
            return allowed[0]
        return PLACEHOLDER_TEXT
    return value


def _harvest() -> list[tuple[str, str, dict[str, Any], str]]:
    """(command, source location, payload, raw json) for every parseable example."""
    found: list[tuple[str, str, dict[str, Any], str]] = []
    for path in sorted(CLAUDE_DIR.rglob("*.md")):
        text = path.read_text()
        for match in INVOCATION_RE.finditer(text):
            command, raw = match.group(1), match.group(2)
            line = text[: match.start()].count("\n") + 1
            where = f"{path.relative_to(REPO_ROOT)}:{line}"
            try:
                payload = json.loads(_normalise(raw))
            except json.JSONDecodeError:
                # Illustrative snippets (`{...}`, `[...]` elisions) are not
                # invocations; they carry no contract to check.
                continue
            found.append((command, where, payload, raw))
    return found


EXAMPLES = _harvest()
COMMANDS = {spec.name: spec for spec in COMMAND_DESCRIPTORS}


def test_examples_were_found() -> None:
    """Guard the harvester itself: a silent zero would make every test vacuous."""
    assert len(EXAMPLES) > 100, f"harvested only {len(EXAMPLES)} invocations"


@pytest.mark.parametrize(
    ("command", "where", "payload"),
    [(c, w, p) for c, w, p, _ in EXAMPLES],
    ids=[f"{c}@{w.rsplit('/', 1)[-1]}" for c, w, _, _ in EXAMPLES],
)
def test_documented_invocation_matches_cli_schema(
    command: str, where: str, payload: dict[str, Any]
) -> None:
    assert command in COMMANDS, f"{where}: `loom {command}` is not a registered command"

    model: type[BaseModel] = COMMANDS[command].input_model
    try:
        model.model_validate(_substitute(payload, model=model))
    except ValidationError as exc:
        pytest.fail(
            f"{where}: documented payload for `loom {command}` "
            f"no longer validates against {model.__name__}\n"
            f"  payload: {json.dumps(payload)[:300]}\n"
            f"  {exc}"
        )
