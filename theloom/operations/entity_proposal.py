"""Typed options seam for :func:`theloom.semantic.entity_proposer.propose_entities`.

``entity_proposer.propose_entities`` takes a stringly-typed ``options`` dict
(``{"limit": ..., "capabilitySpec": ..., ...}``) — three call sites built that
dict by hand inline (``propose_entities.py``, ``self_improve.py``,
``hypothesis_engine.py``). :class:`EntityProposalOptions` is the typed
replacement: a Pydantic model with the same fields, snake_case in Python and
camelCase on the wire, whose :meth:`~EntityProposalOptions.to_options` method
serializes to exactly the dict ``propose_entities`` accepts.

This module lives in ``operations/`` rather than in
``theloom.semantic.entity_proposer`` itself, which is owned by another track
and is not modified here — this is purely an adapter in front of it.

Two fields (``capability_spec``, ``llm_client``) and one (``simulate_change``)
carry live Python objects, not JSON data — a :class:`CapabilitySpec` instance,
an LLM client, and a callable hook — so this model is not a wire-facing
``CommandInput``/``LoomModel``: it allows arbitrary types and is never
constructed from raw JSON.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from theloom.verification.capability_spec import CapabilitySpec

Strategy = Literal["pattern_completion", "llm_reasoning"]


class EntityProposalOptions(BaseModel):
    """Typed input to ``entity_proposer.propose_entities``.

    Every field defaults to ``None`` ("not provided") so
    :meth:`to_options` can omit it and let ``propose_entities``'s own
    ``_default()`` fallback apply — exactly the behavior of the hand-built
    dicts it replaces, which likewise omitted absent keys.
    """

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True, extra="forbid")

    limit: int | None = None
    simulate: bool | None = None
    strategies: list[Strategy] | None = None
    graph: str | None = None
    min_pattern_occurrences: int | None = Field(default=None, alias="minPatternOccurrences")
    max_patterns: int | None = Field(default=None, alias="maxPatterns")
    capability_spec: CapabilitySpec | None = Field(default=None, alias="capabilitySpec")
    llm_client: Any | None = Field(default=None, alias="llmClient")
    simulate_change: Callable[..., Any] | None = Field(default=None, alias="simulateChange")

    def to_options(self) -> dict[str, Any]:
        """The camelCase options dict ``propose_entities`` accepts.

        Absent (``None``) fields are omitted rather than passed as explicit
        nulls, matching what the hand-built dicts did; explicit ``False``/
        ``0`` values are meaningful and always kept.
        """
        return self.model_dump(by_alias=True, exclude_none=True)


__all__ = ["EntityProposalOptions"]
