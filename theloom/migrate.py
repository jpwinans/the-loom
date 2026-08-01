"""Graph-snapshot importer.

Reads a graph folder — per-graph ``<name>.json`` files in the
``{nodes, edges, metadata}`` snapshot format plus ``_bridges.json`` — and loads
it into FalkorDB via the MultiGraph facade. Docs are imported verbatim (ids,
timestamps, key presence preserved) so the store serves exactly what the
snapshot recorded.

Wipes the prefix first, so importing a snapshot is idempotent.

Two modes:

- **verbatim** (default) — imported documents are historical state, not new
  mutations; no events are appended. The store serves exactly what the snapshot
  recorded.
- **event replay** (``replay_events=True``) — every imported doc is additionally
  recorded as an ``entity_created``/``relation_created`` event, so the store's
  history starts clean with the migration itself as the first chapter. Use this
  for the real cutover migration.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from theloom.store.multigraph import MultiGraph


def import_folder(
    folder: Path | str, multi: MultiGraph, *, replay_events: bool = False
) -> dict[str, Any]:
    """Import every graph + bridges from a snapshot folder. Returns a summary."""
    root = Path(folder)
    multi.wipe()

    summary: dict[str, Any] = {"graphs": {}, "bridges": 0}
    if replay_events:
        summary["events"] = 0
    for graph_file in sorted(root.glob("*.json")):
        if graph_file.name.startswith("_"):
            continue
        name = graph_file.stem
        data = json.loads(graph_file.read_text(encoding="utf-8"))
        multi.register_graph(name)
        store = multi.get_store(name)
        nodes = data.get("nodes") or []
        edges = data.get("edges") or []
        for node in nodes:
            store.import_entity_doc(node)
        for edge in edges:
            store.import_relation_doc(edge)
        metadata = data.get("metadata") or {}
        store.set_metadata_doc(metadata)
        summary["graphs"][name] = {"entities": len(nodes), "relations": len(edges)}
        if replay_events:
            summary["events"] += store.replay_creation_events(nodes, edges)

    bridges_file = root / "_bridges.json"
    if bridges_file.exists():
        bridges = json.loads(bridges_file.read_text(encoding="utf-8")).get("bridges") or []
        for bridge in bridges:
            multi.bridges.import_bridge_doc(bridge)
        summary["bridges"] = len(bridges)

    # Entity vectors exported alongside the snapshot (`_vectors.json`,
    # {entityId: [768 floats]}). Imported verbatim so stored vectors match the
    # exported ones exactly; the default graph holds them (the export format
    # predates per-graph vector files).
    vectors_file = root / "_vectors.json"
    if vectors_file.exists():
        vectors = json.loads(vectors_file.read_text(encoding="utf-8"))
        store = multi.get_store()
        store.ensure_vector_index()
        for entity_id, vector in vectors.items():
            store.set_entity_vector(entity_id, vector)
        summary["vectors"] = len(vectors)
    return summary
