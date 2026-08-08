"""`loom --generate-docs` — the command catalog is generated from the registry
(CLAUDE.md: `uv run loom --generate-docs > COMMANDS.md`; never hand-edit)."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from theloom.cli.app import app
from theloom.cli.docs import generate_docs
from theloom.cli.registry import COMMANDS

runner = CliRunner()


def _block(text: str, command_name: str) -> str:
    """The slice of the catalog covering one command's entry: from its
    bullet up to (but not including) the next command's bullet."""
    marker = f"`{command_name}`"
    start = text.index(marker)
    next_bullet = text.find("\n- **`", start)
    return text[start : next_bullet if next_bullet != -1 else len(text)]


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


# =============================================================================
# TL-486: per-command field documentation
# =============================================================================


def test_catalog_documents_top_level_fields_for_create_entity() -> None:
    block = _block(generate_docs(), "create-entity")
    for field in ("name", "entityType", "observations", "confidence", "provenance", "graph"):
        assert f"`{field}`" in block, f"{field} missing from create-entity's catalog entry"


def test_catalog_documents_nested_confidence_fields() -> None:
    """The {score, basis} confidence object was one of the exact shapes probe
    agents had to read source to discover (TL-486's motivating example) — the
    catalog must spell it out, not just say "confidence: object"."""
    block = _block(generate_docs(), "create-entity")
    assert "`confidence.score`" in block
    assert "`confidence.basis`" in block


def test_catalog_marks_required_vs_optional_fields() -> None:
    block = _block(generate_docs(), "create-relation")
    for field in ("from", "to", "relationType", "strength"):
        field_line = next(line for line in block.splitlines() if f"`{field}`" in line)
        assert "required" in field_line and "optional" not in field_line, field_line
    graph_line = next(line for line in block.splitlines() if "`graph`" in line)
    assert "optional" in graph_line


def test_catalog_shows_enum_values() -> None:
    block = _block(generate_docs(), "create-relation")
    relation_type_line = next(line for line in block.splitlines() if "`relationType`" in line)
    assert "supports" in relation_type_line
    assert "contradicts" in relation_type_line


def test_catalog_documents_array_of_object_fields_with_bracket_notation() -> None:
    block = _block(generate_docs(), "create-relations")
    assert "`relations[].from`" in block
    assert "`relations[].relationType`" in block


def test_catalog_documents_raw_handler_commands_too() -> None:
    """bulk-import bypasses model_validate via ``raw_handler``, but its
    ``input_model`` still describes the accepted shape — the catalog must not
    skip field docs just because the command isn't handled the ordinary way."""
    block = _block(generate_docs(), "bulk-import")
    for field in ("entities", "relations", "jsonlInput", "dryRun", "graph"):
        assert f"`{field}`" in block, f"{field} missing from bulk-import's catalog entry"


def test_catalog_generation_is_deterministic() -> None:
    assert generate_docs() == generate_docs()
