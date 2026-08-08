"""semiring-distances direction handling (TL-483, child of the TL-477 Agent
Contract epic).

The bug: ``SemiringDistancesInput.direction`` was an untyped, undocumented
``str | None`` that flowed straight into the traversal engine. The engine's
own direction check only recognised the literal strings ``"outgoing"`` and
``"incoming"``; anything else — including ``"both"``, or the aliases a caller
would reasonably guess ("out", "in") — silently fell through to a
both-directions scan. That made 'out', 'in', and 'both' produce
byte-identical (both-direction) results, so 'in' never actually walked
inbound edges as its name implied. Worse, a caller who omitted ``direction``
on a pure-sink entity (only inbound edges) got a bare ``{"distances": []}``
— structurally indistinguishable from "this entity has no causal reach",
when the real story is "you searched outgoing on a node with only incoming
edges."

These tests pin the fix at the operations layer:

- omitting ``direction`` on a node with real outgoing edges returns
  non-empty ``distances`` (default is ``'out'``, not an accidental
  empty/both scan whose emptiness would be mistaken for "no reach");
- a pure-sink entity returns a structured ``EMPTY_TRAVERSAL`` notice
  carrying real, store-computed edge counts in each direction, not a bare
  empty list;
- ``'in'`` actually traverses inbound edges and differs from ``'out'`` on
  an asymmetric fixture (probe baseline was three aliases for one
  behavior);
- an invalid direction value is rejected with a typed ``VALIDATION_ERROR``
  that names the ``direction`` field, rather than being silently absorbed
  the way the old untyped ``str | None`` field absorbed anything.
"""

from __future__ import annotations

import pydantic
import pytest

from theloom.cli.registry import run_handler
from theloom.errors import ValidationError
from theloom.operations.algebra import SemiringDistancesInput, semiring_distances
from theloom.operations.entity import CreateEntityInput, create_entity
from theloom.operations.relations import CreateRelationInput, create_relation
from theloom.store.multigraph import MultiGraph


def ent(multi: MultiGraph, name: str) -> str:
    doc = {"name": name, "entityType": "concept", "observations": [name]}
    result = create_entity(CreateEntityInput.model_validate(doc), multi)
    return str(result["id"])


def causes(multi: MultiGraph, from_id: str, to_id: str) -> None:
    doc = {
        "from": from_id,
        "to": to_id,
        "relationType": "causes",
        "polarity": "+",
        "strength": "strong",
        "evidence": None,
    }
    create_relation(CreateRelationInput.model_validate(doc), multi)


# =============================================================================
# Omitted direction: real reach, not a false-negative empty result
# =============================================================================


def test_omitted_direction_returns_real_reach_from_outgoing_source(multi: MultiGraph) -> None:
    """A entity with an unambiguous outgoing edge must not read as
    'no causal reach' just because `direction` was left off the call."""
    source, target = ent(multi, "Source"), ent(multi, "Target")
    causes(multi, source, target)

    result = semiring_distances(
        SemiringDistancesInput.model_validate({"source": source, "semiring": "viterbi"}), multi
    )

    assert result["distances"] != []
    assert {d["entityId"] for d in result["distances"]} == {target}
    assert "notices" not in result


def test_omitted_direction_resolves_to_out_explicitly_too(multi: MultiGraph) -> None:
    """Explicitly passing direction='out' must match the omitted-direction
    default byte-for-byte (same distances) — 'out' IS the default."""
    source, target = ent(multi, "Source"), ent(multi, "Target")
    causes(multi, source, target)

    omitted = semiring_distances(
        SemiringDistancesInput.model_validate({"source": source, "semiring": "viterbi"}), multi
    )
    explicit = semiring_distances(
        SemiringDistancesInput.model_validate(
            {"source": source, "semiring": "viterbi", "direction": "out"}
        ),
        multi,
    )
    assert omitted["distances"] == explicit["distances"]


# =============================================================================
# Pure-sink diagnosis: a real notice with real counts, never a bare []
# =============================================================================


def test_pure_sink_returns_diagnosis_notice_with_real_counts(multi: MultiGraph) -> None:
    """A pure-sink entity (all edges inbound) searched in the default 'out'
    direction must come back with an EMPTY_TRAVERSAL notice stating the
    searched direction and the real edge counts on each side — not a bare
    empty list indistinguishable from 'no relations at all'."""
    upstream, sink = ent(multi, "Upstream"), ent(multi, "Sink")
    causes(multi, upstream, sink)

    result = semiring_distances(
        SemiringDistancesInput.model_validate({"source": sink, "semiring": "viterbi"}), multi
    )

    assert result["distances"] == []
    assert "notices" in result
    assert len(result["notices"]) == 1
    doc = result["notices"][0]
    assert doc["code"] == "EMPTY_TRAVERSAL"
    assert "0 outgoing" in doc["message"]
    assert "1 incoming" in doc["message"]
    assert doc["hint"] is not None
    assert "'in'" in doc["hint"]


def test_pure_sink_notice_counts_multiple_incoming_edges(multi: MultiGraph) -> None:
    """The incoming count in the notice is the real store count, not a
    fabricated 0-or-1 — pin it against a sink with two upstream causes."""
    a, b, sink = ent(multi, "A"), ent(multi, "B"), ent(multi, "Sink")
    causes(multi, a, sink)
    causes(multi, b, sink)

    result = semiring_distances(
        SemiringDistancesInput.model_validate({"source": sink, "semiring": "viterbi"}), multi
    )

    assert result["distances"] == []
    doc = result["notices"][0]
    assert "0 outgoing" in doc["message"]
    assert "2 incoming" in doc["message"]


def test_isolated_entity_in_both_direction_gets_a_notice_with_no_hint(
    multi: MultiGraph,
) -> None:
    """An entity with zero edges in either direction, searched with
    direction='both', still gets a factual notice — but there is no other
    direction to suggest, so the hint is omitted rather than fabricated."""
    lonely = ent(multi, "Lonely")

    result = semiring_distances(
        SemiringDistancesInput.model_validate(
            {"source": lonely, "semiring": "viterbi", "direction": "both"}
        ),
        multi,
    )

    assert result["distances"] == []
    doc = result["notices"][0]
    assert doc["code"] == "EMPTY_TRAVERSAL"
    assert "hint" not in doc


def test_relation_type_filtered_empty_result_gets_no_direction_notice(
    multi: MultiGraph,
) -> None:
    """Distances can also come back empty because a relationTypes filter
    excluded every edge — that is not a direction problem, so the
    direction-diagnosis notice must not fire and claim a false '0 outgoing
    edges' when outgoing edges actually exist."""
    source, target = ent(multi, "Source"), ent(multi, "Target")
    causes(multi, source, target)

    result = semiring_distances(
        SemiringDistancesInput.model_validate(
            {
                "source": source,
                "semiring": "viterbi",
                "relationTypes": ["supports"],
            }
        ),
        multi,
    )

    assert result["distances"] == []
    assert "notices" not in result


# =============================================================================
# 'in' must genuinely differ from 'out' — the probe baseline was three
# aliases for one (outgoing-only) behavior.
# =============================================================================


def test_in_direction_traverses_inbound_edges_and_differs_from_out(
    multi: MultiGraph,
) -> None:
    """Asymmetric fixture: predecessor -> hub -> successor. From hub,
    direction='out' must reach only successor; direction='in' must reach
    only predecessor. If 'in' silently behaved like 'out' (the reported
    bug), both calls would return byte-identical distances."""
    predecessor = ent(multi, "Predecessor")
    hub = ent(multi, "Hub")
    successor = ent(multi, "Successor")
    causes(multi, predecessor, hub)
    causes(multi, hub, successor)

    out_result = semiring_distances(
        SemiringDistancesInput.model_validate(
            {"source": hub, "semiring": "viterbi", "direction": "out"}
        ),
        multi,
    )
    in_result = semiring_distances(
        SemiringDistancesInput.model_validate(
            {"source": hub, "semiring": "viterbi", "direction": "in"}
        ),
        multi,
    )

    out_ids = {d["entityId"] for d in out_result["distances"]}
    in_ids = {d["entityId"] for d in in_result["distances"]}
    assert out_ids == {successor}
    assert in_ids == {predecessor}
    assert out_ids != in_ids
    assert out_result["distances"] != in_result["distances"]


def test_both_direction_unions_out_and_in(multi: MultiGraph) -> None:
    """direction='both' must genuinely be the union of 'out' and 'in', not
    a silent alias for either one alone."""
    predecessor = ent(multi, "Predecessor")
    hub = ent(multi, "Hub")
    successor = ent(multi, "Successor")
    causes(multi, predecessor, hub)
    causes(multi, hub, successor)

    both_result = semiring_distances(
        SemiringDistancesInput.model_validate(
            {"source": hub, "semiring": "viterbi", "direction": "both"}
        ),
        multi,
    )
    both_ids = {d["entityId"] for d in both_result["distances"]}
    assert both_ids == {predecessor, successor}


# =============================================================================
# Invalid direction values are rejected with a typed, self-naming error —
# never silently absorbed the way the old bare `str | None` field absorbed
# anything handed to it.
# =============================================================================


def test_invalid_direction_value_is_a_typed_validation_error(multi: MultiGraph) -> None:
    with pytest.raises(pydantic.ValidationError):
        SemiringDistancesInput.model_validate(
            {
                "source": "00000000-0000-4000-8000-000000000000",
                "semiring": "viterbi",
                "direction": "sideways",
            }
        )


def test_invalid_direction_value_via_cli_registry_names_the_field(multi: MultiGraph) -> None:
    with pytest.raises(ValidationError) as excinfo:
        run_handler(
            "semiring-distances",
            {
                "source": "00000000-0000-4000-8000-000000000000",
                "semiring": "viterbi",
                "direction": "sideways",
            },
            multi,
        )
    assert excinfo.value.code == "VALIDATION_ERROR"
    assert "direction" in str(excinfo.value)
