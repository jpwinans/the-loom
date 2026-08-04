"""Analytics for the bundle — thin reuse of the existing analysis operations,
with guardrails so the two super-linear analyses cannot dominate assembly on a
large graph:

* **betweenness** (`rustworkx.betweenness_centrality`, O(V*E) unweighted
  Brandes) has no cheaper approximate path in this codebase, so above
  ``BETWEENNESS_MAX_NODES`` it is simply omitted from the shipped centrality
  dict (``degree``/``pagerank`` still ship — the Overview and Explorer both
  already tolerate a missing algorithm key).
* **loop detection** (``theloom.graph.cycles.find_circuits``, a hand-rolled
  Johnson's elementary-circuit enumeration) has no size bound of its own —
  ``DetectLoopsInput.max_size`` is a *post-hoc* filter on the already-fully-
  enumerated result, not a search-space bound — so it is worst-case
  exponential in the number of cycles. Above ``LOOP_MAX_NODES`` the whole
  enumeration is skipped (``loops: []``); at or under it, ``max_size`` still
  trims the *shipped* list to loops of a practical, readable size.

Every threshold is a bare module-level constant (never captured in a default
argument), so a test can `monkeypatch.setattr(theloom.viz.analytics,
"BETWEENNESS_MAX_NODES", 0)` to exercise a gate against a small seeded graph
instead of building a real 50k-node one. All four are set far above the
10-entity ``tapestry-dev`` fixture (and every other existing test's graph),
so no existing test's output changes.
"""

from __future__ import annotations

from theloom.operations.analysis import (
    AnalyzeCentralityInput,
    DetectComponentsInput,
    DetectLoopsInput,
    ListLeveragePointsInput,
    analyze_centrality,
    detect_components,
    detect_loops,
    list_leverage_points,
)
from theloom.store.multigraph import MultiGraph
from theloom.viz.schema import AnalyticsSection

_ALGORITHMS = ("degree", "betweenness", "pagerank")

# Above this entity count, `betweenness` is dropped from the centrality dict.
BETWEENNESS_MAX_NODES = 5_000
# Above this entity count, loop detection is skipped entirely (`loops: []`).
LOOP_MAX_NODES = 10_000
# At/under LOOP_MAX_NODES, loops longer than this are dropped from the
# shipped list (the enumeration itself still ran in full — see module doc).
LOOP_MAX_SIZE = 12
# The top-N scores shipped per centrality algorithm, at any graph size — the
# full ranking is always computed; only the shipped tail is trimmed, so this
# is a bundle-size guardrail, not a compute-time one.
CENTRALITY_SHIP_LIMIT = 1_000


def assemble_analytics(graph: str | None, multi: MultiGraph) -> AnalyticsSection:
    # One count, paid once, decides both super-linear gates below — far
    # cheaper than the betweenness/loop computations it may skip.
    node_count = multi.get_store(graph).get_stats()["entityCount"]

    algorithms = _ALGORITHMS if node_count <= BETWEENNESS_MAX_NODES else ("degree", "pagerank")
    centrality = {
        algorithm: {
            entry["id"]: entry["score"]
            for entry in analyze_centrality(
                AnalyzeCentralityInput(
                    algorithm=algorithm, graph=graph, limit=CENTRALITY_SHIP_LIMIT
                ),
                multi,
            )["results"]
        }
        for algorithm in algorithms
    }
    components = detect_components(DetectComponentsInput(graph=graph), multi)["components"]
    if node_count <= LOOP_MAX_NODES:
        loops = detect_loops(
            DetectLoopsInput(graph=graph, persist=False, maxSize=LOOP_MAX_SIZE), multi
        )["loops"]
    else:
        loops = []
    leverage = list_leverage_points(ListLeveragePointsInput(graph=graph), multi)["leveragePoints"]
    bridges = multi.bridges.list_bridges()
    return AnalyticsSection(
        centrality=centrality,
        components=components,
        loops=loops,
        leveragePoints=leverage,
        bridges=bridges,
    )
