"""Creativity-Loop composite: no registered command may silently no-op,
stub-render, or raise a bare NotImplementedError.

Its six-step multi-cycle orchestration (explore/retrieve/transfer/score/
accept-reject/learn with cross-cycle exploration credit) has no
implementation, and used to fake it with an all-zero stub loop that always
reported deterministic "success" — a silent no-op no caller could tell apart
from a real (if uneventful) run. It now fails loudly with a typed
OPERATION_ERROR instead.
"""

from __future__ import annotations

import pytest

from theloom.composites.creativity_loop import (
    CreativityLoopInput,
    _map_to_config,
    creativity_loop,
)
from theloom.errors import OperationError


def test_creativity_loop_raises_typed_operation_error_not_bare_not_implemented() -> None:
    with pytest.raises(OperationError) as excinfo:
        creativity_loop(CreativityLoopInput(), None)
    assert excinfo.value.code == "OPERATION_ERROR"
    assert not isinstance(excinfo.value, NotImplementedError)
    assert "not implemented" in str(excinfo.value).lower()


def test_config_mapping_still_applies_documented_defaults() -> None:
    config = _map_to_config(CreativityLoopInput())
    assert config["maxCycles"] == 10
    assert config["interestingnessThreshold"] == 0.3
    assert config["consecutiveFailureLimit"] == 3
    assert config["explorationBudget"] == 5
    assert config["transferBudget"] == 10
    assert config["dryRunCredit"] is False
    assert config["useTriggerQueue"] is True
    # Absent optionals are omitted, not defaulted to null.
    assert "graph" not in config
    assert "exploreTopK" not in config
    assert "purpose" not in config
    assert "generalizationBias" not in config


def test_config_mapping_overrides_and_keeps_optionals() -> None:
    config = _map_to_config(
        CreativityLoopInput.model_validate(
            {"graph": "research", "maxCycles": 5, "exploreTopK": 3, "purpose": "find analogies"}
        )
    )
    assert config["graph"] == "research"
    assert config["maxCycles"] == 5
    assert config["exploreTopK"] == 3
    assert config["purpose"] == "find analogies"
