"""analyze-centrality tests.

Pins the ranked-array output shape: [{id, name, entityType, score}] sorted
descending by score, instead of a bare id->score map — the shape change is
the point (see the compact-output package).
"""

from __future__ import annotations

import pytest
from falkordb import FalkorDB
from redis import Redis

from theloom.errors import ValidationError
from theloom.operations.analysis import AnalyzeCentralityInput, analyze_centrality
from theloom.operations.entity import CreateEntityInput, create_entity
from theloom.operations.relations import CreateRelationInput, create_relation
from theloom.store.multigraph import MultiGraph


@pytest.fixture()
def multi(db: FalkorDB, redis_client: Redis, namespace: str) -> MultiGraph:
    return MultiGraph(db, redis_client, default_graph="default", key_prefix=namespace)


def ent(multi: MultiGraph, name: str) -> str:
    result = create_entity(
        CreateEntityInput.model_validate(
            {"name": name, "entityType": "concept", "observations": [name]}
        ),
        multi,
    )
    return str(result["id"])


def rel(multi: MultiGraph, from_id: str, to_id: str) -> None:
    create_relation(
        CreateRelationInput.model_validate(
            {
                "from": from_id,
                "to": to_id,
                "relationType": "related_to",
                "polarity": None,
                "strength": "moderate",
                "evidence": None,
            }
        ),
        multi,
    )


def test_analyze_centrality_returns_ranked_array_with_names(multi: MultiGraph) -> None:
    hub = ent(multi, "Hub")
    a, b = ent(multi, "A"), ent(multi, "B")
    rel(multi, a, hub)
    rel(multi, b, hub)

    result = analyze_centrality(AnalyzeCentralityInput(algorithm="degree"), multi)
    assert result["algorithm"] == "degree"
    assert isinstance(result["results"], list)
    top = result["results"][0]
    assert top["id"] == hub
    assert top["name"] == "Hub"
    assert top["entityType"] == "concept"
    assert isinstance(top["score"], float)
    scores = [entry["score"] for entry in result["results"]]
    assert scores == sorted(scores, reverse=True)


def test_analyze_centrality_limit_trims_results(multi: MultiGraph) -> None:
    hub = ent(multi, "Hub")
    a, b = ent(multi, "A"), ent(multi, "B")
    rel(multi, a, hub)
    rel(multi, b, hub)

    result = analyze_centrality(AnalyzeCentralityInput(algorithm="degree", limit=1), multi)
    assert len(result["results"]) == 1
    assert result["limit"] == 1


def test_analyze_centrality_invalid_algorithm_raises(multi: MultiGraph) -> None:
    ent(multi, "Solo")
    with pytest.raises(ValidationError):
        analyze_centrality(AnalyzeCentralityInput(algorithm="nonsense"), multi)
