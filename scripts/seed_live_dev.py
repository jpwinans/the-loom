"""Seed a minimal live graph set for the live-mode e2e (no embeddings).

Run: `uv run python scripts/seed_live_dev.py`. Idempotent-ish: it wipes and
recreates the two demo graphs it owns. Uses MultiGraph + config directly (the
single config path), never touching a user's real graphs beyond these two
names — in particular it never enumerates or assembles the caller's default
graph.
"""

from __future__ import annotations

from falkordb import FalkorDB

from theloom.config import load_config
from theloom.model import EntityCreate, RelationCreate
from theloom.store.multigraph import MultiGraph


def main() -> None:
    config = load_config()
    db = FalkorDB(host=config.host, port=config.port)
    multi = MultiGraph(db, db.connection, default_graph=config.default_graph)

    for name in ("tapestry-dev", "tapestry-alt"):
        if multi.has_graph(name) and name != multi.default_graph:
            multi.delete_graph(name)
        multi.create_graph(name)

    dev = multi.get_store("tapestry-dev")
    ids: dict[str, str] = {}
    for entity_name, kind in (
        ("Resource stock", "variable"),
        ("Consumption rate", "variable"),
        ("Scarcity signal", "variable"),
        ("Conservation policy", "claim"),
    ):
        entity = dev.create_entity(
            EntityCreate.model_validate(
                {"name": entity_name, "entityType": kind, "observations": [f"{entity_name} note."]}
            )
        )
        ids[entity_name] = entity.id

    for src, dst, rel, pol in (
        ("Resource stock", "Consumption rate", "inhibits", "-"),
        ("Consumption rate", "Scarcity signal", "causes", "+"),
        ("Scarcity signal", "Resource stock", "inhibits", "-"),
    ):
        dev.create_relation(
            RelationCreate.model_validate(
                {"from": ids[src], "to": ids[dst], "relationType": rel, "polarity": pol}
            )
        )

    alt = multi.get_store("tapestry-alt")
    alt.create_entity(
        EntityCreate.model_validate(
            {"name": "Alt-graph seed", "entityType": "concept", "observations": ["Second graph."]}
        )
    )
    print("Seeded tapestry-dev (4 entities, 3 causal relations) and tapestry-alt (1 entity).")


if __name__ == "__main__":
    main()
