"""CWSG analogy transfer — Copying with Substitution and Generation.

Gentner's SME three-step algorithm with a Hofstadter concept-slippage
pre-generation step:

0. Systematicity filter: only relations connected to the matched relational
   structure transfer (computeConnectedMappedIds, one hop of propagation).
1. Copy: source relations with >=1 mapped endpoint and no existing target edge.
2. Substitute: mapped endpoints -> target IDs, unmapped -> ``__NOVEL__{srcId}``.
2.5. Slippage: only when temperature>0 AND allEntities/allRelations are given;
     find_concept_slippages seeds creative alternative proposals.
3. Generate: EntityProposal per novel placeholder (deduped, provenance
   observations), plus slippage alternatives; confidence from
   compute_analogy_confidence.

Optionally computes absence-surprise scoring and runs a Keane IAM adaptability
filter (rejected proposals dropped). This is the synchronous core; the async
purpose-relevance enhancement is not implemented here — so options.purpose /
generalizationBias / embeddingManager are accepted but unused.
"""

from __future__ import annotations

from typing import Any

from theloom.analysis.absence_surprise import score_transfer_absences
from theloom.analysis.adaptability import assess_transfer_adaptability
from theloom.analysis.analogy_confidence import compute_analogy_confidence
from theloom.analysis.slippage import find_concept_slippages

NOVEL_PREFIX = "__NOVEL__"
DEFAULT_SLIPPAGE_SCORE = 0.5
SLIPPAGE_CONFIDENCE_BASE = 0.6
DEFAULT_GENERALIZATION_BIAS = 0.7
GENERALIZATION_BOOST_FACTOR = 0.3


def _js_number(value: float) -> str:
    """Render a number the way JS String(number) would (integers without a
    trailing '.0'), used for the 'Temperature: {t}' observation."""
    if value == int(value):
        return str(int(value))
    return repr(float(value))


def cwsg_transfer(
    mapping_result: dict[str, Any], options: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Run CWSG on a cross-domain mapping result, returning novel entity
    proposals plus copied/substituted relation bookkeeping."""
    options = options or {}

    temp_opt = options.get("temperature")
    temperature = max(0.0, min(1.0, temp_opt if temp_opt is not None else 0.0))

    source_relations = mapping_result.get("sourceRelations") or []
    target_relations = mapping_result.get("targetRelations") or []
    mappings = mapping_result["mappings"]

    source_to_target: dict[str, str] = {}
    for pair in mappings:
        source_to_target[pair["sourceId"]] = pair["targetId"]

    mapped_source_ids = {p["sourceId"] for p in mappings}

    target_edge_set = {f"{rel['from']}->{rel['to']}" for rel in target_relations}

    connected_source_ids = _compute_connected_mapped_ids(source_relations, mapped_source_ids)

    # -- Step 1: Copy ---------------------------------------------------------
    copied_relations: list[dict[str, Any]] = []
    systematicity_excluded = 0

    for rel in source_relations:
        from_mapped = rel["from"] in source_to_target
        to_mapped = rel["to"] in source_to_target

        if not from_mapped and not to_mapped:
            continue

        from_connected = rel["from"] in connected_source_ids
        to_connected = rel["to"] in connected_source_ids
        if not from_connected and not to_connected:
            systematicity_excluded += 1
            continue

        if from_mapped and to_mapped:
            target_from = source_to_target[rel["from"]]
            target_to = source_to_target[rel["to"]]
            if f"{target_from}->{target_to}" in target_edge_set:
                continue

        copied_relations.append(
            {
                "sourceRelation": rel,
                "sourceFromId": rel["from"],
                "sourceToId": rel["to"],
                "fromMapped": from_mapped,
                "toMapped": to_mapped,
            }
        )

    # -- Step 2: Substitute ---------------------------------------------------
    substituted_relations: list[dict[str, Any]] = []

    for copied in copied_relations:
        from_mapped = copied["fromMapped"]
        to_mapped = copied["toMapped"]

        target_from_id = (
            source_to_target[copied["sourceFromId"]]
            if from_mapped
            else f"{NOVEL_PREFIX}{copied['sourceFromId']}"
        )
        target_to_id = (
            source_to_target[copied["sourceToId"]]
            if to_mapped
            else f"{NOVEL_PREFIX}{copied['sourceToId']}"
        )

        substituted_relations.append(
            {
                "sourceRelation": copied["sourceRelation"],
                "targetFromId": target_from_id,
                "targetToId": target_to_id,
                "fromIsNovel": not from_mapped,
                "toIsNovel": not to_mapped,
                "relationType": copied["sourceRelation"]["relationType"],
            }
        )

    # -- Step 2.5: Concept slippage ------------------------------------------
    slippage_map: dict[str, list[dict[str, Any]]] = {}
    slippage_augmentations: list[dict[str, Any]] = []
    all_entities = options.get("allEntities")
    all_relations = options.get("allRelations")

    if temperature > 0 and all_entities and all_relations:
        # Insertion-ordered unique novel source IDs (JS Set iteration order).
        novel_source_ids: dict[str, None] = {}
        for sub in substituted_relations:
            if sub["fromIsNovel"]:
                novel_source_ids.setdefault(sub["targetFromId"].replace(NOVEL_PREFIX, "", 1), None)
            if sub["toIsNovel"]:
                novel_source_ids.setdefault(sub["targetToId"].replace(NOVEL_PREFIX, "", 1), None)

        for source_entity_id in novel_source_ids:
            entity_exists = any(e["id"] == source_entity_id for e in all_entities)
            if not entity_exists:
                continue

            try:
                slippage_result = find_concept_slippages(
                    all_entities, all_relations, source_entity_id, {"temperature": temperature}
                )
                if slippage_result["candidates"]:
                    slippage_map[source_entity_id] = slippage_result["candidates"]
                    slippage_augmentations.append(
                        {
                            "sourceEntityId": source_entity_id,
                            "candidates": slippage_result["candidates"],
                        }
                    )
            except Exception:
                # Slippage failure is non-fatal — fall through to standard generation.
                continue

    # -- Step 3: Generate -----------------------------------------------------
    structural_preservation = mapping_result["quality"]["structuralPreservation"]
    slippage_score_opt = options.get("slippageScore")
    slippage_score = DEFAULT_SLIPPAGE_SCORE if slippage_score_opt is None else slippage_score_opt
    confidence = compute_analogy_confidence(
        structural_preservation,
        slippage_score,
        options.get("interestingnessScore"),
        options.get("confidenceWeights"),
    )

    novel_proposals: dict[str, dict[str, Any]] = {}

    for sub in substituted_relations:
        if sub["fromIsNovel"]:
            _add_or_update_proposal(
                novel_proposals,
                sub["targetFromId"],
                sub["sourceRelation"],
                "from",
                sub,
                mapping_result,
                confidence,
            )
        if sub["toIsNovel"]:
            _add_or_update_proposal(
                novel_proposals,
                sub["targetToId"],
                sub["sourceRelation"],
                "to",
                sub,
                mapping_result,
                confidence,
            )

    proposals = list(novel_proposals.values())

    # Slippage-derived creative alternatives.
    if slippage_map:
        for source_entity_id, candidates in slippage_map.items():
            novel_placeholder_id = f"{NOVEL_PREFIX}{source_entity_id}"
            related_subs = [
                sub
                for sub in substituted_relations
                if (sub["fromIsNovel"] and sub["targetFromId"] == novel_placeholder_id)
                or (sub["toIsNovel"] and sub["targetToId"] == novel_placeholder_id)
            ]

            for candidate in candidates:
                relations: list[dict[str, Any]] = []
                for sub in related_subs:
                    is_from_endpoint = (
                        sub["fromIsNovel"] and sub["targetFromId"] == novel_placeholder_id
                    )
                    other_endpoint_id = (
                        sub["targetToId"] if is_from_endpoint else sub["targetFromId"]
                    )
                    other_is_novel = sub["toIsNovel"] if is_from_endpoint else sub["fromIsNovel"]

                    if not other_is_novel:
                        new_relation = {
                            "targetId": other_endpoint_id,
                            "relationType": sub["relationType"],
                            "direction": "outgoing" if is_from_endpoint else "incoming",
                        }
                        is_duplicate = any(
                            r["targetId"] == new_relation["targetId"]
                            and r["relationType"] == new_relation["relationType"]
                            and r["direction"] == new_relation["direction"]
                            for r in relations
                        )
                        if not is_duplicate:
                            relations.append(new_relation)

                slippage_confidence = SLIPPAGE_CONFIDENCE_BASE * candidate["score"]
                summary = candidate["slippagePath"]["summary"]
                proposals.append(
                    {
                        "entity": {
                            "name": f"{candidate['entityName']} (slippage)",
                            "entityType": candidate["entityType"],
                            "observations": [
                                (
                                    "Generated by concept slippage from "
                                    f'"{source_entity_id}" in {mapping_result["sourceDomain"]}'
                                ),
                                (
                                    f"Slippage target: {candidate['entityName']} "
                                    f"(score: {candidate['score']:.3f}, "
                                    f"distance: {candidate['distance']:.3f})"
                                ),
                                f"Slippage path: {summary}",
                                f"Temperature: {_js_number(temperature)}",
                            ],
                        },
                        "relations": relations,
                        "rationale": (
                            f'Concept slippage: "{source_entity_id}" slipped to '
                            f'"{candidate["entityName"]}" — {summary}'
                        ),
                        "confidence": slippage_confidence,
                        "strategy": "analogy_transfer",
                    }
                )

    # -- Optional: absence surprise ------------------------------------------
    absence_surprise: dict[str, Any] | None = None
    if options.get("computeAbsenceSurprise") and all_entities and all_relations:
        absence_surprise = score_transfer_absences(
            all_entities,
            all_relations,
            {
                "copiedRelations": copied_relations,
                "substitutedRelations": substituted_relations,
                "proposals": proposals,
                "systematicityExcluded": systematicity_excluded,
                "totalSourceRelations": len(source_relations),
                "slippageAugmentations": slippage_augmentations if slippage_augmentations else None,
                "temperature": temperature,
            },
            mapping_result,
            options.get("absenceSurpriseOptions"),
        )

    # -- Optional Step 4: adaptability filter --------------------------------
    if options.get("assessAdaptability") and options.get("targetEntityIds"):
        target_entity_ids = options["targetEntityIds"]
        target_rels = options.get("targetRelationsForAdaptability") or []
        entity_type_map = options.get("entityTypeMap") or {}

        adaptability_results = assess_transfer_adaptability(
            proposals,
            substituted_relations,
            target_rels,
            target_entity_ids,
            entity_type_map,
            options.get("adaptabilityOptions"),
        )

        filtered: list[dict[str, Any]] = []
        filtered_adaptability: list[dict[str, Any]] = []
        for i in range(len(proposals)):
            result = adaptability_results[i]
            if result["decision"] != "reject":
                filtered.append(proposals[i])
                filtered_adaptability.append(result)

        return _build_result(
            copied_relations,
            substituted_relations,
            filtered,
            systematicity_excluded,
            len(source_relations),
            slippage_augmentations,
            temperature,
            absence_surprise,
            filtered_adaptability,
        )

    return _build_result(
        copied_relations,
        substituted_relations,
        proposals,
        systematicity_excluded,
        len(source_relations),
        slippage_augmentations,
        temperature,
        absence_surprise,
        None,
    )


def _build_result(
    copied_relations: list[dict[str, Any]],
    substituted_relations: list[dict[str, Any]],
    proposals: list[dict[str, Any]],
    systematicity_excluded: int,
    total_source_relations: int,
    slippage_augmentations: list[dict[str, Any]],
    temperature: float,
    absence_surprise: dict[str, Any] | None,
    adaptability: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Assemble the result; optional keys are omitted when undefined, so
    absent fields drop out of the JSON entirely."""
    result: dict[str, Any] = {
        "copiedRelations": copied_relations,
        "substitutedRelations": substituted_relations,
        "proposals": proposals,
        "systematicityExcluded": systematicity_excluded,
        "totalSourceRelations": total_source_relations,
        "temperature": temperature,
    }
    if slippage_augmentations:
        result["slippageAugmentations"] = slippage_augmentations
    if absence_surprise is not None:
        result["absenceSurprise"] = absence_surprise
    if adaptability is not None:
        result["adaptability"] = adaptability
    return result


def _compute_connected_mapped_ids(
    source_relations: list[dict[str, Any]], mapped_source_ids: set[str]
) -> set[str]:
    """Source IDs connected to the matched relational structure (both-mapped
    relations), with one hop of propagation to adjacent mapped/unmapped IDs."""
    core_connected: set[str] = set()
    for rel in source_relations:
        if rel["from"] in mapped_source_ids and rel["to"] in mapped_source_ids:
            core_connected.add(rel["from"])
            core_connected.add(rel["to"])

    connected = set(core_connected)
    for rel in source_relations:
        if rel["from"] in core_connected and rel["to"] in mapped_source_ids:
            connected.add(rel["to"])
        if rel["to"] in core_connected and rel["from"] in mapped_source_ids:
            connected.add(rel["from"])

    for rel in source_relations:
        if rel["from"] in core_connected or rel["to"] in core_connected:
            connected.add(rel["from"])
            connected.add(rel["to"])

    return connected


def _add_or_update_proposal(
    proposals: dict[str, dict[str, Any]],
    novel_placeholder_id: str,
    source_relation: dict[str, Any],
    endpoint_role: str,
    sub: dict[str, Any],
    mapping_result: dict[str, Any],
    confidence: float,
) -> None:
    """Create or extend the proposal for a novel placeholder endpoint."""
    source_entity_id = novel_placeholder_id.replace(NOVEL_PREFIX, "", 1)

    unmapped_info = next(
        (
            u
            for u in mapping_result["unmapped"]
            if u["entityId"] == source_entity_id and u["domain"] == "source"
        ),
        None,
    )

    entity_name = (
        f"{unmapped_info['entityName']} (analogy)"
        if unmapped_info
        else f"Novel entity from {mapping_result['sourceDomain']}"
    )
    entity_type = unmapped_info["entityType"] if unmapped_info else "concept"

    other_endpoint_id = sub["targetToId"] if endpoint_role == "from" else sub["targetFromId"]
    other_is_novel = sub["toIsNovel"] if endpoint_role == "from" else sub["fromIsNovel"]
    direction = "outgoing" if endpoint_role == "from" else "incoming"

    inferred_obs = (
        f"Inferred from source relation: {source_relation['from']} "
        f"-[{source_relation['relationType']}]-> {source_relation['to']}"
    )

    existing = proposals.get(novel_placeholder_id)

    if existing is not None:
        if not other_is_novel:
            new_relation = {
                "targetId": other_endpoint_id,
                "relationType": sub["relationType"],
                "direction": direction,
            }
            is_duplicate = any(
                r["targetId"] == new_relation["targetId"]
                and r["relationType"] == new_relation["relationType"]
                and r["direction"] == new_relation["direction"]
                for r in existing["relations"]
            )
            if not is_duplicate:
                existing["relations"].append(new_relation)
                existing["entity"]["observations"].append(inferred_obs)
        return

    relations: list[dict[str, Any]] = []
    if not other_is_novel:
        relations.append(
            {
                "targetId": other_endpoint_id,
                "relationType": sub["relationType"],
                "direction": direction,
            }
        )

    proposals[novel_placeholder_id] = {
        "entity": {
            "name": entity_name,
            "entityType": entity_type,
            "observations": [
                (
                    f"Generated by analogy transfer from {mapping_result['sourceDomain']} "
                    f"to {mapping_result['targetDomain']}"
                ),
                f"Source entity: {source_entity_id}",
                inferred_obs,
            ],
        },
        "relations": relations,
        "rationale": (
            f'CWSG analogy transfer: source entity "{source_entity_id}" has structural role '
            f"in {mapping_result['sourceDomain']} with no target correspondence "
            f"in {mapping_result['targetDomain']}"
        ),
        "confidence": confidence,
        "strategy": "analogy_transfer",
    }
