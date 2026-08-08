"""detect-loops / list-loops must not let the naive sequence produce a false
belief that a graph has no feedback loops (TL-481, child of the Agent
Contract epic TL-477).

The bug: ``detect-loops`` with no ``persist`` key returns full loop data (each
loop carries ``id: null`` and ``persisted: false``) but writes nothing to the
graph. A caller who then runs ``list-loops`` -- which only ever reads
*persisted* loop entities -- gets back an empty list. Read in sequence, detect
says "found 2 loops" and list says "there are 0 loops"; nothing in either
response explains the discrepancy, so the natural conclusion ("this graph has
no feedback loops") is false.

The fix keeps the persist:false default (composites like graph_reconnaissance
and entity_deep_dive already call detect-loops with an explicit persist:false
for cheap, non-mutating inspection, and flipping the default to true would
make every such call silently accumulate duplicate loop entities on repeat
calls -- there is no dedup on create_loop_entity). Instead, both responses are
made honest per the shared notice convention (theloom/operations/notices.py):

- detect-loops carries ``applied`` truthfully (false unless persist was
  requested) and, when loops were found but not persisted, a NOT_PERSISTED
  notice naming the exact flag to pass.
- list-loops carries ``count``/``loops`` (replacing the old bare-array
  response) plus, when zero loop entities exist in the graph at all, a
  NONE_PERSISTED notice that explicitly distinguishes "nothing has been
  persisted yet" from "no loops exist" -- it never asserts the latter, since
  list-loops has no way to know it.

These tests pin the naive sequence end-to-end on a minimal 2-variable
feedback cycle (A causes B, B causes A, both positive polarity -> a
reinforcing loop), matching the rigor of test_ops_analysis.py and
test_cli_schema_flag.py.
"""

from __future__ import annotations

from theloom.operations.analysis import (
    DetectLoopsInput,
    ListLoopsInput,
    detect_loops,
    list_loops,
)
from theloom.operations.entity import CreateEntityInput, create_entity
from theloom.operations.relations import CreateRelationInput, create_relation
from theloom.store.multigraph import MultiGraph


def ent(multi: MultiGraph, name: str) -> str:
    result = create_entity(
        CreateEntityInput.model_validate(
            {"name": name, "entityType": "concept", "observations": [name]}
        ),
        multi,
    )
    return str(result["id"])


def causal_rel(multi: MultiGraph, from_id: str, to_id: str, polarity: str = "+") -> None:
    create_relation(
        CreateRelationInput.model_validate(
            {
                "from": from_id,
                "to": to_id,
                "relationType": "causes",
                "polarity": polarity,
                "strength": "moderate",
                "evidence": "test fixture",
            }
        ),
        multi,
    )


def two_variable_feedback_cycle(multi: MultiGraph) -> tuple[str, str]:
    """A -> B -> A, both edges positive polarity: a 2-member reinforcing loop --
    the minimal repro graph from the TL-481 bug report."""
    a = ent(multi, "A")
    b = ent(multi, "B")
    causal_rel(multi, a, b)
    causal_rel(multi, b, a)
    return a, b


# =============================================================================
# detect-loops: honest about what was (not) written
# =============================================================================


def test_detect_loops_without_persist_reports_applied_false_and_a_notice(
    multi: MultiGraph,
) -> None:
    two_variable_feedback_cycle(multi)

    result = detect_loops(DetectLoopsInput(graph=multi.default_graph), multi)

    # The loop is still found and classified -- detection itself is untouched.
    assert result["loopCount"] == 1
    loop = result["loops"][0]
    assert loop["classification"] == "reinforcing"
    assert loop["netPolarity"] == "+"
    assert loop["memberCount"] == 2
    assert loop["id"] is None
    assert loop["persisted"] is False

    # But the envelope says plainly that nothing was written.
    assert result["applied"] is False
    notices = result.get("notices", [])
    codes = [n["code"] for n in notices]
    assert "NOT_PERSISTED" in codes
    not_persisted = next(n for n in notices if n["code"] == "NOT_PERSISTED")
    assert "persist" in (not_persisted.get("hint") or "").lower()


def test_detect_loops_with_persist_reports_applied_true_and_no_notice(
    multi: MultiGraph,
) -> None:
    two_variable_feedback_cycle(multi)

    result = detect_loops(DetectLoopsInput(graph=multi.default_graph, persist=True), multi)

    assert result["applied"] is True
    assert result["loops"][0]["id"] is not None
    assert result["loops"][0]["persisted"] is True
    assert "notices" not in result


def test_detect_loops_no_loops_found_carries_no_spurious_notice(multi: MultiGraph) -> None:
    """persist:false with zero loops detected: there is nothing "not persisted"
    to warn about, so no NOT_PERSISTED noise."""
    ent(multi, "Solo")

    result = detect_loops(DetectLoopsInput(graph=multi.default_graph), multi)

    assert result["loopCount"] == 0
    assert result["applied"] is False
    assert "notices" not in result


# =============================================================================
# list-loops: distinguishes "nothing persisted yet" from "no loops exist"
# =============================================================================


def test_list_loops_empty_graph_carries_none_persisted_notice(multi: MultiGraph) -> None:
    result = list_loops(ListLoopsInput(graph=multi.default_graph), multi)

    assert result["count"] == 0
    assert result["loops"] == []
    notices = result.get("notices", [])
    codes = [n["code"] for n in notices]
    assert "NONE_PERSISTED" in codes
    doc = next(n for n in notices if n["code"] == "NONE_PERSISTED")
    # It must never claim loops don't exist -- only that none are persisted.
    assert "no loops exist" not in doc["message"].lower()
    assert "persist" in doc["message"].lower()


def test_list_loops_after_persisted_detect_finds_the_loop_with_no_notice(
    multi: MultiGraph,
) -> None:
    two_variable_feedback_cycle(multi)
    detect_loops(DetectLoopsInput(graph=multi.default_graph, persist=True), multi)

    result = list_loops(ListLoopsInput(graph=multi.default_graph), multi)

    assert result["count"] == 1
    assert result["loops"][0]["_metadata"]["classification"] == "reinforcing"
    assert "notices" not in result


# =============================================================================
# The naive sequence itself: detect (no persist key), then list.
# =============================================================================


def test_naive_sequence_cannot_conclude_the_graph_has_no_loops(multi: MultiGraph) -> None:
    """The exact sequence from the bug report, run deliberately naively: no
    persist key on detect-loops, then list-loops. Read together, the two
    responses must not be able to support the false belief "this graph has no
    feedback loops" -- either detect must say the results are unpersisted (and
    how to fix that), or list must say nothing has been persisted yet, or
    both. Here: both.
    """
    two_variable_feedback_cycle(multi)

    detected = detect_loops(DetectLoopsInput(graph=multi.default_graph), multi)
    listed = list_loops(ListLoopsInput(graph=multi.default_graph), multi)

    # The loop was genuinely found and classified by detect-loops.
    assert detected["loopCount"] == 1
    assert detected["loops"][0]["classification"] == "reinforcing"

    # list-loops alone looks like "no loops" -- but only alone.
    assert listed["count"] == 0

    # The discrepancy is explained on both ends: detect said it didn't write,
    # list said nothing has been written yet. Neither is silent about it.
    assert detected["applied"] is False
    detect_codes = {n["code"] for n in detected.get("notices", [])}
    assert "NOT_PERSISTED" in detect_codes

    list_codes = {n["code"] for n in listed.get("notices", [])}
    assert "NONE_PERSISTED" in list_codes
