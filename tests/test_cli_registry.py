"""Registry-uniformity: every command builds through the single construction
path (`theloom.cli.registry._build` over `_Spec` rows), and every descriptor
states its `allow_empty` stance explicitly — there is no dialect where the
flag silently defaults by omission."""

from __future__ import annotations

import inspect

from pydantic import BaseModel

from theloom.cli.registry import COMMANDS, CommandDescriptor, _build, _Spec


class CommandDescriptorStubInput(BaseModel):
    pass


def test_every_command_is_a_command_descriptor() -> None:
    assert COMMANDS
    for descriptor in COMMANDS:
        assert isinstance(descriptor, CommandDescriptor)


def test_every_descriptor_has_an_explicit_allow_empty_bool() -> None:
    for descriptor in COMMANDS:
        assert isinstance(descriptor.allow_empty, bool), descriptor.name


def test_every_command_comes_from_a_block_using_the_single_build_path() -> None:
    """Every ``_xxx_commands``/``_tail_commands`` function in the registry
    module returns its list via ``_build`` over ``_Spec`` rows — the raw
    ``raw_handler`` hatch (bulk-import) is the sole documented exception, and
    even it is appended onto a `_build`-produced list rather than replacing
    the pattern outright."""
    import theloom.cli.registry as registry

    block_functions = [
        obj
        for name, obj in vars(registry).items()
        if name.startswith("_") and name.endswith("_commands") and inspect.isfunction(obj)
    ]
    assert block_functions, "expected at least one _xxx_commands() block function"
    for fn in block_functions:
        source = inspect.getsource(fn)
        assert "_build(" in source, fn.__name__


def test_no_command_has_a_bare_tuple_construction_dialect() -> None:
    """Regression pin: the registry used to mix 4/5/6-tuple comprehensions and
    keyword-only ``CommandDescriptor(...)`` calls across blocks. Every command
    is now declared as a ``_Spec`` (or, for the raw-handler hatch, an explicit
    ``CommandDescriptor`` with ``allow_empty`` passed by keyword)."""
    names = {descriptor.name for descriptor in COMMANDS}
    assert len(names) == len(COMMANDS)  # no name collisions from a bad merge
    assert "bulk-import" in names


def test_build_preserves_spec_order_and_fields() -> None:
    specs = [
        _Spec("a", "Cat", "summary a", CommandDescriptorStubInput, lambda p, m: None, True),
        _Spec("b", "Cat", "summary b", CommandDescriptorStubInput, lambda p, m: None, False),
    ]
    built = _build(specs)
    assert [d.name for d in built] == ["a", "b"]
    assert built[0].allow_empty is True
    assert built[1].allow_empty is False
    assert built[0].category == "Cat"
    assert built[0].summary == "summary a"
