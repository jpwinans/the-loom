"""Unit tests for the typed entity-proposer options seam.

`theloom.semantic.entity_proposer.propose_entities` takes a stringly-typed
options dict (`{"limit": ..., "capabilitySpec": ..., ...}`); three call sites
(propose_entities.py, self_improve.py, hypothesis_engine.py) hand-built that
dict inline. `EntityProposalOptions` is the typed replacement — it lives in
operations/ (entity_proposer.py itself is owned by another track) and its
`to_options()` round-trips to exactly what entity_proposer accepts.
"""

from __future__ import annotations

from theloom.operations.entity_proposal import EntityProposalOptions
from theloom.verification.capability_spec import CapabilitySpec


class TestEntityProposalOptionsRoundTrip:
    def test_defaults_produce_an_empty_options_dict(self) -> None:
        """An all-absent typed input serializes to {} — entity_proposer's own
        `_default()` fallbacks apply, exactly as when the hand-built dict
        callers used to omit a key."""
        options = EntityProposalOptions().to_options()
        assert options == {}

    def test_scalar_fields_round_trip_to_camelcase_keys(self) -> None:
        options = EntityProposalOptions(
            limit=5,
            simulate=True,
            strategies=["pattern_completion"],
            graph="research",
            min_pattern_occurrences=3,
            max_patterns=15,
        ).to_options()

        assert options == {
            "limit": 5,
            "simulate": True,
            "strategies": ["pattern_completion"],
            "graph": "research",
            "minPatternOccurrences": 3,
            "maxPatterns": 15,
        }

    def test_capability_spec_passes_through_by_identity(self) -> None:
        """A CapabilitySpec instance isn't JSON data — it must survive
        round-tripping as the exact same object, not a copy or a dump."""
        spec = CapabilitySpec()

        options = EntityProposalOptions(capability_spec=spec).to_options()

        assert options["capabilitySpec"] is spec

    def test_llm_client_and_simulate_change_pass_through_by_identity(self) -> None:
        sentinel_client = object()

        def sentinel_simulate_change(*args: object, **kwargs: object) -> None:
            return None

        options = EntityProposalOptions(
            llm_client=sentinel_client, simulate_change=sentinel_simulate_change
        ).to_options()

        assert options["llmClient"] is sentinel_client
        assert options["simulateChange"] is sentinel_simulate_change

    def test_false_and_zero_are_not_dropped_as_if_absent(self) -> None:
        """simulate=False and limit=0 are meaningful, explicit values — the
        round-trip must distinguish them from "not provided", unlike a naive
        `exclude_none` over falsy values."""
        options = EntityProposalOptions(simulate=False, limit=0).to_options()

        assert options["simulate"] is False
        assert options["limit"] == 0
