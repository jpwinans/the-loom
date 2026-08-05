"""Enrichment-Crawl composite: no registered command may raise a bare
NotImplementedError or silently no-op.

Two paths:
- No LLM configured: returns the documented three-failed-section template
  envelope (an honest, explicit degraded response — not a no-op).
- An LLM *is* configured: the CISC-voting crawl has no implementation, so the
  command must fail loudly with a typed OPERATION_ERROR rather than a bare
  NotImplementedError (untyped) or a silent fake-success envelope.
"""

from __future__ import annotations

import pytest
from falkordb import FalkorDB
from redis import Redis

from theloom.composites.enrichment_crawl import EnrichmentCrawlInput, enrichment_crawl
from theloom.errors import OperationError
from theloom.store.multigraph import MultiGraph


@pytest.fixture()
def multi(db: FalkorDB, redis_client: Redis, namespace: str) -> MultiGraph:
    return MultiGraph(db, redis_client, default_graph="default", key_prefix=namespace)


def test_no_llm_returns_the_documented_failed_section_envelope(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("theloom.composites.enrichment_crawl.create_synthesis_client", lambda: None)
    result = enrichment_crawl(EnrichmentCrawlInput(), multi)
    assert result["metadata"]["sectionsFailed"] == 3
    assert result["metadata"]["sectionsSucceeded"] == 0
    for name in ("prioritize", "crawl", "summary"):
        assert result["result"][name]["data"] is None
        assert "ANTHROPIC_API_KEY" in result["result"][name]["error"]


def test_llm_configured_raises_typed_operation_error_not_bare_not_implemented(
    multi: MultiGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "theloom.composites.enrichment_crawl.create_synthesis_client", lambda: object()
    )
    with pytest.raises(OperationError) as excinfo:
        enrichment_crawl(EnrichmentCrawlInput(), multi)
    assert excinfo.value.code == "OPERATION_ERROR"
    assert not isinstance(excinfo.value, NotImplementedError)
    assert "not implemented" in str(excinfo.value).lower()


def test_registered_summary_marks_the_llm_path_unavailable() -> None:
    """The registry summary (and the COMMANDS.md catalog generated from it)
    must flag that the LLM-configured path is unavailable."""
    from theloom.cli.registry import COMMANDS

    descriptor = next(c for c in COMMANDS if c.name == "enrichment-crawl")
    assert "unavailable" in descriptor.summary.lower()
