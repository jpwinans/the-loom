"""Work memory — the experiential layer, recorded natively in the graph.

``record-outcome`` is the write half of cross-session memory: after a piece of
work, the agent records *what it asked*, *what it concluded*, *which entities
it leaned on*, and *how that turned out*. Nothing new is invented to hold
that — the record is an ``evidence`` entity tagged ``map_layer: usage``, and
the citations are ordinary ``supports`` / ``questions`` edges to the entities
that were used. It therefore participates in every existing capability for
free: provenance, versioning, the event log, traversal, visualization.

Two deliberate non-behaviours:

- **Embedding is not automatic.** Like every other write command, this one
  leaves the vector to an explicit ``embed-entities`` call.
- **Nothing is written on a bad citation.** Every cited id is confirmed to
  exist before the evidence entity is created, so a typo can never leave a
  dangling usage record behind.

The reading half is the ``reflect`` composite, which aggregates these records
with time decay; the observation vocabulary they share lives here.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from theloom.errors import NotFoundError
from theloom.model import RelationType, UsageOutcome
from theloom.operations.common import CommandInput, UuidStr
from theloom.operations.entity import CreateEntityInput, create_entity
from theloom.operations.relations import CreateRelationInput, create_relation
from theloom.store.multigraph import MultiGraph
from theloom.timeutil import iso_now

# -- the shared observation vocabulary ------------------------------------------

#: Marks an evidence entity as a usage record rather than ordinary evidence.
USAGE_LAYER_TAG = "map_layer: usage"
QUESTION_PREFIX = "question: "
ANSWER_PREFIX = "answer: "
OUTCOME_PREFIX = "outcome: "
CORRECTION_PREFIX = "correction: "
RECORDED_PREFIX = "recorded: "

#: Written by ``reflect``, never by hand.
USAGE_STATUS_PREFIX = "usage_status: "
USAGE_STALE_PREFIX = "usage_stale: "
FINGERPRINT_PREFIX = "file_fingerprint: "

EXTRACTOR = "record-outcome"
MAX_NAME_CHARS = 80

#: A useful outcome corroborates what it cited; a dead end or a correction
#: casts doubt on it. Epistemic edge types already say exactly this.
OUTCOME_RELATION: dict[UsageOutcome, RelationType] = {
    UsageOutcome.USEFUL: RelationType.SUPPORTS,
    UsageOutcome.DEAD_END: RelationType.QUESTIONS,
    UsageOutcome.CORRECTED: RelationType.QUESTIONS,
}


class RecordOutcomeInput(CommandInput):
    """At least one citation is required — an outcome that cites nothing
    teaches nothing."""

    question: str
    answer: str | None = None
    entity_ids: list[UuidStr] = Field(alias="entityIds", min_length=1)
    outcome: UsageOutcome
    correction: str | None = None
    graph: str | None = None


def _truncate(text: str, limit: int) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1].rstrip() + "…"


def _observations(params: RecordOutcomeInput, recorded_at: str) -> list[str]:
    observations = [USAGE_LAYER_TAG, f"{QUESTION_PREFIX}{params.question}"]
    if params.answer is not None:
        observations.append(f"{ANSWER_PREFIX}{params.answer}")
    observations.append(f"{OUTCOME_PREFIX}{params.outcome.value}")
    if params.correction is not None:
        observations.append(f"{CORRECTION_PREFIX}{params.correction}")
    observations.append(f"{RECORDED_PREFIX}{recorded_at}")
    return observations


def record_outcome(params: RecordOutcomeInput, multi: MultiGraph) -> dict[str, Any]:
    """Record how one piece of work turned out, as evidence plus citations."""
    store = multi.get_store(params.graph)
    known = store.read_entity_docs(params.entity_ids)
    missing = [entity_id for entity_id in params.entity_ids if entity_id not in known]
    if missing:
        raise NotFoundError(
            "Cannot record an outcome citing entities that do not exist: "
            f"{', '.join(missing)}. Use list-entities to verify the ids first."
        )

    recorded_at = iso_now()
    evidence = create_entity(
        CreateEntityInput.model_validate(
            {
                "name": f"usage: {_truncate(params.question, MAX_NAME_CHARS)}",
                "entityType": "evidence",
                "observations": _observations(params, recorded_at),
                "provenance": {
                    "sourceType": "observation",
                    "sourceId": None,
                    "externalRef": None,
                    "extractionDate": recorded_at,
                    "extractor": EXTRACTOR,
                    "extractionMethod": "manual",
                },
                "memoryType": "experience",
                "graph": params.graph,
            }
        ),
        multi,
    )

    relation_type = OUTCOME_RELATION[params.outcome]
    citation = f"Recorded outcome '{params.outcome.value}' for question: {params.question}"
    relations = [
        create_relation(
            CreateRelationInput.model_validate(
                {
                    "from": evidence["id"],
                    "to": entity_id,
                    "relationType": relation_type.value,
                    "polarity": None,
                    "strength": "moderate",
                    "evidence": citation,
                    "graph": params.graph,
                }
            ),
            multi,
        )
        for entity_id in params.entity_ids
    ]
    return {"evidence": evidence, "relations": relations}
