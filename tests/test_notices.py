"""The shared notice/``applied`` convention (TL-486, foundation for the
sibling tickets TL-481–485 and TL-472): the one reusable mechanism for a
command response to say plainly what actually happened, instead of shipping
success-shaped output for something that didn't happen.

``theloom.operations.notices`` is the single implementation; this file both
unit-tests its two helpers and proves — by driving a real command through the
registry's ``run_handler`` and the CLI's own JSON formatter — that a command
response built with them survives to the CLI's stdout JSON unchanged.
"""

from __future__ import annotations

import json

from theloom.cli.io import format_success
from theloom.cli.registry import _BY_NAME as REGISTRY_BY_NAME
from theloom.cli.registry import CommandDescriptor, run_handler
from theloom.operations.common import CommandInput
from theloom.operations.notices import notice, with_notices


def test_notice_builds_a_structured_code_message_hint_doc() -> None:
    doc = notice("NOT_PERSISTED", "Results were not written.", hint="Pass persist: true.")
    assert doc == {
        "code": "NOT_PERSISTED",
        "message": "Results were not written.",
        "hint": "Pass persist: true.",
    }


def test_notice_omits_hint_key_when_not_given() -> None:
    doc = notice("PARAM_IGNORED", "graph was ignored for this command.")
    assert doc == {"code": "PARAM_IGNORED", "message": "graph was ignored for this command."}
    assert "hint" not in doc


def test_with_notices_is_additive_and_leaves_a_clean_result_untouched() -> None:
    result = {"entity": {"id": "x"}}
    out = with_notices(result)
    assert out == result
    assert out is not result  # a copy, not a mutation of the caller's dict
    assert "notices" not in out
    assert "applied" not in out


def test_with_notices_attaches_notices_only_when_non_empty() -> None:
    result = {"count": 3}
    assert "notices" not in with_notices(result, notices=[])
    n = [notice("NOT_PERSISTED", "nothing written")]
    assert with_notices(result, notices=n)["notices"] == n


def test_with_notices_attaches_applied_only_when_explicitly_given() -> None:
    result = {"count": 3}
    assert "applied" not in with_notices(result)
    assert with_notices(result, applied=False)["applied"] is False
    assert with_notices(result, applied=True)["applied"] is True


def test_with_notices_does_not_mutate_the_input_dict() -> None:
    result = {"count": 1}
    with_notices(result, notices=[notice("X", "y")], applied=False)
    assert result == {"count": 1}


# =============================================================================
# End-to-end: a command response carrying notices/applied through the CLI's
# own JSON formatter, via the real registry dispatch path (run_handler).
# =============================================================================


class _EchoWithNoticesInput(CommandInput):
    persist: bool = False


def _echo_with_notices(params: _EchoWithNoticesInput, _multi: object) -> dict[str, object]:
    """A stand-in mutating handler: it "computes" a result and only "applies"
    it when asked, exactly the shape TL-482/TL-472 exist to fix on the real
    commands — exercised here without touching any of their behavior."""
    result = {"computed": 42}
    if params.persist:
        return with_notices(result, applied=True)
    return with_notices(
        result,
        notices=[
            notice(
                "NOT_PERSISTED",
                "Result was computed but not written.",
                hint="Pass persist: true to write it.",
            )
        ],
        applied=False,
    )


def test_notices_and_applied_survive_run_handler_and_cli_json_formatting() -> None:
    """Registers a throwaway descriptor directly in the registry's lookup
    table (never in COMMANDS, so it never appears in --help/--schema/the
    catalog) purely to drive the real dispatch + formatting path end to end."""
    name = "__test-echo-with-notices__"
    descriptor = CommandDescriptor(
        name=name,
        category="Test",
        summary="test fixture",
        input_model=_EchoWithNoticesInput,
        handler=_echo_with_notices,
        allow_empty=True,
    )
    REGISTRY_BY_NAME[name] = descriptor
    try:
        dry = run_handler(name, {}, multi=object())  # type: ignore[arg-type]
        assert dry == {
            "computed": 42,
            "notices": [
                {
                    "code": "NOT_PERSISTED",
                    "message": "Result was computed but not written.",
                    "hint": "Pass persist: true to write it.",
                }
            ],
            "applied": False,
        }
        stdout = format_success(dry)
        reparsed = json.loads(stdout)
        assert reparsed["applied"] is False
        assert reparsed["notices"][0]["code"] == "NOT_PERSISTED"

        applied = run_handler(name, {"persist": True}, multi=object())  # type: ignore[arg-type]
        assert applied == {"computed": 42, "applied": True}
        assert "notices" not in applied
        assert json.loads(format_success(applied))["applied"] is True
    finally:
        del REGISTRY_BY_NAME[name]
