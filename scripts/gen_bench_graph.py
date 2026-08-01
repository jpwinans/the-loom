"""Synthetic large-graph generator for the Tapestry scale benchmark.

Builds the `tapestry-bench` graph — the graph Task 8's recorded 50k/100k
benchmark measures `export-bundle`/`visualize` assembly against, and the
guardrails in `theloom/viz/analytics.py` / `theloom/viz/bundle.py` were tuned
against. **Never** targets `default` or any graph you actually use — the graph
name is a hard-coded constant, not a flag, so the script cannot be pointed at
one by accident.

Run: `uv run python scripts/gen_bench_graph.py` (defaults to 50,000 entities /
100,000 relations) or `--entities N --relations M` for a smaller sanity-check
run. The output graph is **not committed** — it is tens of MB of FalkorDB +
Redis state, rebuilt on demand; nothing in CI builds or depends on it.

No embeddings are seeded: this script never imports
`theloom.operations.semantic` / `theloom.semantic.embed`, so it never touches
`get_embedder` or the fastembed model — the same "model-free" discipline
every other viz test/seed script follows.

Entities are distributed round-robin across the 19 `EntityType` values, each
with empty `observations` (no text needed since nothing here is embedded).
Relations are mostly the non-causal `related_to` type (explicit
`"polarity": null`), with a `--causal-fraction` slice drawn from the six
`CAUSAL_RELATION_TYPES` (`causes`, `enables`, `requires`, `inhibits`,
`amplifies`, `dampens`; polarity from `CAUSAL_POLARITY_DEFAULTS`) so the
Systems/loop-detection analytics have a realistic causal subgraph to chew on,
and the remaining non-causal fraction spreads thinly across the other
structural/epistemic types for variety. Endpoints are drawn uniformly at
random from the created entity set — this is a synthetic stress graph, not a
semantically meaningful one.

Performance notes (see the Task 6 research this script was written from):
entities go through `store.create_entity` — the only entity-write primitive
`FalkorGraphStore` exposes, one Cypher round trip per entity, since there is
no batch/`UNWIND` entity-creation method in the store today. Relations go
through the batched `store.create_relations` (grouped server-side by relation
type via `UNWIND`, so a chunk spanning all ~15 relation types costs at most
~15 Cypher queries, not one query per relation), chunked at `_RELATION_BATCH`
so no single query payload gets unreasonably large. This deliberately does
NOT go through `theloom.operations.bulk.bulk_import`: its per-relation
`store.read_relation(...)` dedup check would cost one extra query per
relation at this volume (100k extra round trips) that a fresh synthetic graph
has no use for.
"""

from __future__ import annotations

import argparse
import random
import time

from falkordb import FalkorDB

from theloom.config import load_config
from theloom.model import (
    CAUSAL_POLARITY_DEFAULTS,
    CAUSAL_RELATION_TYPES,
    EntityCreate,
    EntityType,
    RelationCreate,
    RelationType,
)
from theloom.store.multigraph import MultiGraph

# Hard-coded, deliberately not a CLI flag: this script must never be pointed
# at a graph that holds real data. It writes tens of thousands of synthetic
# entities, so an accidental target would be destructive to reverse.
GRAPH_NAME = "tapestry-bench"

# How many RelationCreate specs go into one `store.create_relations(...)`
# call — that method has no internal chunking of its own, so this keeps each
# per-relation-type $rows payload a bounded size rather than one giant list.
_RELATION_BATCH = 5_000

_CAUSAL_TYPES = tuple(CAUSAL_RELATION_TYPES)
_NON_CAUSAL_TYPES = tuple(t for t in RelationType if t not in CAUSAL_RELATION_TYPES)
# Within the non-causal slice, `related_to` dominates (plain structural
# connective tissue); the rest of `_NON_CAUSAL_TYPES` gets the remainder, for
# a little type diversity without it costing anything semantically.
_RELATED_TO_FRACTION = 0.8


def _build_entities(count: int) -> list[EntityCreate]:
    types = list(EntityType)
    return [
        EntityCreate.model_validate(
            {
                "name": f"bench-entity-{i}",
                "entityType": types[i % len(types)].value,
                "observations": [],
            }
        )
        for i in range(count)
    ]


def _random_relation_type(
    rng: random.Random, causal_fraction: float
) -> tuple[RelationType, str | None]:
    if rng.random() < causal_fraction:
        relation_type = rng.choice(_CAUSAL_TYPES)
        return relation_type, CAUSAL_POLARITY_DEFAULTS[relation_type]
    if rng.random() < _RELATED_TO_FRACTION:
        return RelationType.RELATED_TO, None
    return rng.choice(_NON_CAUSAL_TYPES), None


def _build_relations(
    count: int, entity_ids: list[str], causal_fraction: float, rng: random.Random
) -> list[RelationCreate]:
    n = len(entity_ids)
    relations: list[RelationCreate] = []
    for _ in range(count):
        from_id = entity_ids[rng.randrange(n)]
        to_id = entity_ids[rng.randrange(n)]
        while to_id == from_id:
            to_id = entity_ids[rng.randrange(n)]
        relation_type, polarity = _random_relation_type(rng, causal_fraction)
        relations.append(
            RelationCreate.model_validate(
                {
                    "from": from_id,
                    "to": to_id,
                    "relationType": relation_type.value,
                    "polarity": polarity,
                }
            )
        )
    return relations


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--entities", type=int, default=50_000, help="Entity count (default: 50000)."
    )
    parser.add_argument(
        "--relations", type=int, default=100_000, help="Relation count (default: 100000)."
    )
    parser.add_argument(
        "--causal-fraction",
        type=float,
        default=0.2,
        help="Fraction of relations drawn from the six causal types (default: 0.2).",
    )
    parser.add_argument(
        "--seed", type=int, default=42, help="RNG seed, for a reproducible synthetic graph."
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    rng = random.Random(args.seed)

    config = load_config()
    db = FalkorDB(host=config.host, port=config.port)
    multi = MultiGraph(db, db.connection, default_graph=config.default_graph)

    if multi.has_graph(GRAPH_NAME):
        multi.delete_graph(GRAPH_NAME)
    multi.create_graph(GRAPH_NAME)
    store = multi.get_store(GRAPH_NAME)

    start = time.monotonic()
    entity_ids = [store.create_entity(spec).id for spec in _build_entities(args.entities)]
    entities_elapsed = time.monotonic() - start
    print(f"  entities: {len(entity_ids)} in {entities_elapsed:.1f}s")

    relation_start = time.monotonic()
    relations = _build_relations(args.relations, entity_ids, args.causal_fraction, rng)
    for offset in range(0, len(relations), _RELATION_BATCH):
        store.create_relations(relations[offset : offset + _RELATION_BATCH])
    relations_elapsed = time.monotonic() - relation_start
    print(f"  relations: {len(relations)} in {relations_elapsed:.1f}s")

    total = time.monotonic() - start
    print(
        f"Built graph '{GRAPH_NAME}': {len(entity_ids)} entities, "
        f"{len(relations)} relations. Total: {total:.1f}s."
    )


if __name__ == "__main__":
    main()
