"""Command-catalog generation (`loom --generate-docs > COMMANDS.md`).

The catalog is derived from the registry — the single source of the CLI
surface — so it can never drift from the implementation: here the doc IS the
registry. Each command's entry (TL-486) also lists its input fields —
dotted-path for nested objects, ``[]`` for arrays of objects, enum values
spelled out — so an agent can construct a valid payload from COMMANDS.md
alone, without reading ``theloom/operations/*.py`` or falling back to
``--schema`` for the common case.
"""

from __future__ import annotations

import json
from collections import defaultdict

from theloom.cli.registry import COMMANDS, CommandDescriptor
from theloom.cli.schema import FieldRow, field_rows


def _field_line(row: FieldRow) -> str:
    """One field's documentation line: name, type, required/optional, and
    (when present) its default and description — the exact facts an agent
    needs to fill in that key without guessing."""
    status = "required" if row.required else "optional"
    if row.has_default and row.default is not None:
        status += f", default: {json.dumps(row.default)}"
    line = f"  - `{row.path}` — {row.type}; {status}"
    if row.description:
        # Collapse embedded newlines (class docstrings pulled in verbatim from
        # $defs entries) so each field stays exactly one catalog line.
        line += f" — {' '.join(row.description.split())}"
    return line


def _command_block(descriptor: CommandDescriptor) -> list[str]:
    lines = [f"- **`{descriptor.name}`** — {descriptor.summary}"]
    lines.extend(_field_line(row) for row in field_rows(descriptor.input_model))
    return lines


def generate_docs() -> str:
    """Render the Markdown command catalog from the registry."""
    by_category: dict[str, list[CommandDescriptor]] = defaultdict(list)
    for descriptor in COMMANDS:
        by_category[descriptor.category].append(descriptor)

    lines = [
        "# Command Catalog",
        "",
        "Generated from the registry (`theloom/cli/registry.py`) — never hand-edit.",
        "",
        f"**{len(COMMANDS)} registry commands** across {len(by_category)} categories, "
        "plus the special `init` command.",
        "",
        "Each command lists its input fields below its summary: dotted paths "
        "(`confidence.score`) descend into nested objects, `[]` (`relations[].from`) "
        "marks an array of objects. `required`/`optional` is scoped to the field's "
        "immediate parent — a required field of an optional object only applies once "
        "that object is supplied at all. Run `loom <command> --schema` for the raw "
        "JSON Schema (with full `$defs`) behind any entry.",
        "",
    ]
    for category in sorted(by_category):
        lines.append(f"## {category}")
        lines.append("")
        for descriptor in sorted(by_category[category], key=lambda d: d.name):
            lines.extend(_command_block(descriptor))
            lines.append("")
    return "\n".join(lines)
