"""``loom <command> --schema`` (TL-486): every registry command prints its
input model's JSON Schema and exits 0, driven generically off
``CommandDescriptor.input_model`` — no per-command code.

Also covers the companion enrichment at the same call site: PARSE_ERROR and
INPUT_REQUIRED failures (raised before a command's own input model is even
reached) point the caller at ``--schema`` instead of leaving them to guess.
"""

from __future__ import annotations

import json
import re

import pytest
from typer.testing import CliRunner

from theloom.cli.app import app
from theloom.cli.registry import COMMANDS

runner = CliRunner()

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


# Rich/Typer render --help through a colorized, box-drawn layout whenever the
# ambient environment looks like a color-capable terminal (observed in CI,
# not reproduced by default in a plain local shell). Two effects follow: (1)
# a styled token like "--schema" can be split across separate ANSI style
# spans so the raw substring is absent from ``result.output``, and (2) a
# narrow render width can word-wrap a flag name across lines. Neither affects
# the feature — only whether a plain substring match on rendered help text is
# reliable. Route --help invocations through this helper instead of asserting
# on ``result.output`` directly: it pins a wide, stable render width and
# strips ANSI escapes so the plain text is deterministic across CI and local
# runs alike.
def _invoke_help(args: list[str]) -> str:
    result = runner.invoke(app, args, env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    return _ANSI_ESCAPE_RE.sub("", result.output)


# One command per category-ish spread, exercised individually below so a
# failure names the exact command rather than a generic parametrize id.
SPOT_CHECK_COMMANDS = [
    "create-entity",
    "create-relation",
    "create-relations",
    "update-entity",
    "delete-entity",
    "list-entities",
    "embed-entity",
    "embed-entities",
    "semantic-search",
    "detect-loops",
    "list-loops",
    "propagate-credit",
    "run-inference",
    "verify-fidelity",
    "ingest-content",
    "create-graph",
    "list-graphs",
    "bulk-import",
    "symbolic-solve",
    "explore",
]


def test_spot_check_commands_are_registered() -> None:
    names = {c.name for c in COMMANDS}
    missing = [name for name in SPOT_CHECK_COMMANDS if name not in names]
    assert not missing, f"spot-check list references unregistered commands: {missing}"


@pytest.mark.parametrize("command_name", SPOT_CHECK_COMMANDS)
def test_schema_flag_prints_input_model_json_schema(command_name: str) -> None:
    descriptor = next(c for c in COMMANDS if c.name == command_name)
    result = runner.invoke(app, [command_name, "--schema"])
    assert result.exit_code == 0, result.output
    printed = json.loads(result.stdout)
    assert printed == descriptor.input_model.model_json_schema()


@pytest.mark.parametrize("descriptor", COMMANDS, ids=[c.name for c in COMMANDS])
def test_schema_flag_works_for_every_registry_command(descriptor: object) -> None:
    from theloom.cli.registry import CommandDescriptor

    assert isinstance(descriptor, CommandDescriptor)
    result = runner.invoke(app, [descriptor.name, "--schema"])
    assert result.exit_code == 0, f"{descriptor.name}: {result.output}"
    printed = json.loads(result.stdout)
    assert printed == descriptor.input_model.model_json_schema()


def test_schema_flag_requires_no_store_connection() -> None:
    """--schema must short-circuit before _build_multigraph(): it works with
    no FalkorDB reachable (a fresh agent should be able to discover shapes
    before ever standing up the store)."""
    result = runner.invoke(app, ["create-entity", "--schema"])
    assert result.exit_code == 0
    schema = json.loads(result.stdout)
    assert schema["title"] == "CreateEntityInput"


def test_schema_flag_appears_in_command_help() -> None:
    output = _invoke_help(["create-entity", "--help"])
    assert "--schema" in output


def test_schema_flag_output_matches_enum_and_required_fields() -> None:
    result = runner.invoke(app, ["create-relation", "--schema"])
    assert result.exit_code == 0
    schema = json.loads(result.stdout)
    assert "relationType" in schema["properties"]
    assert set(schema["required"]) >= {"from", "to", "relationType", "strength"}


def test_bulk_import_raw_handler_command_also_exposes_schema() -> None:
    """bulk-import uses ``raw_handler`` (bypasses model_validate in
    run_handler), but --schema is driven off ``descriptor.input_model``
    directly in app.py, not through run_handler — so the raw-handler hatch
    doesn't leave it schema-less."""
    result = runner.invoke(app, ["bulk-import", "--schema"])
    assert result.exit_code == 0
    schema = json.loads(result.stdout)
    assert schema["title"] == "BulkImportInput"


def test_missing_input_error_points_to_schema_flag() -> None:
    result = runner.invoke(app, ["create-entity"], input="")
    assert result.exit_code == 1
    error = json.loads(result.output)
    assert error["code"] == "INPUT_REQUIRED"
    assert "create-entity --schema" in error["error"]


def test_missing_input_error_names_required_top_level_fields() -> None:
    result = runner.invoke(app, ["create-entity"], input="")
    error = json.loads(result.output)
    assert "name" in error["error"]
    assert "entityType" in error["error"]


def test_malformed_json_error_points_to_schema_flag() -> None:
    result = runner.invoke(app, ["create-entity", "{not json"])
    assert result.exit_code == 1
    error = json.loads(result.output)
    assert error["code"] == "PARSE_ERROR"
    assert "create-entity --schema" in error["error"]
