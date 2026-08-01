"""Enrichment Crawl composite.

Crawls high-priority frontier nodes and proposes enrichment relations via CISC
N-sample LLM voting (``numSamples`` is a spend multiplier). The full crawl runs
only with an LLM configured; in template mode (no LLM) it returns a
deterministic three-failed-section envelope, so the command is testable without
exercising the enrichment modules.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import Field

from theloom.composites.framework import build_composite_result, failed_section
from theloom.operations.common import CommandInput
from theloom.store.multigraph import MultiGraph
from theloom.synthesis.llm import create_synthesis_client

# Fixed message; the envelope text is the contract (the config-routed client
# also supports local providers).
_NO_LLM_MESSAGE = "ANTHROPIC_API_KEY not set — cannot run enrichment crawl"


class EnrichmentCrawlInput(CommandInput):
    max_nodes: int | None = Field(default=None, gt=0, alias="maxNodes")
    max_candidates: int | None = Field(default=None, gt=0, alias="maxCandidates")
    num_samples: int | None = Field(default=None, gt=0, alias="numSamples")
    min_confidence: float | None = Field(default=None, ge=0, le=1, alias="minConfidence")
    dry_run: bool | None = Field(default=None, alias="dryRun")
    graph: str | None = None


def enrichment_crawl(params: EnrichmentCrawlInput, multi: MultiGraph) -> dict[str, Any]:
    start = time.perf_counter()
    # Resolve the store outside any section (a bad graph propagates before the
    # try, rather than being caught as a section failure).
    multi.get_store(params.graph)

    client = create_synthesis_client()
    if client is None:
        sections = {
            "prioritize": failed_section(_NO_LLM_MESSAGE),
            "crawl": failed_section(_NO_LLM_MESSAGE),
            "summary": failed_section(_NO_LLM_MESSAGE),
        }
        total_ms = round((time.perf_counter() - start) * 1000)
        return build_composite_result(sections, total_ms)

    # LLM-mode crawl (prioritize → CISC crawl → summarize) is out of scope here
    # (LLM env stripped); it would live in a theloom/enrichment/ package.
    # Raising here keeps the contract explicit rather than silently empty.
    raise NotImplementedError(
        "enrichment-crawl LLM path (CISC voting) is not built; only the template-mode "
        "no-LLM envelope is supported."
    )
