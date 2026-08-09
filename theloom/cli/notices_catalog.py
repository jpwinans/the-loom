"""``notices-catalog``: enumerate every notice code, its meaning, and the
commands that can emit it -- generated from source, not hand-maintained, the
same way ``COMMANDS.md`` is generated from the registry (desire 3).

The mechanism has two halves:

- ``theloom.operations.notices.NOTICE_CATALOG`` is the single source of
  *meanings*. ``notice()`` itself refuses to build a doc whose code isn't a
  catalog key (raises ``ValueError``), so a code cannot ship un-cataloged --
  a new code needs its meaning added the same commit it lands in an emitting
  call site.
- This module supplies the other half: *which* commands can actually surface
  a given code in their own response. That mapping is walked, never
  hand-listed, by a small per-command call-graph analysis over each command
  handler's own source module:

  1. a command's handler is resolved to its defining module and function
     name via the *live* function object already sitting on the registry's
     ``CommandDescriptor`` -- no separate name lookup to keep in sync;
  2. that module's source is parsed once with ``ast`` into its top-level
     functions;
  3. starting at the handler, a depth-first walk follows calls to other
     top-level functions **defined in the same module** and records every
     ``notice("CODE", ...)`` literal reached along the way.

The walk is deliberately scoped to a single module. The notices convention
(see ``notices.py``'s module docstring) is that a command attaches its *own*
notices from its *own* handler -- composites that call into another
command's handler (e.g. ``graph-reconnaissance`` calling ``detect-loops``
internally for a section of its own report) do not forward that inner
command's notices into their own response, so they must not be credited
with emitting its codes; crediting them would be a false claim about what a
caller can actually observe. Should a future command genuinely forward
another module's notice into its own response, the reachability test in
``tests/test_notices_catalog.py`` fails for that code (no command found) --
the forcing function that says "teach the analyzer, or attach the notice
from the surfacing command's own module instead."
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass
from types import ModuleType
from typing import Any

from theloom.operations.common import CommandInput
from theloom.operations.notices import NOTICE_CATALOG, Doc, list_envelope
from theloom.store.multigraph import MultiGraph


class EmptyInput(CommandInput):
    pass


@dataclass(frozen=True)
class _ModuleFunctions:
    """One module's top-level functions, plus whatever local name(s) its own
    imports bind to ``theloom.operations.notices.notice`` -- resolved from
    the module's actual import statement so an alias still works, rather
    than assuming the bare name ``notice``."""

    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef]
    notice_names: frozenset[str]


def _notice_bound_names(tree: ast.Module) -> frozenset[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "theloom.operations.notices":
            for alias in node.names:
                if alias.name == "notice":
                    names.add(alias.asname or alias.name)
    return frozenset(names)


def _parse_module(module: ModuleType) -> _ModuleFunctions:
    source = inspect.getsource(module)
    tree = ast.parse(source)
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    return _ModuleFunctions(functions=functions, notice_names=_notice_bound_names(tree))


def _direct_notice_codes(node: ast.AST, notice_names: frozenset[str]) -> set[str]:
    """Notice codes this function emits directly: literal string first
    arguments to a call of one of the module's ``notice`` bindings. A
    non-literal first argument (a variable, an f-string) can't be attributed
    statically and is deliberately not counted -- every current call site
    passes a literal, and the reachability test would catch a code that
    became unattributable this way (it would simply stop appearing)."""
    codes: set[str] = set()
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        if isinstance(func, ast.Name) and func.id in notice_names and call.args:
            first = call.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                codes.add(first.value)
    return codes


def _called_local_functions(node: ast.AST, known: dict[str, Any]) -> set[str]:
    """Bare-name calls to other top-level functions defined in the same
    module (private helpers like ``_dry_run_notice``) -- attribute calls
    (``store.read_entity(...)``, ``other_module.fn(...)``) are deliberately
    not followed; see the module docstring for why cross-module reachability
    is out of scope."""
    called: set[str] = set()
    for call in ast.walk(node):
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id in known:
            called.add(call.func.id)
    return called


def _reachable_codes(module_functions: _ModuleFunctions, start: str) -> set[str]:
    if start not in module_functions.functions:
        return set()
    seen: set[str] = set()
    stack = [start]
    codes: set[str] = set()
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        node = module_functions.functions[name]
        codes |= _direct_notice_codes(node, module_functions.notice_names)
        stack.extend(_called_local_functions(node, module_functions.functions) - seen)
    return codes


def build_catalog() -> list[Doc]:
    """Every notice code, its meaning, and the sorted list of command names
    that can emit it -- walked from the live registry every call, never
    hand-listed. Codes with no reachable command are still listed (their
    ``commands`` array is empty) rather than silently dropped; the
    registry-walking test asserts that never happens for a real build."""
    from theloom.cli.registry import COMMANDS  # deferred: breaks the import cycle

    module_cache: dict[ModuleType, _ModuleFunctions] = {}
    code_to_commands: dict[str, set[str]] = {}
    for descriptor in COMMANDS:
        handler = descriptor.handler
        if handler is None or getattr(handler, "__name__", "") == "<lambda>":
            continue
        module = inspect.getmodule(handler)
        if module is None:
            continue
        if module not in module_cache:
            module_cache[module] = _parse_module(module)
        for code in _reachable_codes(module_cache[module], handler.__name__):
            code_to_commands.setdefault(code, set()).add(descriptor.name)

    return [
        {
            "code": code,
            "meaning": meaning,
            "commands": sorted(code_to_commands.get(code, ())),
        }
        for code, meaning in sorted(NOTICE_CATALOG.items())
    ]


def notices_catalog(_: EmptyInput, _multi: MultiGraph) -> Doc:
    """List every notice code this build of The Loom can emit, its meaning,
    and which commands can surface it in their own response -- generated
    from source (the registry plus each emitting module's own code), never
    hand-maintained, so a new code lands in the catalog the same commit it
    lands in code.

    Also carries an ``alerts`` section: since-last-session's calibration/
    dream-expiry alert codes (``theloom.composites.alerts.alert_catalog``)
    are a sibling vocabulary that deliberately sits outside ``notice()``/
    ``NOTICE_CATALOG`` (see that module's docstring), so they get no
    reachability walk here -- just the same discoverability, hand-kept.
    """
    envelope = list_envelope(build_catalog())

    from theloom.composites.alerts import alert_catalog  # deferred: same reason as COMMANDS above

    envelope["alerts"] = alert_catalog()
    return envelope
