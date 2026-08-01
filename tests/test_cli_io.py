"""CLI JSON protocol tests.

JSON in (arg or stdin, 100 MB cap, objects only), JSON out (2-space indent to
stdout), {error, code} to stderr with exit 1. Codes come from typed exceptions,
never from prose matching.
"""

from __future__ import annotations

import io
import json

import pytest

from theloom.cli.io import format_error, parse_json_input
from theloom.errors import InputRequiredError, LoomError, ParseError, ValidationError


class FakeStdin(io.StringIO):
    def __init__(self, data: str = "", tty: bool = False) -> None:
        super().__init__(data)
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def test_argument_json_object_is_parsed() -> None:
    assert parse_json_input('{"a": 1}', stdin=FakeStdin(tty=True)) == {"a": 1}


@pytest.mark.parametrize("bad", ["[1,2]", '"text"', "42", "null"])
def test_non_object_json_is_a_parse_error(bad: str) -> None:
    with pytest.raises(ParseError) as excinfo:
        parse_json_input(bad, stdin=FakeStdin(tty=True))
    assert "must be an object" in str(excinfo.value)


def test_invalid_json_is_a_parse_error() -> None:
    with pytest.raises(ParseError):
        parse_json_input("{not json", stdin=FakeStdin(tty=True))


def test_stdin_is_read_when_no_argument() -> None:
    assert parse_json_input(None, stdin=FakeStdin('{"b": 2}')) == {"b": 2}


def test_empty_allowed_returns_empty_object() -> None:
    assert parse_json_input(None, allow_empty=True, stdin=FakeStdin(tty=True)) == {}
    assert parse_json_input("", allow_empty=True, stdin=FakeStdin(tty=True)) == {}


def test_no_input_when_required_raises() -> None:
    with pytest.raises(InputRequiredError):
        parse_json_input(None, stdin=FakeStdin(tty=True))
    with pytest.raises(InputRequiredError):
        parse_json_input(None, stdin=FakeStdin(""))  # piped but blank


def test_stdin_size_cap_enforced() -> None:
    big = '{"pad": "' + "x" * 200 + '"}'
    with pytest.raises(LoomError):
        parse_json_input(None, stdin=FakeStdin(big), max_bytes=100)


def test_format_error_uses_typed_codes() -> None:
    assert json.loads(format_error(ValidationError("bad input"))) == {
        "error": "bad input",
        "code": "VALIDATION_ERROR",
    }
    assert json.loads(format_error(RuntimeError("boom"))) == {
        "error": "boom",
        "code": "OPERATION_ERROR",
    }
