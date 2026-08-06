"""Typed error codes for the inference-trace lookup commands.

inference-trace-for-fact previously raised OperationError for a missing
trace with a comment reasoning about prose substring matching — exactly what
the typed-code system exists to forbid. Both the "no provenance" and
"provenance points at a trace that isn't there" branches are missing-resource
failures and must carry NOT_FOUND.
"""

from __future__ import annotations

import pytest

from theloom.errors import NotFoundError
from theloom.operations.entity import CreateEntityInput, create_entity
from theloom.operations.inference import (
    InferenceTraceForFactInput,
    InferenceTraceGetInput,
    inference_trace_for_fact,
    inference_trace_get,
)
from theloom.operations.relations import CreateRelationInput, create_relation
from theloom.store.multigraph import MultiGraph

MISSING = "00000000-0000-4000-8000-000000000000"


def ent(multi: MultiGraph, name: str) -> str:
    result = create_entity(
        CreateEntityInput.model_validate(
            {"name": name, "entityType": "concept", "observations": [name]}
        ),
        multi,
    )
    return str(result["id"])


def test_trace_get_missing_id_raises_not_found(multi: MultiGraph) -> None:
    with pytest.raises(NotFoundError) as excinfo:
        inference_trace_get(InferenceTraceGetInput.model_validate({"traceId": MISSING}), multi)
    assert excinfo.value.code == "NOT_FOUND"


def test_trace_for_fact_no_provenance_raises_not_found(multi: MultiGraph) -> None:
    a, b = ent(multi, "A"), ent(multi, "B")
    relation = create_relation(
        CreateRelationInput.model_validate(
            {
                "from": a,
                "to": b,
                "relationType": "related_to",
                "polarity": None,
                "strength": "moderate",
                "evidence": None,
            }
        ),
        multi,
    )

    with pytest.raises(NotFoundError) as excinfo:
        inference_trace_for_fact(
            InferenceTraceForFactInput.model_validate({"relationId": relation["id"]}), multi
        )
    assert excinfo.value.code == "NOT_FOUND"


def test_trace_for_fact_missing_relation_raises_not_found(multi: MultiGraph) -> None:
    with pytest.raises(NotFoundError) as excinfo:
        inference_trace_for_fact(
            InferenceTraceForFactInput.model_validate({"relationId": MISSING}), multi
        )
    assert excinfo.value.code == "NOT_FOUND"
