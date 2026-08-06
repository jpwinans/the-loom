"""Structural Survey composite.

Structural analysis around one entity: ego subgraph, cycle detection, path
finding (needs a target), metapath
traversal, and a cross-type query. Every section runs inside :func:`time_section`
except path finding, which degrades to :func:`failed_section` without a target.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from theloom.composites.framework import failed_section, run_composite, time_section
from theloom.operations.algebra import (
    CrossTypeQueryInput,
    MetapathTraverseInput,
    cross_type_query,
    metapath_traverse,
)
from theloom.operations.analysis import (
    DetectCyclesInput,
    ExtractSubgraphInput,
    FindAllPathsInput,
    detect_cycles,
    extract_subgraph,
    find_all_paths,
)
from theloom.operations.common import CommandInput, UuidStr
from theloom.store.multigraph import MultiGraph

DEFAULT_EGO_DEPTH = 2


class StructuralSurveyInput(CommandInput):
    entity_id: UuidStr = Field(alias="entityId")
    depth: int | None = Field(default=None, ge=1)
    target: UuidStr | None = None
    max_depth: int | None = Field(default=None, ge=1, alias="maxDepth")
    metapath_patterns: list[str] | None = Field(default=None, alias="metapathPatterns")
    graph: str | None = None


def structural_survey(params: StructuralSurveyInput, multi: MultiGraph) -> dict[str, Any]:
    graph = params.graph
    entity_id = params.entity_id

    def _subgraph() -> dict[str, Any]:
        result = extract_subgraph(
            ExtractSubgraphInput.model_validate(
                {
                    "mode": "ego",
                    "entityId": entity_id,
                    "depth": params.depth if params.depth is not None else DEFAULT_EGO_DEPTH,
                    "graph": graph,
                }
            ),
            multi,
        )
        entities = [
            {"id": e["id"], "name": e["name"], "entityType": e["entityType"]}
            for e in result.get("entities", [])
        ]
        relations = [
            {"from": r["from"], "to": r["to"], "relationType": r["relationType"]}
            for r in result.get("relations", [])
        ]
        return {
            "entityCount": len(entities),
            "relationCount": len(relations),
            "entities": entities,
            "relations": relations,
        }

    def _cycles() -> dict[str, Any]:
        result = detect_cycles(
            DetectCyclesInput.model_validate({"includePaths": True, "graph": graph}), multi
        )
        # `cycles` is present only when hasCycle is true; omit otherwise
        # (never serialize null).
        out: dict[str, Any] = {"hasCycle": result["hasCycle"]}
        if "cycles" in result:
            out["cycles"] = result["cycles"]
        return out

    if params.target:

        def _paths() -> dict[str, Any]:
            result = find_all_paths(
                FindAllPathsInput.model_validate(
                    {
                        "source": entity_id,
                        "target": params.target,
                        "maxDepth": params.max_depth,
                        "graph": graph,
                    }
                ),
                multi,
            )
            return {"paths": result["paths"], "maxDepth": result["maxDepth"]}

        paths_section = time_section(_paths)
    else:
        paths_section = failed_section("No target entity specified -- skipping path finding")

    def _metapaths() -> list[dict[str, Any]]:
        patterns = params.metapath_patterns or []
        entries: list[dict[str, Any]] = []
        for pattern in patterns:
            result = metapath_traverse(
                MetapathTraverseInput.model_validate(
                    {"source": entity_id, "metapath": pattern, "graph": graph}
                ),
                multi,
            )
            entries.append({"pattern": pattern, "results": result["results"]})
        return entries

    def _cross_type() -> dict[str, Any]:
        result = cross_type_query(
            CrossTypeQueryInput.model_validate(
                {"source": entity_id, "target": params.target, "graph": graph}
            ),
            multi,
        )
        return {
            "plan": result["plan"],
            "value": result["value"],
            "path": result["path"],
            "crossTypeMetadata": result["crossTypeMetadata"],
        }

    return run_composite(
        [
            ("subgraph", _subgraph),
            ("cycles", _cycles),
            ("paths", paths_section),
            ("metapaths", _metapaths),
            ("crossType", _cross_type),
        ]
    )
