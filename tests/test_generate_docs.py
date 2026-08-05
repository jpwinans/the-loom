"""`loom --generate-docs` — the command catalog is generated from the registry
(CLAUDE.md: `uv run loom --generate-docs > COMMANDS.md`; never hand-edit)."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from theloom.cli.app import app
from theloom.cli.docs import generate_docs
from theloom.cli.registry import COMMANDS

runner = CliRunner()


def test_generate_docs_lists_every_registry_command() -> None:
    text = generate_docs()
    for descriptor in COMMANDS:
        assert f"`{descriptor.name}`" in text, descriptor.name
        assert descriptor.summary in text, descriptor.name
    # Grouped by category, catalog header present.
    assert text.startswith("# Command Catalog")
    for category in {c.category for c in COMMANDS}:
        assert f"## {category}" in text


def test_cli_flag_prints_catalog_and_exits_zero() -> None:
    result = runner.invoke(app, ["--generate-docs"])
    assert result.exit_code == 0
    assert result.stdout == generate_docs()


def test_commands_md_is_regenerated_from_the_registry() -> None:
    """COMMANDS.md is generated, never hand-edited — a registry summary change
    that isn't regenerated leaves the published catalog lying about a command."""
    catalog = Path(__file__).resolve().parent.parent / "COMMANDS.md"
    assert catalog.read_text() == generate_docs(), (
        "COMMANDS.md is stale; regenerate with `uv run loom --generate-docs > COMMANDS.md`"
    )


def test_flag_does_not_break_subcommands() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
