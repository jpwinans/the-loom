"""CLI JSON protocol.

JSON in from the positional argument or stdin (100 MB cap);
JSON out to stdout with 2-space indentation; ``{error, code}`` to stderr and
exit 1 on failure. Codes come from typed exceptions — anything untyped is an
OPERATION_ERROR.
"""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from theloom.errors import InputRequiredError, LoomError, OperationError, ParseError

MAX_STDIN_BYTES = 100 * 1024 * 1024


def _parse_object(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError(f"Invalid JSON input: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ParseError("JSON input must be an object, not an array, string, or primitive")
    return parsed


def parse_json_input(
    json_arg: str | None,
    allow_empty: bool = False,
    *,
    stdin: TextIO | None = None,
    max_bytes: int = MAX_STDIN_BYTES,
) -> dict[str, Any]:
    """Parse JSON input: argument, else piped stdin, else {} / error."""
    if json_arg:
        return _parse_object(json_arg)

    source = stdin if stdin is not None else sys.stdin
    if not source.isatty():
        data = source.read(max_bytes + 1)
        if len(data.encode("utf-8", errors="ignore")) > max_bytes:
            raise OperationError(
                f"Stdin input exceeds maximum size of {max_bytes / 1024 / 1024} MB"
            )
        if data.strip():
            return _parse_object(data)

    if allow_empty:
        return {}
    raise InputRequiredError("No input provided. Pass JSON as argument or pipe via stdin.")


def _jsonify(value: Any) -> Any:
    """JSON.stringify semantics: non-finite numbers serialize as null."""
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return None
    if isinstance(value, dict):
        return {key: _jsonify(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonify(item) for item in value]
    return value


def format_success(result: Any) -> str:
    """JSON.stringify(result, null, 2) equivalent (Infinity/NaN -> null)."""
    return json.dumps(_jsonify(result), indent=2, ensure_ascii=False)


def format_error(error: BaseException) -> str:
    """One-line {error, code} document, code from the typed exception.

    ``details`` is included as a third key only when the raising error set
    one (see ``LoomError.details``) — an additive field, so an error that
    never populates it keeps the exact ``{error, code}`` shape.
    """
    if isinstance(error, LoomError):
        doc: dict[str, Any] = {"error": error.message, "code": error.code}
        if error.details:
            doc["details"] = error.details
        return json.dumps(doc, ensure_ascii=False)
    return json.dumps({"error": str(error), "code": "OPERATION_ERROR"}, ensure_ascii=False)


def output_success(result: Any) -> None:
    sys.stdout.write(format_success(result) + "\n")


def output_error(error: BaseException) -> None:
    sys.stderr.write(format_error(error) + "\n")
