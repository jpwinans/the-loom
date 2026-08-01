"""The init command.

Creates the config directory (0700) and config.json (0600), then registers
the default graph. No hardcoded personal paths.

The config carries ``graphHost``/``graphPort`` (FalkorDB) and the output
reflects that, alongside ``configPath`` and ``defaultGraph``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from theloom.config import DEFAULT_GRAPH, DEFAULT_HOST, DEFAULT_PORT
from theloom.store.multigraph import MultiGraph


def run_init(config_dir: Path, multi: MultiGraph) -> dict[str, Any]:
    """Create the config dir + file (idempotent) and register the default graph."""
    config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    config_path = config_dir / "config.json"

    if not config_path.exists():
        default_config = {
            "graphHost": DEFAULT_HOST,
            "graphPort": DEFAULT_PORT,
            "defaultGraph": DEFAULT_GRAPH,
        }
        config_path.write_text(json.dumps(default_config, indent=2) + "\n", encoding="utf-8")
        config_path.chmod(0o600)

    # Registering is a set-add — the MultiGraph constructor already ensures the
    # default graph, so this is idempotent by construction.
    multi.register_graph(multi.default_graph)

    return {
        "configPath": str(config_path),
        "graphHost": DEFAULT_HOST,
        "graphPort": DEFAULT_PORT,
        "defaultGraph": multi.default_graph,
    }
