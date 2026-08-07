"""export-graph — a compact, zero-infrastructure JSON artifact.

The whole point is portability: a plain JSON file readable with nothing but a
text editor, no FalkorDB or Loom CLI required to consume it.
Unlike ``export-bundle``/``visualize`` (theloom/viz/bundle.py,
theloom/viz/html.py) this ships no TapestryBundle sections — no analytics, no
temporal or semantic layers, no HTML — because it is a data export, not a
visualization. It reuses the same store read paths those two use
(``store.list_entities`` / ``store.list_relations``) rather than duplicating
them, just without the bundle assembly wrapped around them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import Field

from theloom.errors import NotFoundError, OperationError
from theloom.model import EntityFilter, EntityStatus, EntityType
from theloom.operations.common import CommandInput
from theloom.store.multigraph import MultiGraph
from theloom.timeutil import iso_now

Doc = dict[str, Any]

# Refuse to write an export above this estimated size unless `force` is set —
# same refuse-by-default/opt-out-explicitly shape as update_codebase's shrink
# guard (theloom/operations/extraction.py). 200MB is comfortably past any
# graph this CLI has been exercised against but still catches a
# scope-by-accident full export of something huge before it hits disk.
MAX_EXPORT_BYTES = 200 * 1024 * 1024


class ExportGraphInput(CommandInput):
    graph: str | None = None
    output: str
    include_superseded: bool = Field(default=False, alias="includeSuperseded")
    entity_types: list[EntityType] | None = Field(default=None, alias="entityTypes")
    force: bool = False


def _compact_entity(entity: Doc) -> Doc:
    return {
        "id": entity["id"],
        "name": entity["name"],
        "entityType": entity["entityType"],
        "observations": entity.get("observations", []),
    }


def _compact_relation(relation: Doc) -> Doc:
    return {
        "from": relation["from"],
        "to": relation["to"],
        "relationType": relation["relationType"],
        "evidence": relation.get("evidence"),
    }


def export_graph(params: ExportGraphInput, multi: MultiGraph) -> Doc:
    target = params.graph or multi.default_graph
    if params.graph and not multi.has_graph(params.graph):
        raise NotFoundError(
            f"Graph '{params.graph}' not found. Use list_graphs to see available graphs."
        )
    store = multi.get_store(target)

    statuses = [EntityStatus.ACTIVE]
    if params.include_superseded:
        statuses.append(EntityStatus.SUPERSEDED)
    entities = store.list_entities(EntityFilter(statusFilter=statuses))
    if params.entity_types:
        allowed = set(params.entity_types)
        entities = [e for e in entities if e.entity_type in allowed]
    kept_ids = {e.id for e in entities}

    relations = [r for r in store.list_relations() if r.from_ in kept_ids and r.to in kept_ids]

    entity_docs = [
        _compact_entity(e.model_dump(by_alias=True, exclude_unset=True)) for e in entities
    ]
    relation_docs = [
        _compact_relation(r.model_dump(by_alias=True, exclude_unset=True)) for r in relations
    ]

    payload = {
        "meta": {
            "graph": target,
            "generated": iso_now(),
            "counts": {"entities": len(entity_docs), "relations": len(relation_docs)},
        },
        "entities": entity_docs,
        "relations": relation_docs,
    }

    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    if len(data) > MAX_EXPORT_BYTES and not params.force:
        raise OperationError(
            f"Export would write ~{len(data) / 1024 / 1024:.1f}MB, over the "
            f"{MAX_EXPORT_BYTES / 1024 / 1024:.0f}MB guard. Pass force: true to write it anyway."
        )

    target_path = Path(params.output)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_bytes(data)

    return {
        "path": str(target_path),
        "entityCount": len(entity_docs),
        "relationCount": len(relation_docs),
        "bytes": len(data),
    }
