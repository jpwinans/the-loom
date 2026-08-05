"""The committed JSON Schema must match the live Pydantic model — regenerate
with: uv run python -m theloom.viz.schema"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from theloom.viz.schema import TapestryBundle, bundle_json_schema

REPO_ROOT = Path(__file__).parent.parent
SCHEMA_PATH = REPO_ROOT / "tapestry" / "schema" / "bundle.schema.json"
FIXTURE_PATH = REPO_ROOT / "tapestry" / "fixtures" / "dev-bundle.json"

REGEN_HINT = "Regenerate it with: uv run python -m theloom.viz.schema"


def test_committed_schema_matches_model() -> None:
    committed = json.loads(SCHEMA_PATH.read_text())
    assert committed == bundle_json_schema(), (
        f"{SCHEMA_PATH.relative_to(REPO_ROOT)} has drifted from the Pydantic "
        f"bundle models. {REGEN_HINT}"
    )


def test_dev_fixture_is_a_bundle_the_model_would_emit() -> None:
    """The frontend's contract test validates this fixture against the committed
    schema; that only means something if the fixture is a bundle the Python
    assembler could actually produce. `LoomModel` forbids extra fields, so this
    also catches a stale key the schema no longer describes."""
    fixture = json.loads(FIXTURE_PATH.read_text())
    try:
        bundle = TapestryBundle.model_validate(fixture)
    except PydanticValidationError as exc:  # pragma: no cover - failure path
        pytest.fail(
            f"{FIXTURE_PATH.relative_to(REPO_ROOT)} is not a valid TapestryBundle "
            f"— regenerate the fixture from a live graph. {exc}"
        )
    assert bundle.model_dump(by_alias=True, exclude_none=True) == fixture
