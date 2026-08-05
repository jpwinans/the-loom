"""Re-extraction retires the pre-``calls`` legacy call edges.

Before call edges were typed ``calls`` they were emitted as ``related_to``.
`extract-codebase` is the documented way to refresh such a graph, so a re-run
must not simply add a second, parallel edge for every call: the legacy
``related_to`` twin is closed out bi-temporally, leaving ``related_to`` to mean
a semantic link and nothing else. A ``related_to`` edge between the same pair
that is *not* a legacy call edge is untouched.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from falkordb import FalkorDB
from redis import Redis

from theloom.extraction import treesitter
from theloom.model import RelationCreate
from theloom.operations.bulk import BulkImportInput, bulk_import
from theloom.operations.extraction import ExtractCodebaseInput, extract_codebase
from theloom.store.falkor import FalkorGraphStore
from theloom.store.multigraph import MultiGraph

Doc = dict[str, Any]

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "repo"
GRAPH = "default"

SEMANTIC_EVIDENCE = "both participate in the onboarding flow"


@pytest.fixture()
def multi(db: FalkorDB, redis_client: Redis, namespace: str) -> MultiGraph:
    return MultiGraph(db, redis_client, default_graph=GRAPH, key_prefix=namespace)


@pytest.fixture()
def store(multi: MultiGraph) -> FalkorGraphStore:
    return multi.get_store(GRAPH)


def _first_call(extraction: Doc) -> Doc:
    return next(r for r in extraction["relations"] if r["relationType"] == "calls")


@pytest.fixture()
def legacy_graph(multi: MultiGraph, store: FalkorGraphStore) -> tuple[str, str]:
    """A graph as the old extractor left it: entities, plus the call edge of a
    known pair carried as ``related_to`` with the old evidence prose. Returns
    the (from, to) entity ids of that pair."""
    extraction = treesitter.extract_codebase(str(FIXTURE_REPO))
    result = bulk_import(
        BulkImportInput.model_validate(
            {"entities": extraction["entities"], "graph": GRAPH},
        ),
        multi,
    )
    call = _first_call(extraction)
    from_id = result["mapping"][call["from"]]
    to_id = result["mapping"][call["to"]]
    store.create_relation(
        RelationCreate.model_validate(
            {
                "from": from_id,
                "to": to_id,
                "relationType": "related_to",
                "strength": "moderate",
                # The pre-``calls`` extractor's evidence: no call-site anchor.
                "evidence": f"{call['from']} calls {call['to']}",
            }
        )
    )
    store.create_relation(
        RelationCreate.model_validate(
            {
                "from": from_id,
                "to": to_id,
                "relationType": "related_to",
                "strength": "moderate",
                "evidence": SEMANTIC_EVIDENCE,
            }
        )
    )
    return from_id, to_id


def _related_to_evidence(store: FalkorGraphStore, from_id: str, to_id: str) -> list[str | None]:
    return [r.evidence for r in store.read_relations(from_id, to_id, "related_to")]


def test_re_extract_retires_legacy_related_to_call_edges(
    multi: MultiGraph, store: FalkorGraphStore, legacy_graph: tuple[str, str]
) -> None:
    from_id, to_id = legacy_graph

    result = extract_codebase(
        ExtractCodebaseInput.model_validate(
            {"projectPath": str(FIXTURE_REPO), "graph": GRAPH},
        ),
        multi,
    )

    assert result["legacyCallEdgesRetired"] == 1
    # The call is now exactly one edge, typed `calls`.
    assert store.read_relation(from_id, to_id, "calls") is not None
    # ...and `related_to` between the pair is only the semantic link.
    assert _related_to_evidence(store, from_id, to_id) == [SEMANTIC_EVIDENCE]


def test_re_extract_is_idempotent_after_the_legacy_edges_are_gone(
    multi: MultiGraph, store: FalkorGraphStore, legacy_graph: tuple[str, str]
) -> None:
    from_id, to_id = legacy_graph
    params = ExtractCodebaseInput.model_validate(
        {"projectPath": str(FIXTURE_REPO), "graph": GRAPH},
    )
    extract_codebase(params, multi)

    second = extract_codebase(params, multi)

    assert second["legacyCallEdgesRetired"] == 0
    assert second["importResult"]["relationsCreated"] == 0
    assert len(store.read_relations(from_id, to_id, "calls")) == 1
    assert _related_to_evidence(store, from_id, to_id) == [SEMANTIC_EVIDENCE]


def test_dry_run_reports_the_legacy_edges_without_retiring_them(
    multi: MultiGraph, store: FalkorGraphStore, legacy_graph: tuple[str, str]
) -> None:
    from_id, to_id = legacy_graph

    result = extract_codebase(
        ExtractCodebaseInput.model_validate(
            {"projectPath": str(FIXTURE_REPO), "graph": GRAPH, "dryRun": True},
        ),
        multi,
    )

    assert result["legacyCallEdgesRetired"] == 1
    assert store.read_relation(from_id, to_id, "calls") is None
    assert len(_related_to_evidence(store, from_id, to_id)) == 2


def test_retirement_is_bi_temporal_not_erasure(
    db: FalkorDB, namespace: str, multi: MultiGraph, legacy_graph: tuple[str, str]
) -> None:
    """The legacy edge leaves the live projection but its history survives."""
    extract_codebase(
        ExtractCodebaseInput.model_validate(
            {"projectPath": str(FIXTURE_REPO), "graph": GRAPH},
        ),
        multi,
    )

    rows = (
        db.select_graph(f"{namespace}:graph:{GRAPH}")
        .query("MATCH (v:_RelationVersion) RETURN v._doc, v.tx_to")
        .result_set
    )
    retired = [json.loads(doc) for doc, tx_to in rows if tx_to]
    assert [
        r for r in retired if r["relationType"] == "related_to" and " calls " in str(r["evidence"])
    ]
