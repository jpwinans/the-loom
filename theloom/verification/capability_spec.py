"""Capability Spec DSL.

Extends graph verification with *capability invariants* — structural
expectations describing what the graph should be able to do. When validated,
violations become a gap list, each carrying a ``suggestedAction`` that bridges
to the Entity Proposal Engine.

The coverage and coupling checks reuse the shared violation generators in
``theloom/verification/metrics.py`` (``coverage`` / ``coupling``), the same
functions the ``check-capabilities`` command calls, so output is byte-identical
between the two surfaces; the completeness, test-coverage and
pattern-consistency checks are ported directly.

Violation shape: ``{capabilityName, violationType, message, suggestedAction,
elementId?}`` where violationType is one of completeness | coverage | coupling |
test_coverage | pattern.
"""

from __future__ import annotations

from typing import Any

Doc = dict[str, Any]

_ALL_STATUSES = ["active", "superseded", "deprecated", "retracted", "investigating"]


def _to_doc(obj: Any) -> Doc:
    """Normalize a store row (Pydantic model or plain dict) to a wire dict."""
    if isinstance(obj, dict):
        return obj
    dumped: Doc = obj.model_dump(by_alias=True, exclude_unset=True)
    return dumped


def _list_entity_docs(store: Any) -> list[Doc]:
    from theloom.model import EntityFilter

    result = store.list_entities(EntityFilter.model_validate({"statusFilter": _ALL_STATUSES}))
    return [_to_doc(e) for e in result]


def _list_relation_docs(store: Any) -> list[Doc]:
    return [_to_doc(r) for r in store.list_relations()]


class CapabilitySpec:
    """Fluent DSL for declaring capability invariants that can be verified."""

    def __init__(self) -> None:
        self._capabilities: list[Doc] = []

    # -- capability declarations ----------------------------------------------

    def require_type_completeness(self, types: list[str]) -> CapabilitySpec:
        name = f"type-completeness({','.join(types)})"

        def check(store: Any) -> list[Doc]:
            entities = _list_entity_docs(store)
            present = {e["entityType"] for e in entities}
            return [
                {
                    "capabilityName": name,
                    "violationType": "completeness",
                    "message": f"Entity type '{t}' has no instances in the graph",
                    "suggestedAction": f"Create at least one entity of type '{t}'",
                }
                for t in types
                if t not in present
            ]

        self._capabilities.append({"name": name, "violationType": "completeness", "check": check})
        return self

    def require_semantic_coverage(
        self, parent_type: str, child_type: str, relation_type: str
    ) -> CapabilitySpec:
        cap_name = f"coverage({parent_type}->{child_type} via {relation_type})"

        def check(store: Any) -> list[Doc]:
            from theloom.verification.metrics import coverage

            entities = _list_entity_docs(store)
            relations = _list_relation_docs(store)
            result = coverage(entities, relations, parent_type, child_type, relation_type)
            violations: list[Doc] = result["violations"]
            return violations

        self._capabilities.append({"name": cap_name, "violationType": "coverage", "check": check})
        return self

    def require_coupling_below(self, metric: str, threshold: float) -> CapabilitySpec:
        cap_name = f"coupling({metric}<{threshold})"

        def check(store: Any) -> list[Doc]:
            from theloom.verification.metrics import coupling

            entities = _list_entity_docs(store)
            relations = _list_relation_docs(store)
            result = coupling(entities, relations, metric, threshold)
            violations: list[Doc] = result["violations"]
            return violations

        self._capabilities.append({"name": cap_name, "violationType": "coupling", "check": check})
        return self

    def require_test_coverage(self) -> CapabilitySpec:
        cap_name = "test-coverage"

        def check(store: Any) -> list[Doc]:
            entities = _list_entity_docs(store)
            relations = _list_relation_docs(store)
            procedures = {e["id"] for e in entities if e["entityType"] == "procedure"}
            composites = [e for e in entities if e["entityType"] in ("system", "procedure")]
            violations: list[Doc] = []
            for composite in composites:
                if composite["entityType"] == "procedure":
                    continue
                has_test = any(
                    (r["from"] == composite["id"] and r["to"] in procedures)
                    or (r["to"] == composite["id"] and r["from"] in procedures)
                    for r in relations
                )
                if not has_test:
                    violations.append(
                        {
                            "capabilityName": cap_name,
                            "violationType": "test_coverage",
                            "elementId": composite["id"],
                            "message": (
                                f"Entity '{composite['name']}' (type: {composite['entityType']}) "
                                f"has no linked test procedure"
                            ),
                            "suggestedAction": (
                                f"Create a 'procedure' entity describing how to test "
                                f"'{composite['name']}' and link it via 'tests'"
                            ),
                        }
                    )
            return violations

        self._capabilities.append(
            {"name": cap_name, "violationType": "test_coverage", "check": check}
        )
        return self

    def require_pattern_consistency(self, min_occurrences: int | None = None) -> CapabilitySpec:
        threshold = min_occurrences if min_occurrences is not None else 2
        cap_name = f"pattern-consistency(min={threshold})"

        def check(store: Any) -> list[Doc]:
            entities = _list_entity_docs(store)
            relations = _list_relation_docs(store)

            by_type: dict[str, list[Doc]] = {}
            for entity in entities:
                by_type.setdefault(entity["entityType"], []).append(entity)

            fingerprints: dict[str, Doc] = {}
            for entity in entities:
                outgoing = sorted(
                    {r["relationType"] for r in relations if r["from"] == entity["id"]}
                )
                incoming = sorted({r["relationType"] for r in relations if r["to"] == entity["id"]})
                fingerprints[entity["id"]] = {
                    "entityType": entity["entityType"],
                    "outgoingRelationTypes": outgoing,
                    "incomingRelationTypes": incoming,
                }

            violations: list[Doc] = []
            for entity_type, type_entities in by_type.items():
                if len(type_entities) < threshold + 1:
                    continue

                outgoing_counts: dict[str, int] = {}
                incoming_counts: dict[str, int] = {}
                for entity in type_entities:
                    fp = fingerprints[entity["id"]]
                    for rel_type in fp["outgoingRelationTypes"]:
                        outgoing_counts[rel_type] = outgoing_counts.get(rel_type, 0) + 1
                    for rel_type in fp["incomingRelationTypes"]:
                        incoming_counts[rel_type] = incoming_counts.get(rel_type, 0) + 1

                total_in_type = len(type_entities)

                for rel_type, count in outgoing_counts.items():
                    if count >= threshold and count < total_in_type:
                        for entity in type_entities:
                            fp = fingerprints[entity["id"]]
                            if rel_type not in fp["outgoingRelationTypes"]:
                                violations.append(
                                    {
                                        "capabilityName": cap_name,
                                        "violationType": "pattern",
                                        "elementId": entity["id"],
                                        "message": (
                                            f"Entity '{entity['name']}' (type: {entity_type}) is "
                                            f"missing outgoing '{rel_type}' relation that "
                                            f"{count}/{total_in_type} peers have"
                                        ),
                                        "suggestedAction": (
                                            f"Add a '{rel_type}' relation from "
                                            f"'{entity['name']}' to maintain structural "
                                            f"consistency with other '{entity_type}' entities"
                                        ),
                                    }
                                )

                for rel_type, count in incoming_counts.items():
                    if count >= threshold and count < total_in_type:
                        for entity in type_entities:
                            fp = fingerprints[entity["id"]]
                            if rel_type not in fp["incomingRelationTypes"]:
                                violations.append(
                                    {
                                        "capabilityName": cap_name,
                                        "violationType": "pattern",
                                        "elementId": entity["id"],
                                        "message": (
                                            f"Entity '{entity['name']}' (type: {entity_type}) is "
                                            f"missing incoming '{rel_type}' relation that "
                                            f"{count}/{total_in_type} peers have"
                                        ),
                                        "suggestedAction": (
                                            f"Add an incoming '{rel_type}' relation to "
                                            f"'{entity['name']}' to maintain structural "
                                            f"consistency with other '{entity_type}' entities"
                                        ),
                                    }
                                )

            return violations

        self._capabilities.append({"name": cap_name, "violationType": "pattern", "check": check})
        return self

    # -- derivation & validation ----------------------------------------------

    def derive_from_graph(self, store: Any) -> CapabilitySpec:
        entities = _list_entity_docs(store)
        present_types = list(dict.fromkeys(e["entityType"] for e in entities))
        if present_types:
            self.require_type_completeness(present_types)
        self.require_pattern_consistency()
        return self

    def get_capabilities(self) -> list[Doc]:
        return self._capabilities

    def validate(self, store: Any) -> Doc:
        all_violations: list[Doc] = []
        capability_results: list[Doc] = []

        for capability in self._capabilities:
            violations: list[Doc] = capability["check"](store)
            all_violations.extend(violations)
            capability_results.append(
                {
                    "name": capability["name"],
                    "pass": len(violations) == 0,
                    "violations": violations,
                }
            )

        passed_count = sum(1 for c in capability_results if c["pass"])
        failed_count = sum(1 for c in capability_results if not c["pass"])

        return {
            "pass": failed_count == 0,
            "totalCapabilities": len(capability_results),
            "passedCapabilities": passed_count,
            "failedCapabilities": failed_count,
            "violations": all_violations,
            "capabilities": capability_results,
        }

    def to_json(self) -> Doc:
        return {
            "capabilities": [
                {"name": c["name"], "violationType": c["violationType"]} for c in self._capabilities
            ]
        }
