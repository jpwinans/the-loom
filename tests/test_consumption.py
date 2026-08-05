"""Consumption commands: explore, find-callers/find-callees, blast-radius.

These are the one-call comprehension answers. What is pinned here is not just
the happy path but the *honesty* of the outputs under pressure:

- a budget cut degrades breadth evenly — every non-empty section keeps at least
  one row, the queried entity's own row is never cut, and what was dropped is
  rolled up grouped-by-file instead of disappearing;
- the truncation block's numbers add up (shown + cut == total) and say how to
  widen;
- blast-radius traverses only the curated allowlist, honours its depth cap,
  seeds a class's members, and reports the hubs it refused to expand through.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from theloom.cli.registry import COMMANDS, run_handler
from theloom.errors import ValidationError
from theloom.model import RelationCreate
from theloom.store.multigraph import MultiGraph


def ent(
    multi: MultiGraph,
    name: str,
    entity_type: str = "procedure",
    observations: list[str] | None = None,
) -> str:
    doc: dict[str, Any] = {
        "name": name,
        "entityType": entity_type,
        "observations": observations if observations is not None else [name],
    }
    result = run_handler("create-entity", doc, multi)
    return str(result["id"])


def rel(
    multi: MultiGraph,
    from_id: str,
    to_id: str,
    relation_type: str,
    evidence: str | None = None,
) -> None:
    run_handler(
        "create-relation",
        {
            "from": from_id,
            "to": to_id,
            "relationType": relation_type,
            "polarity": None,
            "strength": "moderate",
            "evidence": evidence or f"{from_id} {relation_type} {to_id}",
        },
        multi,
    )


def symbol(multi: MultiGraph, name: str, path: str, lines: str = "1-9") -> str:
    return ent(
        multi,
        name,
        "procedure",
        [f"File path: {path}", f"Line range: {lines}", "Symbol kind: Function"],
    )


def file_entity(multi: MultiGraph, path: str) -> str:
    return ent(
        multi,
        f"file:{path}",
        "system",
        [f"File path: {path}", "Language: python", "Symbol kind: File"],
    )


# =============================================================================
# explore
# =============================================================================


@pytest.fixture()
def code(multi: MultiGraph) -> dict[str, str]:
    """A miniature extracted codebase plus one semantic-layer claim."""
    ids = {
        "file_a": file_entity(multi, "theloom/a.py"),
        "file_c": file_entity(multi, "theloom/c.py"),
        "run": symbol(multi, "run (a.py)", "theloom/a.py", "10-20"),
        "helper": symbol(multi, "helper (c.py)", "theloom/c.py", "3-8"),
        "caller": symbol(multi, "caller_one (b.py)", "theloom/b.py", "5-30"),
        "runner": ent(
            multi,
            "Runner (a.py)",
            "concept",
            ["File path: theloom/a.py", "Line range: 30-60", "Symbol kind: Class"],
        ),
        "base": ent(
            multi,
            "Base (base.py)",
            "concept",
            ["File path: theloom/base.py", "Line range: 1-40", "Symbol kind: Class"],
        ),
        "claim": ent(
            multi,
            "Mutations append events",
            "claim",
            [
                "map_layer: semantic",
                "module_group: ops",
                "statement: every mutation appends an event",
                "anchor: theloom/a.py:10",
            ],
        ),
    }
    rel(multi, ids["run"], ids["file_a"], "part_of")
    rel(multi, ids["runner"], ids["file_a"], "part_of")
    rel(multi, ids["caller"], ids["run"], "calls", "caller_one calls run at theloom/b.py:12")
    rel(multi, ids["run"], ids["helper"], "calls", "run calls helper at theloom/a.py:15")
    rel(multi, ids["file_a"], ids["file_c"], "requires", "a.py imports c.py")
    rel(multi, ids["runner"], ids["base"], "instance_of", "Runner extends Base")
    rel(multi, ids["claim"], ids["file_a"], "related_to", "grounds the claim")
    return ids


def test_explore_answers_a_symbol_in_one_call(multi: MultiGraph, code: dict[str, str]) -> None:
    result = run_handler("explore", {"name": "run (a.py)"}, multi)

    assert result["entity"]["id"] == code["run"]
    assert result["entity"]["name"] == "run (a.py)"
    assert result["definition"] == "theloom/a.py:10-20"

    assert result["callersIn"]["total"] == 1
    assert result["callersIn"]["shown"][0] == {
        "name": "caller_one (b.py)",
        "entityType": "procedure",
        "at": "theloom/b.py:12",
        "file": "theloom/b.py",
    }
    assert result["callsOut"]["shown"][0]["name"] == "helper (c.py)"
    assert result["callsOut"]["shown"][0]["at"] == "theloom/a.py:15"
    assert result["partOf"]["shown"][0]["name"] == "file:theloom/a.py"
    assert result["imports"]["total"] == 0
    assert result["inheritance"]["total"] == 0

    # The semantic layer hangs off the FILE, so a symbol must reach it too.
    assert [s["name"] for s in result["semantic"]["shown"]] == ["Mutations append events"]
    assert result["semantic"]["shown"][0]["anchor"] == "statement: every mutation appends an event"

    assert result["truncation"]["applied"] is False
    assert result["truncation"]["shown"] == result["truncation"]["total"]


def test_explore_drops_retired_neighbours(multi: MultiGraph, code: dict[str, str]) -> None:
    """Updates invalidate; they never overwrite. A superseded neighbour has left
    the current projection, so it must leave every neighbourhood read too —
    otherwise explore reports retired state as live and contradicts the
    resolver, which is active-only."""
    run_handler("update-entity", {"id": code["helper"], "status": "superseded"}, multi)
    run_handler("update-entity", {"id": code["claim"], "status": "superseded"}, multi)

    result = run_handler("explore", {"name": "run (a.py)"}, multi)
    assert result["callsOut"]["total"] == 0
    assert result["callsOut"]["shown"] == []
    assert result["semantic"]["total"] == 0
    # ... and what is still live is untouched.
    assert [row["name"] for row in result["callersIn"]["shown"]] == ["caller_one (b.py)"]


def test_explore_reports_recursion_once_in_each_direction(multi: MultiGraph) -> None:
    """A self-edge is direct recursion: one call out, one caller in — and the
    "both" store read returns it twice, which must not become a phantom row.

    The create-relation *command* gates self-edges away, but the codebase
    extractor writes call edges through ``store.create_relation`` directly, so
    this is the shape recursion actually takes in an extracted graph.
    """
    fact = symbol(multi, "factorial (m.py)", "theloom/m.py", "1-5")
    multi.get_store(None).create_relation(
        RelationCreate.model_validate(
            {
                "from": fact,
                "to": fact,
                "relationType": "calls",
                "strength": "moderate",
                "evidence": "factorial calls factorial at theloom/m.py:3",
            }
        )
    )

    result = run_handler("explore", {"name": "factorial (m.py)"}, multi)
    assert result["callsOut"]["total"] == 1
    assert result["callersIn"]["total"] == 1
    assert result["callsOut"]["shown"][0]["name"] == "factorial (m.py)"
    assert result["callersIn"]["shown"][0]["name"] == "factorial (m.py)"
    # ... and the dedicated call lists agree with explore.
    callers = run_handler("find-callers", {"name": "factorial (m.py)"}, multi)["callers"]
    callees = run_handler("find-callees", {"name": "factorial (m.py)"}, multi)["callees"]
    assert len(callers) == 1
    assert len(callees) == 1


def test_explore_of_a_file_reports_imports_and_contents(
    multi: MultiGraph, code: dict[str, str]
) -> None:
    result = run_handler("explore", {"name": "file:theloom/a.py"}, multi)
    assert [row["name"] for row in result["imports"]["shown"]] == ["file:theloom/c.py"]
    assert sorted(row["name"] for row in result["contains"]["shown"]) == [
        "Runner (a.py)",
        "run (a.py)",
    ]
    assert result["semantic"]["total"] == 1


def test_explore_inheritance_records_which_way_it_points(
    multi: MultiGraph, code: dict[str, str]
) -> None:
    result = run_handler("explore", {"name": "Runner (a.py)"}, multi)
    assert result["inheritance"]["shown"] == [
        {
            "name": "Base (base.py)",
            "entityType": "concept",
            "kind": "extends",
            "file": "theloom/base.py",
        }
    ]
    base = run_handler("explore", {"name": "Base (base.py)"}, multi)
    assert base["inheritance"]["shown"][0]["kind"] == "extendedBy"


def _wide_graph(multi: MultiGraph) -> str:
    """A symbol with a wide neighbourhood in every direction."""
    seed = symbol(multi, "hot_spot (core.py)", "theloom/core.py", "1-100")
    file_seed = file_entity(multi, "theloom/core.py")
    rel(multi, seed, file_seed, "part_of")
    for index in range(12):
        caller = symbol(multi, f"caller_{index:02d} (up.py)", "theloom/callers/up.py", "1-2")
        site = f"theloom/callers/up.py:{index + 1}"
        rel(multi, caller, seed, "calls", f"caller_{index:02d} calls hot_spot at {site}")
        callee = symbol(multi, f"callee_{index:02d} (down.py)", "theloom/callees/down.py", "1-2")
        call = f"hot_spot calls callee_{index:02d} at theloom/core.py:{index + 1}"
        rel(multi, seed, callee, "calls", call)
        claim = ent(
            multi,
            f"invariant {index:02d}",
            "claim",
            ["map_layer: semantic", f"statement: invariant number {index:02d} holds everywhere"],
        )
        rel(multi, claim, seed, "related_to", "grounds")
    return seed


#: Big enough that the round-robin allocator actually runs — below the fixed
#: overhead every section collapses to its unconditional first row, and an
#: evenness assertion over [1, 1, 1] pins nothing.
WIDE_BUDGET = 500


def test_explore_budget_degrades_breadth_evenly(multi: MultiGraph) -> None:
    _wide_graph(multi)
    result = run_handler("explore", {"name": "hot_spot (core.py)", "budget": WIDE_BUDGET}, multi)

    truncation = result["truncation"]
    assert truncation["applied"] is True
    assert truncation["shown"] < truncation["total"]
    assert truncation["shown"] + sum(truncation["cut"].values()) == truncation["total"]
    assert "budget" in truncation["hint"]

    # Every populated section survives: breadth degrades, sections do not vanish.
    for key in ("callersIn", "callsOut", "partOf", "semantic"):
        assert result[key]["shown"], key
    # ... and no section hogged the whole budget. The allocator must have run
    # past the unconditional first row, or evenness here would be vacuous.
    counts = [len(result[key]["shown"]) for key in ("callersIn", "callsOut", "semantic")]
    assert min(counts) >= 2
    assert max(counts) - min(counts) <= 1


def test_explore_budget_floor_is_one_row_per_section(multi: MultiGraph) -> None:
    """Below the fixed overhead there is nothing left to allocate: every
    populated section keeps exactly its first row, and nothing more."""
    _wide_graph(multi)
    result = run_handler("explore", {"name": "hot_spot (core.py)", "budget": 100}, multi)
    for key in ("callersIn", "callsOut", "partOf", "semantic"):
        assert len(result[key]["shown"]) == 1, key


def test_explore_rolls_up_what_it_dropped(multi: MultiGraph) -> None:
    _wide_graph(multi)
    result = run_handler("explore", {"name": "hot_spot (core.py)", "budget": WIDE_BUDGET}, multi)

    callers = result["callersIn"]
    assert callers["total"] == 12
    dropped = callers["total"] - len(callers["shown"])
    assert dropped > 0
    assert sum(entry["count"] for entry in callers["byFile"]) == dropped
    assert callers["byFile"][0]["file"] == "theloom/callers/up.py"


def test_explore_rolls_up_semantic_by_type(multi: MultiGraph) -> None:
    """A claim lives in no file, so its rollup groups by type, not by file."""
    _wide_graph(multi)
    semantic = run_handler("explore", {"name": "hot_spot (core.py)", "budget": WIDE_BUDGET}, multi)[
        "semantic"
    ]
    assert "byFile" not in semantic
    assert semantic["byType"] == [
        {"entityType": "claim", "count": semantic["total"] - len(semantic["shown"])}
    ]


@pytest.mark.parametrize("budget", [500, 1000, 2000])
def test_explore_stays_inside_the_budget_it_can_meet(multi: MultiGraph, budget: int) -> None:
    _wide_graph(multi)
    result = run_handler("explore", {"name": "hot_spot (core.py)", "budget": budget}, multi)
    assert len(json.dumps(result, separators=(",", ":"))) <= budget * 4


def test_explore_admits_when_the_floor_exceeds_the_budget(multi: MultiGraph) -> None:
    """One row per section plus the entity is the smallest honest answer; when
    even that overruns the budget, the block says so instead of pretending."""
    _wide_graph(multi)
    result = run_handler("explore", {"name": "hot_spot (core.py)", "budget": 20}, multi)
    assert "smallest honest answer" in result["truncation"]["hint"]
    assert result["callersIn"]["shown"]


def test_explore_never_cuts_the_queried_entity(multi: MultiGraph) -> None:
    seed = _wide_graph(multi)
    result = run_handler("explore", {"entityId": seed, "budget": 100}, multi)
    assert result["entity"]["id"] == seed
    assert result["entity"]["observations"] == [
        "File path: theloom/core.py",
        "Line range: 1-100",
        "Symbol kind: Function",
    ]
    assert result["definition"] == "theloom/core.py:1-100"
    assert result["truncation"]["applied"] is True


def test_explore_refuses_ambiguity_and_demands_exactly_one(multi: MultiGraph) -> None:
    symbol(multi, "run_a (a.py)", "theloom/a.py")
    symbol(multi, "run_b (b.py)", "theloom/b.py")
    with pytest.raises(ValidationError) as excinfo:
        run_handler("explore", {"name": "run_"}, multi)
    assert excinfo.value.code == "VALIDATION_ERROR"
    assert "run_a (a.py)" in str(excinfo.value)
    with pytest.raises(ValidationError):
        run_handler("explore", {}, multi)


# =============================================================================
# find-callers / find-callees
# =============================================================================


def test_find_callers_anchors_each_row_at_its_call_site(
    multi: MultiGraph, code: dict[str, str]
) -> None:
    result = run_handler("find-callers", {"name": "run (a.py)"}, multi)
    assert result["entity"]["id"] == code["run"]
    assert result["callers"] == [
        {
            "name": "caller_one (b.py)",
            "entityType": "procedure",
            "at": "theloom/b.py:12",
            "file": "theloom/b.py",
        }
    ]
    assert result["truncation"] == {
        "applied": False,
        "shown": 1,
        "total": 1,
        "hint": "Nothing was cut.",
    }


def test_find_callees_lists_the_other_direction(multi: MultiGraph, code: dict[str, str]) -> None:
    result = run_handler("find-callees", {"name": "run (a.py)"}, multi)
    assert [row["name"] for row in result["callees"]] == ["helper (c.py)"]
    assert result["callees"][0]["file"] == "theloom/c.py"
    assert result["callees"][0]["at"] == "theloom/a.py:15"


def test_find_callers_rolls_up_by_file_past_the_cap(multi: MultiGraph) -> None:
    seed = symbol(multi, "target (t.py)", "theloom/t.py")
    for index in range(8):
        path = "theloom/x.py" if index < 5 else "theloom/y.py"
        caller = symbol(multi, f"c{index} ({path})", path)
        rel(multi, caller, seed, "calls", f"c{index} calls target at {path}:{index + 1}")

    result = run_handler("find-callers", {"name": "target (t.py)", "limit": 3}, multi)
    assert len(result["callers"]) == 3
    truncation = result["truncation"]
    assert truncation == {
        "applied": True,
        "shown": 3,
        "total": 8,
        "hint": truncation["hint"],
    }
    assert "limit" in truncation["hint"]
    assert sum(entry["count"] for entry in result["byFile"]) == 5


def test_find_callers_drops_retired_callers(multi: MultiGraph, code: dict[str, str]) -> None:
    """A superseded symbol is not a live caller — and explore of it raises
    NOT_FOUND, so listing it here would make the surface contradict itself."""
    run_handler("update-entity", {"id": code["caller"], "status": "superseded"}, multi)
    result = run_handler("find-callers", {"name": "run (a.py)"}, multi)
    assert result["callers"] == []
    assert result["truncation"]["total"] == 0


def test_find_callers_refuses_ambiguity(multi: MultiGraph) -> None:
    symbol(multi, "dup_one (a.py)", "theloom/a.py")
    symbol(multi, "dup_two (b.py)", "theloom/b.py")
    with pytest.raises(ValidationError):
        run_handler("find-callers", {"name": "dup_"}, multi)
    with pytest.raises(ValidationError):
        run_handler("find-callees", {}, multi)


# =============================================================================
# blast-radius
# =============================================================================


@pytest.fixture()
def chain(multi: MultiGraph) -> dict[str, str]:
    """Widget.run is a method of Widget; alpha calls it, beta calls alpha,
    gamma calls beta. delta is merely related_to Widget (not a dependency)."""
    ids = {
        "widget": ent(
            multi,
            "Widget (w.py)",
            "concept",
            ["File path: theloom/core/w.py", "Line range: 1-50", "Symbol kind: Class"],
        ),
        "method": symbol(multi, "Widget.run (w.py)", "theloom/core/w.py", "10-20"),
        "alpha": symbol(multi, "alpha (a.py)", "theloom/api/a.py"),
        "beta": symbol(multi, "beta (b.py)", "theloom/api/b.py"),
        "gamma": symbol(multi, "gamma (c.py)", "theloom/cli/c.py"),
        "delta": symbol(multi, "delta (d.py)", "theloom/cli/d.py"),
        "sub": ent(
            multi,
            "SubWidget (s.py)",
            "concept",
            ["File path: theloom/core/s.py", "Line range: 1-9", "Symbol kind: Class"],
        ),
    }
    rel(multi, ids["method"], ids["widget"], "part_of")
    rel(multi, ids["alpha"], ids["method"], "calls", "alpha calls Widget.run at theloom/api/a.py:3")
    rel(multi, ids["beta"], ids["alpha"], "calls", "beta calls alpha at theloom/api/b.py:4")
    rel(multi, ids["gamma"], ids["beta"], "calls", "gamma calls beta at theloom/cli/c.py:5")
    rel(multi, ids["delta"], ids["widget"], "related_to", "mentions it")
    rel(multi, ids["sub"], ids["widget"], "instance_of", "SubWidget extends Widget")
    return ids


def test_blast_radius_reaches_through_members_and_groups_by_module(
    multi: MultiGraph, chain: dict[str, str]
) -> None:
    result = run_handler("blast-radius", {"name": "Widget (w.py)"}, multi)

    assert result["seed"]["id"] == chain["widget"]
    assert result["depth"] == 4
    assert result["relationTypes"] == ["calls", "requires", "instance_of"]

    by_module = {entry["module"]: entry for entry in result["affected"]["byModule"]}
    depths = {
        row["name"]: row["depth"] for entry in by_module.values() for row in entry["entities"]
    }
    # The method seeds the traversal (a change to the class is a change to its
    # members), so a caller bound to the method is inside the radius — but the
    # member is the change, not the fallout, and is counted as a seed instead.
    assert result["seededMembers"] == 1
    assert "Widget.run (w.py)" not in depths
    assert depths["alpha (a.py)"] == 1
    assert depths["beta (b.py)"] == 2
    assert depths["gamma (c.py)"] == 3
    assert depths["SubWidget (s.py)"] == 1
    # related_to is not a dependency: delta is untouched.
    assert "delta (d.py)" not in depths

    assert by_module["theloom/api"]["count"] == 2
    assert by_module["theloom/core"]["count"] == 1
    assert result["affected"]["total"] == 4
    assert result["truncation"]["applied"] is False


def test_blast_radius_honours_its_depth_cap(multi: MultiGraph, chain: dict[str, str]) -> None:
    result = run_handler("blast-radius", {"name": "Widget (w.py)", "depth": 1}, multi)
    names = {row["name"] for entry in result["affected"]["byModule"] for row in entry["entities"]}
    assert names == {"alpha (a.py)", "SubWidget (s.py)"}


def test_blast_radius_follows_imports(multi: MultiGraph) -> None:
    core = file_entity(multi, "theloom/core/store.py")
    importer = file_entity(multi, "theloom/api/handler.py")
    rel(multi, importer, core, "requires", "handler.py imports store.py")
    result = run_handler("blast-radius", {"name": "file:theloom/core/store.py"}, multi)
    names = {row["name"] for entry in result["affected"]["byModule"] for row in entry["entities"]}
    assert names == {"file:theloom/api/handler.py"}


def test_blast_radius_suppresses_hubs_and_says_so(multi: MultiGraph) -> None:
    seed = symbol(multi, "seed (s.py)", "theloom/core/s.py")
    hub = symbol(multi, "log (util.py)", "theloom/util/util.py")
    rel(multi, hub, seed, "calls", "log calls seed at theloom/util/util.py:1")
    for index in range(12):
        far = symbol(multi, f"far_{index:02d} (f.py)", "theloom/far/f.py")
        rel(multi, far, hub, "calls", f"far_{index:02d} calls log at theloom/far/f.py:{index + 1}")
    near = symbol(multi, "near (n.py)", "theloom/near/n.py")
    rel(multi, near, seed, "calls", "near calls seed at theloom/near/n.py:1")
    outer = symbol(multi, "outer (o.py)", "theloom/near/o.py")
    rel(multi, outer, near, "calls", "outer calls near at theloom/near/o.py:1")

    result = run_handler("blast-radius", {"name": "seed (s.py)"}, multi)
    names = {row["name"] for entry in result["affected"]["byModule"] for row in entry["entities"]}
    assert names == {"log (util.py)", "near (n.py)", "outer (o.py)"}
    assert [hub_row["name"] for hub_row in result["suppressedHubs"]] == ["log (util.py)"]
    assert result["suppressedHubs"][0]["degree"] == 13
    assert "hub" in result["truncation"]["hint"]
    # A whole subtree (the hub's 12 dependants) was excluded, so the impact
    # list is NOT complete — saying otherwise is the dishonest answer.
    assert result["truncation"]["applied"] is True


def test_blast_radius_ignores_retired_dependants(multi: MultiGraph, chain: dict[str, str]) -> None:
    """A superseded dependant is neither fallout nor a route to fallout: beta
    and gamma reach Widget only through alpha, so they leave with it."""
    run_handler("update-entity", {"id": chain["alpha"], "status": "superseded"}, multi)
    result = run_handler("blast-radius", {"name": "Widget (w.py)"}, multi)
    names = {row["name"] for entry in result["affected"]["byModule"] for row in entry["entities"]}
    assert names == {"SubWidget (s.py)"}
    assert result["affected"]["total"] == 1


def test_blast_radius_limit_spreads_across_modules(
    multi: MultiGraph, chain: dict[str, str]
) -> None:
    result = run_handler("blast-radius", {"name": "Widget (w.py)", "limit": 2}, multi)
    modules = [entry for entry in result["affected"]["byModule"]]
    assert sum(len(entry["entities"]) for entry in modules) == 2
    # Counts stay exact even when the names are cut, and breadth is spread.
    assert sum(entry["count"] for entry in modules) == 4
    assert len({entry["module"] for entry in modules if entry["entities"]}) == 2
    truncation = result["truncation"]
    assert truncation["applied"] is True
    assert truncation["shown"] == 2
    assert truncation["total"] == 4
    assert "limit" in truncation["hint"]


def test_blast_radius_refuses_ambiguity(multi: MultiGraph) -> None:
    symbol(multi, "amb_one (a.py)", "theloom/a.py")
    symbol(multi, "amb_two (b.py)", "theloom/b.py")
    with pytest.raises(ValidationError):
        run_handler("blast-radius", {"name": "amb_"}, multi)


# =============================================================================
# Registry
# =============================================================================


def test_consumption_commands_are_registered() -> None:
    names = {command.name for command in COMMANDS}
    assert {"explore", "find-callers", "find-callees", "blast-radius"} <= names


def test_consumption_commands_accept_a_graph(multi: MultiGraph) -> None:
    multi.create_graph("side")
    result = run_handler(
        "create-entity",
        {
            "name": "lonely (x.py)",
            "entityType": "procedure",
            "observations": ["File path: theloom/x.py", "Line range: 1-2"],
            "graph": "side",
        },
        multi,
    )
    entity_id = str(result["id"])
    assert run_handler("explore", {"name": "lonely", "graph": "side"}, multi)["entity"]["id"] == (
        entity_id
    )
    assert run_handler("find-callers", {"name": "lonely", "graph": "side"}, multi)["callers"] == []
    assert (
        run_handler("blast-radius", {"name": "lonely", "graph": "side"}, multi)["affected"]["total"]
        == 0
    )
