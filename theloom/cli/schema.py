"""JSON-Schema helpers shared by the command catalog generator (``docs.py``)
and by validation-error enrichment (``registry.py``'s ``run_handler``).

Every command's input contract is a Pydantic model, so
``BaseModel.model_json_schema()`` already gives its full JSON Schema for free
(nested models resolve through ``$defs``). This module is the *one* place
that walks that schema — flattening it into per-field rows for COMMANDS.md
(``field_rows``), and looking up the schema fragment at a specific
``pydantic.ValidationError`` location (``field_schema_fragment``) so a
validation failure can show the offending field's expected shape instead of
forwarding pydantic's bare prose (``describe_validation_error``).

Nothing here imports the registry or the CLI app — it depends only on
``pydantic.BaseModel`` subclasses, so both the docs generator and the
registry's error path can use it without a cycle.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast

import pydantic

from theloom.errors import ValidationError

_MAX_DEPTH = 8


@dataclass(frozen=True)
class FieldRow:
    """One row of a flattened input schema: a leaf field or an object/array
    branch, dotted-path addressed (``confidence.score``) with ``[]`` marking
    descent into an array's item schema (``relations[].from``).

    ``required`` is scoped to the field's *immediate* parent object — a
    required field of an optional nested object is only mandatory once the
    caller supplies that object at all.
    """

    path: str
    type: str
    required: bool
    has_default: bool
    default: Any
    description: str


_MISSING = object()


def _resolve_ref(node: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    ref = node.get("$ref")
    if ref is None:
        return node
    return cast(dict[str, Any], defs.get(ref.rsplit("/", 1)[-1], {}))


def _ref_name(node: dict[str, Any]) -> str | None:
    ref = node.get("$ref")
    return ref.rsplit("/", 1)[-1] if ref else None


def _effective(node: dict[str, Any], defs: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Resolve ``$ref``/nullable ``anyOf`` down to the node that actually
    carries ``type``/``enum``/``properties``, plus whether the field accepts
    ``null`` (a `` X | None`` annotation renders as ``anyOf: [X, {type: null}]``
    in pydantic v2's schema)."""
    nullable = False
    if "anyOf" in node:
        branches = node["anyOf"]
        non_null = [b for b in branches if b.get("type") != "null"]
        nullable = len(non_null) < len(branches)
        if len(non_null) == 1:
            node = non_null[0]
        elif not non_null:
            return {"type": "null"}, True
        else:
            return {"anyOf": non_null}, nullable
    return _resolve_ref(node, defs), nullable


def type_str(node: dict[str, Any], defs: dict[str, Any]) -> str:
    """A short human-readable type for one (raw, un-resolved) schema node:
    ``enum(a, b, c)``, ``array<string>``, ``object``, ``string | null``…"""
    resolved, nullable = _effective(node, defs)
    if "enum" in resolved:
        rendered = "enum(" + ", ".join(str(v) for v in resolved["enum"]) + ")"
    elif "anyOf" in resolved:
        parts = list(dict.fromkeys(type_str(branch, defs) for branch in resolved["anyOf"]))
        rendered = " | ".join(parts)
    elif resolved.get("type") == "array":
        rendered = f"array<{type_str(resolved.get('items', {}), defs)}>"
    elif resolved.get("type") == "object" and "properties" in resolved:
        rendered = "object"
    else:
        rendered = str(resolved.get("type") or "any")
    if nullable and rendered != "null" and "null" not in rendered:
        return f"{rendered} | null"
    return rendered


def field_rows(model: type[pydantic.BaseModel]) -> list[FieldRow]:
    """Flatten a command's input model into one row per field, depth-first —
    nested objects get a dotted path, arrays of objects get a ``[]`` segment.
    A cycle guard (``seen``) stops a self-referential model from recursing
    forever; ``_MAX_DEPTH`` is a hard backstop."""
    schema = model.model_json_schema()
    defs = schema.get("$defs", {})
    return _object_rows(schema, defs, prefix="", depth=0, seen=frozenset())


def _object_rows(
    node: dict[str, Any],
    defs: dict[str, Any],
    *,
    prefix: str,
    depth: int,
    seen: frozenset[str],
) -> list[FieldRow]:
    resolved, _ = _effective(node, defs)
    props: dict[str, Any] = resolved.get("properties", {})
    if not props or depth >= _MAX_DEPTH:
        return []
    required = set(resolved.get("required", []))
    rows: list[FieldRow] = []
    for name, prop in props.items():
        path = f"{prefix}{name}"
        resolved_prop, _ = _effective(prop, defs)
        default = prop.get("default", _MISSING)
        description = prop.get("description") or resolved_prop.get("description", "")
        rows.append(
            FieldRow(
                path=path,
                type=type_str(prop, defs),
                required=name in required,
                has_default=default is not _MISSING,
                default=None if default is _MISSING else default,
                description=description,
            )
        )
        rows.extend(_nested_rows(prop, defs, path=path, depth=depth, seen=seen))
    return rows


def _nested_rows(
    prop: dict[str, Any],
    defs: dict[str, Any],
    *,
    path: str,
    depth: int,
    seen: frozenset[str],
) -> list[FieldRow]:
    resolved, _ = _effective(prop, defs)
    if resolved.get("type") == "array":
        item = resolved.get("items", {})
        item_resolved, _ = _effective(item, defs)
        item_ref = _ref_name(item)
        if item_ref and item_ref in seen:
            return []
        if item_resolved.get("properties"):
            next_seen = seen | ({item_ref} if item_ref else set())
            return _object_rows(item, defs, prefix=f"{path}[].", depth=depth + 1, seen=next_seen)
        return []
    ref_name = _ref_name(prop) or _ref_name(resolved)
    if ref_name and ref_name in seen:
        return []
    if resolved.get("properties"):
        next_seen = seen | ({ref_name} if ref_name else set())
        return _object_rows(prop, defs, prefix=f"{path}.", depth=depth + 1, seen=next_seen)
    return []


def field_schema_fragment(
    model: type[pydantic.BaseModel], loc: tuple[Any, ...]
) -> dict[str, Any] | None:
    """The raw JSON-Schema node at a pydantic ``ValidationError`` error's
    ``loc`` path — the "expected shape" a validation failure should show.

    Integer path components (list indices) descend into the array's
    ``items`` schema, since JSON Schema carries no per-index entry. Returns
    ``None`` when the path doesn't resolve (e.g. a root-level "extra fields
    not permitted" error has an empty ``loc``).
    """
    schema = model.model_json_schema()
    defs = schema.get("$defs", {})
    node: dict[str, Any] = schema
    for part in loc:
        resolved, _ = _effective(node, defs)
        if isinstance(part, int):
            node = resolved.get("items", {})
            continue
        props = resolved.get("properties", {})
        if part not in props:
            return None
        node = props[part]
    resolved, _ = _effective(node, defs)
    return resolved


def top_level_required(model: type[pydantic.BaseModel]) -> list[str]:
    """The command's required top-level field names, in schema order — used
    to enrich INPUT_REQUIRED/PARSE_ERROR messages with a quick pointer to
    what the caller still owes, without needing the full schema."""
    schema = model.model_json_schema()
    return list(schema.get("required", []))


def describe_validation_error(
    model: type[pydantic.BaseModel], exc: pydantic.ValidationError, *, command: str
) -> ValidationError:
    """Turn a raw pydantic ``ValidationError`` into the CLI's typed
    ``ValidationError``: a message that names each offending field and echoes
    the JSON-Schema fragment it must satisfy, plus a matching ``details`` list
    for programmatic consumers — instead of forwarding pydantic's bare prose.
    """
    details: list[dict[str, Any]] = []
    lines: list[str] = []
    for error in exc.errors():
        loc = error["loc"]
        field = ".".join(str(part) for part in loc) if loc else "(top level)"
        fragment = field_schema_fragment(model, loc)
        entry: dict[str, Any] = {"field": field, "message": error["msg"]}
        line = f"'{field}': {error['msg']}"
        if fragment is not None:
            entry["expected"] = fragment
            line += f" — expected {json.dumps(fragment, sort_keys=True)}"
        details.append(entry)
        lines.append(line)
    message = "; ".join(lines) + f". Run `loom {command} --schema` for the complete input schema."
    return ValidationError(message, details=details)
