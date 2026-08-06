"""blast-radius's traversal, as its own testable unit: the reverse-
reachability walk, the hub-suppression rule, and module grouping — pinned
directly against ``run_traversal``/``group_by_module`` rather than through the
whole ``blast-radius`` command, and against small graphs built just for this.

``group_by_module`` is plain-data (docs + affected-depths in, grouped rows
out) and is exercised with synthetic docs, no store involved. ``run_traversal``
reads a live (but small) graph, so those cases go through ``multi``.
"""

from __future__ import annotations

from typing import Any

from theloom.operations.blast_radius_traversal import group_by_module, run_traversal
from theloom.store.multigraph import MultiGraph


def _create(multi: MultiGraph, name: str, entity_type: str = "procedure") -> str:
    from theloom.cli.registry import run_handler

    result = run_handler(
        "create-entity",
        {"name": name, "entityType": entity_type, "observations": [name]},
        multi,
    )
    return str(result["id"])


def _relate(multi: MultiGraph, from_id: str, to_id: str, relation_type: str) -> None:
    from theloom.cli.registry import run_handler

    run_handler(
        "create-relation",
        {
            "from": from_id,
            "to": to_id,
            "relationType": relation_type,
            "polarity": None,
            "strength": "moderate",
            "evidence": f"{from_id} {relation_type} {to_id}",
        },
        multi,
    )


def _active(doc: dict[str, Any] | None) -> bool:
    return doc is not None and str(doc.get("status") or "active") == "active"


# =============================================================================
# run_traversal — reverse reachability + member seeding
# =============================================================================


def test_run_traversal_reaches_dependants_through_seeded_members(multi: MultiGraph) -> None:
    """widget <-part_of- method <-calls- alpha <-calls- beta. Seeding on the
    method (via part_of) means alpha is reached at hop 1 from widget, and beta
    at hop 2 — exactly the "a caller bound to a method is inside the class's
    radius" behaviour blast-radius promises."""
    store = multi.get_store(None)
    widget = _create(multi, "Widget")
    method = _create(multi, "Widget.run")
    alpha = _create(multi, "alpha")
    beta = _create(multi, "beta")
    _relate(multi, method, widget, "part_of")
    _relate(multi, alpha, method, "calls")
    _relate(multi, beta, alpha, "calls")

    seed_doc = store.read_entity_doc(widget)
    assert seed_doc is not None
    result = run_traversal(
        store,
        widget,
        seed_doc,
        relation_types=("calls", "requires", "instance_of"),
        depth=4,
        hub_percentile=99.0,
        min_hub_degree=8,
        is_active=_active,
    )

    assert result.members == [method]
    assert result.affected == {alpha: 1, beta: 2}
    assert result.suppressed == {}
    assert result.docs[alpha]["name"] == "alpha"


def test_run_traversal_honours_depth_cap(multi: MultiGraph) -> None:
    store = multi.get_store(None)
    a = _create(multi, "a")
    b = _create(multi, "b")
    c = _create(multi, "c")
    _relate(multi, b, a, "calls")
    _relate(multi, c, b, "calls")

    seed_doc = store.read_entity_doc(a)
    assert seed_doc is not None
    result = run_traversal(
        store,
        a,
        seed_doc,
        relation_types=("calls", "requires", "instance_of"),
        depth=1,
        hub_percentile=99.0,
        min_hub_degree=8,
        is_active=_active,
    )
    assert result.affected == {b: 1}


def test_run_traversal_suppresses_a_hub_above_the_percentile_threshold(multi: MultiGraph) -> None:
    """A degree-13 hub sitting between a near-degree-1 population must be
    refused expansion — its 12 far-flung dependants never enter ``affected``,
    and it is reported in ``suppressed`` with its degree."""
    store = multi.get_store(None)
    seed = _create(multi, "seed")
    hub = _create(multi, "hub")
    near = _create(multi, "near")
    outer = _create(multi, "outer")
    _relate(multi, hub, seed, "calls")
    _relate(multi, near, seed, "calls")
    _relate(multi, outer, near, "calls")
    for index in range(12):
        far = _create(multi, f"far_{index}")
        _relate(multi, far, hub, "calls")

    seed_doc = store.read_entity_doc(seed)
    assert seed_doc is not None
    result = run_traversal(
        store,
        seed,
        seed_doc,
        relation_types=("calls", "requires", "instance_of"),
        depth=4,
        hub_percentile=99.0,
        min_hub_degree=8,
        is_active=_active,
    )
    assert result.suppressed == {hub: 13}
    assert hub in result.affected  # the hub itself is reached, one hop out...
    assert result.affected[hub] == 1
    # ... but nothing past it is: its 12 dependants are withheld.
    far_ids = {
        entity_id for entity_id, doc in result.docs.items() if doc["name"].startswith("far_")
    }
    assert far_ids.isdisjoint(result.affected)
    assert outer in result.affected
    assert result.affected[outer] == 2


def test_run_traversal_drops_retired_dependants(multi: MultiGraph) -> None:
    from theloom.cli.registry import run_handler

    store = multi.get_store(None)
    seed = _create(multi, "seed")
    alive = _create(multi, "alive")
    retired = _create(multi, "retired")
    _relate(multi, alive, seed, "calls")
    _relate(multi, retired, seed, "calls")
    run_handler("update-entity", {"id": retired, "status": "superseded"}, multi)

    seed_doc = store.read_entity_doc(seed)
    assert seed_doc is not None
    result = run_traversal(
        store,
        seed,
        seed_doc,
        relation_types=("calls", "requires", "instance_of"),
        depth=4,
        hub_percentile=99.0,
        min_hub_degree=8,
        is_active=_active,
    )
    assert result.affected == {alive: 1}


# =============================================================================
# group_by_module — pure, synthetic docs
# =============================================================================


def test_group_by_module_sorts_by_count_desc_then_module_name() -> None:
    docs = {
        "1": {"name": "one", "module": "pkg/a"},
        "2": {"name": "two", "module": "pkg/a"},
        "3": {"name": "three", "module": "pkg/b"},
    }
    affected = {"1": 1, "2": 2, "3": 1}
    grouped = group_by_module(docs, affected, module_of=lambda doc: str(doc["module"]))
    assert grouped == [
        ("pkg/a", [{"name": "one", "depth": 1}, {"name": "two", "depth": 2}]),
        ("pkg/b", [{"name": "three", "depth": 1}]),
    ]


def test_group_by_module_orders_rows_within_a_module_by_depth_then_name() -> None:
    docs = {
        "1": {"name": "zeta", "module": "m"},
        "2": {"name": "alpha", "module": "m"},
        "3": {"name": "beta", "module": "m"},
    }
    affected = {"1": 1, "2": 1, "3": 2}
    grouped = group_by_module(docs, affected, module_of=lambda doc: str(doc["module"]))
    assert grouped == [
        (
            "m",
            [
                {"name": "alpha", "depth": 1},
                {"name": "zeta", "depth": 1},
                {"name": "beta", "depth": 2},
            ],
        )
    ]
