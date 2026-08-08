"""Session workspace commands (desire 2), exercised through the real
registry dispatch (``run_handler``) so the TL-477 contract wiring —
``eventIds`` receipts, the list envelope, truthful ``applied`` — is proved
end to end, the same way ``test_ops_receipts.py`` proves write-receipts.
"""

from __future__ import annotations

from theloom.cli.registry import run_handler
from theloom.store.multigraph import MultiGraph


def test_begin_session_carries_event_ids_and_applied_true(multi: MultiGraph) -> None:
    result = run_handler("begin-session", {"name": "scratch", "ttlSeconds": 3600}, multi)
    assert result["sessionId"].startswith("sess-")
    assert result["namespace"] == f"{result['sessionId']}-"
    assert result["applied"] is True
    assert result["eventIds"]  # write-receipt (desire 1), for free via run_handler


def test_begin_session_with_no_input_gets_a_ttl_less_session(multi: MultiGraph) -> None:
    result = run_handler("begin-session", {}, multi)
    assert result["ttlSeconds"] is None
    assert result["expiresAt"] is None
    assert result["applied"] is True


def test_end_session_reaps_graphs_created_under_the_namespace(multi: MultiGraph) -> None:
    session = run_handler("begin-session", {}, multi)
    namespace = session["namespace"]
    run_handler("create-graph", {"name": f"{namespace}scratch"}, multi)
    run_handler(
        "create-entity",
        {"name": "x", "entityType": "concept", "observations": [], "graph": f"{namespace}adhoc"},
        multi,
    )
    run_handler("create-graph", {"name": "outside-the-session"}, multi)

    result = run_handler("end-session", {"sessionId": session["sessionId"]}, multi)
    assert sorted(result["reapedGraphs"]) == [f"{namespace}adhoc", f"{namespace}scratch"]
    assert result["applied"] is True
    assert "notices" not in result
    assert result["eventIds"]

    listed = run_handler("list-graphs", {}, multi)
    names = [g["name"] for g in listed["items"]]
    assert f"{namespace}scratch" not in names
    assert f"{namespace}adhoc" not in names
    assert "outside-the-session" in names


def test_end_session_twice_is_a_truthful_no_op_the_second_time(multi: MultiGraph) -> None:
    session = run_handler("begin-session", {}, multi)
    run_handler("create-graph", {"name": f"{session['namespace']}a"}, multi)
    run_handler("end-session", {"sessionId": session["sessionId"]}, multi)

    second = run_handler("end-session", {"sessionId": session["sessionId"]}, multi)
    assert second["applied"] is False
    assert second["reapedGraphs"] == []
    assert second["notices"][0]["code"] == "ALREADY_REAPED"


def test_end_session_unknown_id_is_not_found(multi: MultiGraph) -> None:
    from theloom.errors import NotFoundError

    try:
        run_handler("end-session", {"sessionId": "sess-nope"}, multi)
        raise AssertionError("expected NotFoundError")
    except NotFoundError:
        pass


def test_list_sessions_uses_the_uniform_envelope(multi: MultiGraph) -> None:
    run_handler("begin-session", {"name": "a"}, multi)
    run_handler("begin-session", {"name": "b"}, multi)
    result = run_handler("list-sessions", {}, multi)
    assert result["count"] == 2
    assert len(result["items"]) == 2
    assert "eventIds" not in result  # read-only: no receipt to attach


def test_ad_hoc_graph_registry_gap_is_closed_end_to_end(multi: MultiGraph) -> None:
    """The gap the gauntlet named directly: a graph created via a bare
    `graph` param on an ordinary mutating command (no create-graph call at
    all) must be visible to list-graphs and deletable by delete-graph — no
    redis-cli required."""
    run_handler(
        "create-entity",
        {"name": "x", "entityType": "concept", "observations": [], "graph": "adhoc-e2e"},
        multi,
    )
    names = [g["name"] for g in run_handler("list-graphs", {}, multi)["items"]]
    assert "adhoc-e2e" in names

    run_handler("delete-graph", {"name": "adhoc-e2e"}, multi)
    names_after = [g["name"] for g in run_handler("list-graphs", {}, multi)["items"]]
    assert "adhoc-e2e" not in names_after
