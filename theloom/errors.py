"""Typed errors carrying the CLI's structured error codes.

Rather than classifying errors by prose substring matching, every raised error
carries its code from birth; the CLI protocol layer (theloom/cli/io.py)
serializes {error, code} to stderr with exit 1.
"""

from __future__ import annotations

from typing import Literal

ErrorCode = Literal[
    "PARSE_ERROR",
    "INPUT_REQUIRED",
    "VALIDATION_ERROR",
    "NOT_FOUND",
    "OPERATION_ERROR",
    "CONFIG_ERROR",
]


class LoomError(Exception):
    """Base for all Loom errors; carries the structured CLI error code."""

    code: ErrorCode = "OPERATION_ERROR"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


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
