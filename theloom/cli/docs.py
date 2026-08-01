"""Command-catalog generation (`loom --generate-docs > COMMANDS.md`).

The catalog is derived from the registry — the single source of the CLI
surface — so it can never drift from the implementation: here the doc IS the
registry.
"""

from __future__ import annotations

from collections import defaultdict

from theloom.cli.registry import COMMANDS, CommandDescriptor


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
    ]
    for category in sorted(by_category):
        lines.append(f"## {category}")
        lines.append("")
        for descriptor in sorted(by_category[category], key=lambda d: d.name):
            lines.append(f"- **`{descriptor.name}`** — {descriptor.summary}")
        lines.append("")
    return "\n".join(lines)
