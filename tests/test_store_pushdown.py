"""Server-side filter pushdown for the list reads.

The store used to fetch and Pydantic-validate every entity/relation in the
graph and filter in Python. These tests pin the replacement:

- ``theloom/store/filters.py`` stays the semantics oracle — an equivalence
  matrix asserts the pushdown path returns exactly what the full-scan +
  Python-filter path returns.
- The scan itself is narrowed server-side (query shape + row counts, never
  wall-clock).
- Graphs written before the derived index existed still answer correctly and
  are backfilled in place on the first filtered read.
- ``limit`` caps the transferred window and reports the untruncated total.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from falkordb import FalkorDB
from redis import Redis

from theloom.model import (
    Entity,
    EntityCreate,
    EntityFilter,
    Relation,
    RelationCreate,
    RelationFilter,
)
from theloom.store.falkor import FalkorGraphStore
from theloom.store.filters import apply_entity_filters, apply_relation_filters

SOURCE = "<source-id>"


@pytest.fixture()
def store(db: FalkorDB, redis_client: Redis, namespace: str) -> FalkorGraphStore:
    return FalkorGraphStore(db, redis_client, graph_name=f"{namespace}-g", key_prefix=namespace)


# =============================================================================
# Population + oracle
# =============================================================================


def seed(store: FalkorGraphStore) -> dict[str, str]:
    """A mixed population: every status, several types, query hits in the name
    and in observations, versions, a session, and a `sources` edge."""
    specs: dict[str, dict[str, Any]] = {
        "alpha": {
            "name": "Alpha Concept",
            "entityType": "concept",
            "observations": ["mentions WIDGETS in an observation"],
        },
        "beta": {
            "name": "Beta Pattern",
            "entityType": "pattern",
            # A newline *inside* one observation: a query spanning it is a true
            # match for filters.py, unlike one spanning the name/observation
            # boundary that only the folded `_search` haystack joins.
            "observations": ["about gadgets\nand doodads", "omega tail"],
            "version": 2,
        },
        "gamma": {
            "name": "Gamma widgets Concept",
            "entityType": "concept",
            "observations": ["nothing relevant"],
            "status": "deprecated",
        },
        "delta": {
            "name": "Delta Question",
            "entityType": "question",
            "observations": ["widgets again"],
            "status": "superseded",
            "version": 3,
        },
        "epsilon": {
            "name": "epsilon concept",
            "entityType": "concept",
            "observations": ["session scoped", "subgraph: legacy-1"],
            "status": "investigating",
            "session": "s1",
        },
        "zeta": {
            "name": "Zeta Retracted",
            "entityType": "concept",
            "observations": [],
            "status": "retracted",
        },
        "source": {
            "name": "Source Doc",
            "entityType": "source",
            "observations": ["the source of things"],
        },
    }
    ids = {
        key: store.create_entity(EntityCreate.model_validate(spec)).id
        for key, spec in specs.items()
    }
    store.create_relations(
        [
            RelationCreate.model_validate(
                {
                    "from": ids["alpha"],
                    "to": ids["source"],
                    "relationType": "sources",
                    "strength": "strong",
                }
            ),
            RelationCreate.model_validate(
                {
                    "from": ids["beta"],
                    "to": ids["alpha"],
                    "relationType": "supports",
                    "polarity": "+",
                    "strength": "moderate",
                    "session": "s1",
                }
            ),
            RelationCreate.model_validate(
                {
                    "from": ids["beta"],
                    "to": ids["alpha"],
                    "relationType": "contradicts",
                    "polarity": "-",
                    "strength": "weak",
                }
            ),
            RelationCreate.model_validate(
                {
                    "from": ids["gamma"],
                    "to": ids["source"],
                    "relationType": "sources",
                    "strength": "moderate",
                }
            ),
        ]
    )
    return ids


def oracle_entities(store: FalkorGraphStore, filter: EntityFilter | None) -> list[Entity]:
    """The pre-pushdown read path: fetch everything, validate everything,
    filter in Python. The semantics this package must not change."""
    rows = store._rows_paged("MATCH (n:_Entity) RETURN n._doc ORDER BY id(n)")
    entities = [Entity.model_validate(json.loads(row[0])) for row in rows]
    entities = apply_entity_filters(entities, filter)
    if filter is None:
        return entities
    included = store._sources_of(filter.sourced_from)
    excluded = store._sources_of(filter.exclude_sourced_from)
    if included is not None:
        entities = [e for e in entities if e.id in included]
    if excluded is not None:
        entities = [e for e in entities if e.id not in excluded]
    return entities


def oracle_relations(store: FalkorGraphStore, filter: RelationFilter | None) -> list[Relation]:
    rows = store._rows_paged("MATCH (:_Entity)-[r]->(:_Entity) RETURN r._doc ORDER BY id(r)")
    relations = [Relation.model_validate(json.loads(row[0])) for row in rows]
    return apply_relation_filters(relations, filter)


ALL_STATUSES = ["active", "superseded", "deprecated", "retracted", "investigating"]

ENTITY_FILTER_MATRIX: list[dict[str, Any]] = [
    {},
    {"statusFilter": ["active"]},
    {"statusFilter": ["active", "superseded"]},
    {"statusFilter": ALL_STATUSES},
    {"statusFilter": ["deprecated", "retracted"]},
    {"entityType": "concept"},
    {"entityType": "concept", "statusFilter": ALL_STATUSES},
    {"entityType": "source", "statusFilter": ALL_STATUSES},
    {"name": "alpha"},
    {"name": "CONCEPT", "statusFilter": ALL_STATUSES},
    {"name": "no-such-name"},
    {"query": "widgets", "statusFilter": ALL_STATUSES},
    {"query": "WIDGETS"},
    {"query": "gamma", "statusFilter": ALL_STATUSES},
    {"query": "zzz-nomatch"},
    {"name": "concept", "query": "widgets", "statusFilter": ALL_STATUSES},
    {"version": 2},
    {"minVersion": 2, "statusFilter": ALL_STATUSES},
    {"session": "s1", "statusFilter": ALL_STATUSES},
    {"session": "legacy", "statusFilter": ALL_STATUSES},
    {"sourcedFrom": [SOURCE], "statusFilter": ALL_STATUSES},
    {"excludeSourcedFrom": [SOURCE], "statusFilter": ALL_STATUSES},
    {"sourcedFrom": [SOURCE], "excludeSourcedFrom": [SOURCE], "statusFilter": ALL_STATUSES},
    {"entityType": "concept", "query": "widgets", "statusFilter": ["deprecated"]},
    # A query spanning the name/observation boundary: `_search` folds the two
    # together, so the Cypher prefilter says yes and filters.py says no.
    {"query": "alpha concept\nmentions", "statusFilter": ALL_STATUSES},
    # ...and one spanning a newline *within* a single observation, which is a
    # genuine match on both sides.
    {"query": "gadgets\nand doodads", "statusFilter": ALL_STATUSES},
]


def resolve(doc: dict[str, Any], ids: dict[str, str]) -> dict[str, Any]:
    """Substitute the SOURCE placeholder with the seeded source entity id."""
    return {
        key: [ids["source"] if v == SOURCE else v for v in value]
        if isinstance(value, list)
        else value
        for key, value in doc.items()
    }


# =============================================================================
# Equivalence with the Python oracle
# =============================================================================


@pytest.mark.parametrize("filter_doc", ENTITY_FILTER_MATRIX, ids=lambda d: json.dumps(d))
def test_entity_pushdown_matches_the_python_oracle(
    store: FalkorGraphStore, filter_doc: dict[str, Any]
) -> None:
    ids = seed(store)
    filter = EntityFilter.model_validate(resolve(filter_doc, ids))
    assert [e.id for e in store.list_entities(filter)] == [
        e.id for e in oracle_entities(store, filter)
    ]


def test_entity_pushdown_matches_the_oracle_for_no_filter(store: FalkorGraphStore) -> None:
    seed(store)
    assert [e.id for e in store.list_entities(None)] == [e.id for e in oracle_entities(store, None)]


@pytest.mark.parametrize("filter_doc", ENTITY_FILTER_MATRIX, ids=lambda d: json.dumps(d))
def test_entity_docs_track_entities(store: FalkorGraphStore, filter_doc: dict[str, Any]) -> None:
    ids = seed(store)
    filter = EntityFilter.model_validate(resolve(filter_doc, ids))
    docs = store.list_entity_docs(filter)
    assert [d["id"] for d in docs] == [e.id for e in store.list_entities(filter)]


RELATION_FILTER_MATRIX: list[dict[str, Any]] = [
    {},
    {"relationType": "sources"},
    {"relationType": "supports"},
    {"relationType": "related_to"},
    {"polarity": "+"},
    {"polarity": "-"},
    {"session": "s1"},
    {"from": "@beta"},
    {"to": "@alpha"},
    {"from": "@beta", "to": "@alpha"},
    {"from": "@beta", "to": "@alpha", "relationType": "contradicts"},
    {"from": "@alpha", "relationType": "supports"},
]


@pytest.mark.parametrize("filter_doc", RELATION_FILTER_MATRIX, ids=lambda d: json.dumps(d))
def test_relation_pushdown_matches_the_python_oracle(
    store: FalkorGraphStore, filter_doc: dict[str, Any]
) -> None:
    ids = seed(store)
    resolved = {
        key: ids[value[1:]] if isinstance(value, str) and value.startswith("@") else value
        for key, value in filter_doc.items()
    }
    filter = RelationFilter.model_validate(resolved)
    assert [r.id for r in store.list_relations(filter)] == [
        r.id for r in oracle_relations(store, filter)
    ]
    assert [d["id"] for d in store.list_relation_docs(filter)] == [
        r.id for r in store.list_relations(filter)
    ]


# =============================================================================
# Query shape: the scan is narrowed server-side
# =============================================================================


class Spy:
    """Records (cypher, row count) for every query the store runs."""

    def __init__(self, store: FalkorGraphStore) -> None:
        self.calls: list[tuple[str, int]] = []
        self._inner = store._rows
        store._rows = self._record  # type: ignore[method-assign]

    def _record(self, cypher: str, params: dict[str, Any] | None = None) -> list[list[Any]]:
        rows = self._inner(cypher, params)
        self.calls.append((cypher, len(rows)))
        return rows

    def scans(self) -> list[tuple[str, int]]:
        return [call for call in self.calls if call[0].startswith("MATCH (n:_Entity) WHERE")]

    def edge_scans(self) -> list[tuple[str, int]]:
        return [call for call in self.calls if "-[r" in call[0] and "RETURN r._doc" in call[0]]


def test_entity_type_filter_is_pushed_into_cypher(store: FalkorGraphStore) -> None:
    seed(store)
    spy = Spy(store)
    result = store.list_entities(
        EntityFilter.model_validate({"entityType": "concept", "statusFilter": ALL_STATUSES})
    )
    cypher, transferred = spy.scans()[0]
    assert "n._type" in cypher
    assert "n._status" in cypher
    assert transferred == len(result) == 4


def test_status_filter_is_pushed_into_cypher(store: FalkorGraphStore) -> None:
    seed(store)
    spy = Spy(store)
    result = store.list_entities(None)
    cypher, transferred = spy.scans()[0]
    assert "n._status" in cypher
    assert transferred == len(result) == 3


def test_query_filter_is_pushed_into_cypher_over_name_and_observations(
    store: FalkorGraphStore,
) -> None:
    seed(store)
    spy = Spy(store)
    result = store.list_entities(
        EntityFilter.model_validate({"query": "widgets", "statusFilter": ALL_STATUSES})
    )
    cypher, transferred = spy.scans()[0]
    assert "n._search" in cypher
    assert transferred == len(result) == 3


def test_name_filter_is_pushed_into_cypher(store: FalkorGraphStore) -> None:
    seed(store)
    spy = Spy(store)
    result = store.list_entities(
        EntityFilter.model_validate({"name": "concept", "statusFilter": ALL_STATUSES})
    )
    cypher, transferred = spy.scans()[0]
    assert "n._name" in cypher
    assert transferred == len(result) == 3


def test_relation_endpoints_and_type_are_pushed_into_cypher(store: FalkorGraphStore) -> None:
    ids = seed(store)
    spy = Spy(store)
    result = store.list_relations(
        RelationFilter.model_validate({"from": ids["beta"], "relationType": "supports"})
    )
    cypher, transferred = spy.edge_scans()[0]
    assert "[r:supports]" in cypher
    assert transferred == len(result) == 1


# =============================================================================
# Legacy graphs: tolerated, then backfilled
# =============================================================================


def strip_index(store: FalkorGraphStore) -> None:
    """Make the graph look like one written before the derived index existed."""
    store._query("MATCH (n:_Entity) REMOVE n._status, n._type, n._name, n._search")


def unindexed_count(store: FalkorGraphStore) -> int:
    rows = store._rows("MATCH (n:_Entity) WHERE n._status IS NULL RETURN count(n)")
    return int(rows[0][0])


def test_legacy_graph_still_filters_correctly(store: FalkorGraphStore) -> None:
    seed(store)
    widgets = EntityFilter.model_validate({"query": "widgets"})
    expected = [e.id for e in store.list_entities(widgets)]
    strip_index(store)
    assert unindexed_count(store) == 7
    actual = store.list_entities(widgets)
    assert [e.id for e in actual] == expected


def test_first_filtered_read_backfills_the_whole_graph(store: FalkorGraphStore) -> None:
    seed(store)
    strip_index(store)
    store.list_entities(EntityFilter.model_validate({"entityType": "concept"}))
    assert unindexed_count(store) == 0


def test_index_follows_updates(store: FalkorGraphStore) -> None:
    ids = seed(store)
    store.update_entity(ids["alpha"], {"name": "Renamed Alpha", "observations": ["fresh gizmos"]})
    by_name = store.list_entities(EntityFilter.model_validate({"name": "renamed"}))
    assert [e.id for e in by_name] == [ids["alpha"]]
    by_query = store.list_entities(EntityFilter.model_validate({"query": "gizmos"}))
    assert [e.id for e in by_query] == [ids["alpha"]]
    assert store.list_entities(EntityFilter.model_validate({"query": "WIDGETS in an"})) == []


def test_index_follows_status_changes(store: FalkorGraphStore) -> None:
    ids = seed(store)
    store.update_entity(ids["alpha"], {"status": "deprecated"})
    assert [e.id for e in store.list_entities(None)] == [ids["beta"], ids["source"]]
    deprecated = store.list_entities(EntityFilter.model_validate({"statusFilter": ["deprecated"]}))
    assert ids["alpha"] in [e.id for e in deprecated]


# =============================================================================
# limit
# =============================================================================


def test_limit_caps_the_transferred_window(store: FalkorGraphStore) -> None:
    seed(store)
    spy = Spy(store)
    entities, total = store.list_entities_page(
        EntityFilter.model_validate({"statusFilter": ALL_STATUSES, "limit": 2})
    )
    assert len(entities) == 2
    assert total == 7
    assert all(transferred <= 2 for _, transferred in spy.scans())


def test_limit_returns_the_deterministic_prefix(store: FalkorGraphStore) -> None:
    seed(store)
    everything = store.list_entities(EntityFilter.model_validate({"statusFilter": ALL_STATUSES}))
    limited = store.list_entities(
        EntityFilter.model_validate({"statusFilter": ALL_STATUSES, "limit": 3})
    )
    assert [e.id for e in limited] == [e.id for e in everything[:3]]


def test_limit_above_the_match_count_is_not_truncating(store: FalkorGraphStore) -> None:
    seed(store)
    entities, total = store.list_entities_page(
        EntityFilter.model_validate({"statusFilter": ALL_STATUSES, "limit": 100})
    )
    assert len(entities) == total == 7


def test_limit_is_exact_with_a_residual_python_filter(store: FalkorGraphStore) -> None:
    ids = seed(store)
    entities, total = store.list_entities_page(
        EntityFilter.model_validate(
            {"statusFilter": ALL_STATUSES, "sourcedFrom": [ids["source"]], "limit": 1}
        )
    )
    assert total == 2
    assert [e.id for e in entities] == [ids["alpha"]]


def test_limit_on_a_legacy_graph_is_exact(store: FalkorGraphStore) -> None:
    seed(store)
    strip_index(store)
    entities, total = store.list_entities_page(
        EntityFilter.model_validate(
            {"entityType": "concept", "statusFilter": ALL_STATUSES, "limit": 2}
        )
    )
    assert total == 4
    assert len(entities) == 2


@pytest.mark.parametrize("limit", [1, 2, 3])
@pytest.mark.parametrize("filter_doc", ENTITY_FILTER_MATRIX, ids=lambda d: json.dumps(d))
def test_limited_reads_match_the_oracle_prefix_and_total(
    store: FalkorGraphStore, filter_doc: dict[str, Any], limit: int
) -> None:
    """The whole filter matrix, under a limit: the window is the oracle's own
    prefix and the total is the oracle's own length. A server-side LIMIT/count
    may only run where the prefilter decides membership exactly."""
    ids = seed(store)
    resolved = resolve(filter_doc, ids)
    expected = [e.id for e in oracle_entities(store, EntityFilter.model_validate(resolved))]
    entities, total = store.list_entities_page(
        EntityFilter.model_validate({**resolved, "limit": limit})
    )
    assert [e.id for e in entities] == expected[:limit]
    assert total == len(expected)


def test_limit_does_not_count_prefilter_false_positives(store: FalkorGraphStore) -> None:
    """`_search` folds name and observations together, so this query matches
    server-side and nothing at all in filters.py. The total must be 0, not 1,
    and the window must not be spent on the false positive."""
    seed(store)
    entities, total = store.list_entities_page(
        EntityFilter.model_validate(
            {"query": "alpha concept\nmentions", "statusFilter": ALL_STATUSES, "limit": 1}
        )
    )
    assert [e.id for e in entities] == []
    assert total == 0


def test_a_prefilter_false_positive_does_not_consume_the_window(
    store: FalkorGraphStore,
) -> None:
    """A false positive that sorts ahead of a genuine match: `beta` spans the
    query across two observations, `omega` holds it inside one. A server-side
    LIMIT of 1 would spend the window on `beta` and report no items at all."""
    seed(store)
    omega = store.create_entity(
        EntityCreate.model_validate(
            {
                "name": "Omega Concept",
                "entityType": "concept",
                "observations": ["the doodads\nomega line"],
            }
        )
    )
    entities, total = store.list_entities_page(
        EntityFilter.model_validate(
            {"query": "doodads\nomega", "statusFilter": ALL_STATUSES, "limit": 1}
        )
    )
    assert [e.id for e in entities] == [omega.id]
    assert total == 1


def test_limit_does_not_count_unmigrated_nodes_outside_the_window(
    store: FalkorGraphStore,
) -> None:
    """The NULL-status backfill trigger only sees the limited window. Nodes that
    predate the read index and sort *behind* it are tolerated by every prefilter
    predicate, so a server-side count would score them all as matches."""
    ids = seed(store)
    store._query(
        "MATCH (n:_Entity) WHERE n.id <> $id REMOVE n._status, n._type, n._name, n._search",
        {"id": ids["alpha"]},
    )
    entities, total = store.list_entities_page(
        EntityFilter.model_validate({"entityType": "concept", "limit": 1})
    )
    assert [e.id for e in entities] == [ids["alpha"]]
    assert total == 1
    assert unindexed_count(store) == 0


def test_limit_rejects_zero_and_negatives() -> None:
    for value in (0, -1):
        with pytest.raises(ValueError):
            EntityFilter.model_validate({"limit": value})
