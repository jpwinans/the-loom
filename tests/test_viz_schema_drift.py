"""The committed JSON Schema must match the live Pydantic model — regenerate
with: uv run python -m theloom.viz.schema"""

from __future__ import annotations

import json
from pathlib import Path

from theloom.viz.schema import bundle_json_schema

SCHEMA_PATH = Path(__file__).parent.parent / "tapestry" / "schema" / "bundle.schema.json"


def test_committed_schema_matches_model() -> None:
    committed = json.loads(SCHEMA_PATH.read_text())
    assert committed == bundle_json_schema()
