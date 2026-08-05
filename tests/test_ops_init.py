"""init tests.

Creates ~/.loom (0700) and config.json (0600) and registers the default graph.
The FalkorDB build writes graphHost/graphPort into the config and registers the
default graph in the store. Idempotent: an existing config file is never
overwritten.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

from theloom.operations.init import run_init
from theloom.store.multigraph import MultiGraph


def test_init_creates_dir_and_config_with_restricted_modes(
    tmp_path: Path, multi: MultiGraph
) -> None:
    config_dir = tmp_path / ".loom"
    result = run_init(config_dir, multi)

    assert stat.S_IMODE(config_dir.stat().st_mode) == 0o700
    config_path = config_dir / "config.json"
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    config = json.loads(config_path.read_text())
    assert config == {"graphHost": "localhost", "graphPort": 6379, "defaultGraph": "default"}

    assert result["configPath"] == str(config_path)
    assert result["defaultGraph"] == "default"
    assert multi.has_graph("default")


def test_init_preserves_existing_config(tmp_path: Path, multi: MultiGraph) -> None:
    config_dir = tmp_path / ".loom"
    config_dir.mkdir(mode=0o700)
    config_path = config_dir / "config.json"
    config_path.write_text(json.dumps({"defaultGraph": "custom", "graphHost": "remote"}))
    config_path.chmod(0o600)

    result = run_init(config_dir, multi)
    preserved = json.loads(config_path.read_text())
    assert preserved["defaultGraph"] == "custom"  # never overwritten
    assert result["configPath"] == str(config_path)
