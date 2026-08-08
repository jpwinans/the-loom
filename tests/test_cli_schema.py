"""``theloom.cli.schema`` — the one place that walks a command's Pydantic
JSON Schema, shared by the COMMANDS.md field-table generator and by
validation-error enrichment.

No FalkorDB involved: pure schema introspection over pydantic models already
defined in the operations layer.
"""

from __future__ import annotations

import pydantic
import pytest

from theloom.cli.schema import (
    FieldRow,
    describe_validation_error,
    field_rows,
    field_schema_fragment,
    top_level_required,
    type_str,
)
from theloom.errors import ValidationError
from theloom.operations.entity import CreateEntityInput
from theloom.operations.relations import CreateRelationInput, CreateRelationsInput


def test_field_rows_covers_every_top_level_field() -> None:
    rows = field_rows(CreateEntityInput)
    paths = {row.path for row in rows}
    assert {"name", "entityType", "observations", "confidence", "provenance", "graph"} <= paths


def test_field_rows_flattens_nested_objects_with_dotted_paths() -> None:
    rows = {row.path: row for row in field_rows(CreateEntityInput)}
    assert "confidence.score" in rows
    assert "confidence.basis" in rows
    assert "confidence" in rows
    # The nested field's own required-ness is scoped to its parent object: score
    # and basis carry no default inside ConfidenceArg, so they are required
    # *if* confidence is supplied at all, even though confidence itself is optional.
    assert rows["confidence"].required is False
    assert rows["confidence.score"].required is True
    assert rows["confidence.basis"].required is True


def test_field_rows_flattens_array_of_object_with_bracket_notation() -> None:
    rows = {row.path: row for row in field_rows(CreateRelationsInput)}
    assert "relations" in rows
    assert "relations[].from" in rows
    assert "relations[].relationType" in rows
    assert rows["relations[].from"].required is True


def test_field_rows_reports_required_flag_from_schema() -> None:
    rows = {row.path: row for row in field_rows(CreateRelationInput)}
    assert rows["from"].required is True
    assert rows["to"].required is True
    assert rows["graph"].required is False


def test_field_rows_reports_default_when_present() -> None:
    rows = {row.path: row for row in field_rows(CreateRelationInput)}
    assert rows["graph"].has_default is True
    assert rows["graph"].default is None


def test_field_rows_reports_enum_values_in_type() -> None:
    rows = {row.path: row for row in field_rows(CreateEntityInput)}
    assert "concept" in rows["entityType"].type
    assert "claim" in rows["entityType"].type


def test_field_rows_is_a_plain_dataclass_row() -> None:
    rows = field_rows(CreateRelationInput)
    assert all(isinstance(row, FieldRow) for row in rows)


def test_type_str_enum() -> None:
    schema = CreateEntityInput.model_json_schema()
    defs = schema.get("$defs", {})
    assert "enum(" in type_str(schema["properties"]["entityType"], defs)


def test_field_schema_fragment_resolves_simple_field() -> None:
    fragment = field_schema_fragment(CreateRelationInput, ("relationType",))
    assert fragment is not None
    assert "enum" in fragment
    assert "supports" in fragment["enum"]


def test_field_schema_fragment_resolves_nested_field() -> None:
    fragment = field_schema_fragment(CreateEntityInput, ("confidence", "basis"))
    assert fragment is not None
    assert "enum" in fragment


def test_field_schema_fragment_resolves_through_array_index() -> None:
    fragment = field_schema_fragment(CreateRelationsInput, ("relations", 0, "strength"))
    assert fragment is not None
    assert "enum" in fragment
    assert "weak" in fragment["enum"]


def test_field_schema_fragment_none_when_path_does_not_exist() -> None:
    assert field_schema_fragment(CreateRelationInput, ("nonexistentField",)) is None


def test_top_level_required_lists_required_fields() -> None:
    required = top_level_required(CreateRelationInput)
    assert set(required) >= {"from", "to", "relationType", "strength"}
    assert "graph" not in required


def test_describe_validation_error_names_field_and_expected_shape() -> None:
    try:
        CreateEntityInput.model_validate({"name": "x", "entityType": "bogus", "observations": []})
    except pydantic.ValidationError as exc:
        error = describe_validation_error(CreateEntityInput, exc, command="create-entity")
    else:
        pytest.fail("expected a pydantic ValidationError")

    assert isinstance(error, ValidationError)
    assert error.code == "VALIDATION_ERROR"
    assert "entityType" in error.message
    assert "concept" in error.message  # an allowed enum value is echoed
    assert "create-entity --schema" in error.message
    assert error.details is not None
    assert error.details[0]["field"] == "entityType"
    assert "expected" in error.details[0]
    assert "enum" in error.details[0]["expected"]


def test_describe_validation_error_reports_every_offending_field() -> None:
    try:
        CreateRelationInput.model_validate({"strength": "bogus"})
    except pydantic.ValidationError as exc:
        error = describe_validation_error(CreateRelationInput, exc, command="create-relation")
    else:
        pytest.fail("expected a pydantic ValidationError")

    fields = {entry["field"] for entry in error.details or []}
    assert {"from", "to", "strength"} <= fields
