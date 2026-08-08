"""Typed errors carrying the CLI's structured error codes.

Rather than classifying errors by prose substring matching, every raised error
carries its code from birth; the CLI protocol layer (theloom/cli/io.py)
serializes {error, code} to stderr with exit 1.
"""

from __future__ import annotations

from typing import Any, Literal

ErrorCode = Literal[
    "PARSE_ERROR",
    "INPUT_REQUIRED",
    "VALIDATION_ERROR",
    "NOT_FOUND",
    "OPERATION_ERROR",
    "CONFIG_ERROR",
]


class LoomError(Exception):
    """Base for all Loom errors; carries the structured CLI error code.

    ``details`` is an optional, additive list of structured fragments (e.g.
    ``{"field": ..., "message": ..., "expected": <schema fragment>}``) that
    ``theloom.cli.io.format_error`` surfaces as a ``"details"`` key when
    present — never added when absent, so existing ``{error, code}`` output
    is untouched unless a raiser opts in (see
    ``theloom.cli.schema.describe_validation_error``).
    """

    code: ErrorCode = "OPERATION_ERROR"

    def __init__(self, message: str, *, details: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class ParseError(LoomError):
    code: ErrorCode = "PARSE_ERROR"


class InputRequiredError(LoomError):
    code: ErrorCode = "INPUT_REQUIRED"


class ValidationError(LoomError):
    code: ErrorCode = "VALIDATION_ERROR"


class NotFoundError(LoomError):
    code: ErrorCode = "NOT_FOUND"


class OperationError(LoomError):
    code: ErrorCode = "OPERATION_ERROR"


class ConfigError(LoomError):
    code: ErrorCode = "CONFIG_ERROR"
