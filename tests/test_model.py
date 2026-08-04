"""Domain-model tests — the contract for the domain model.

Covers:
  all enums and value sets
  Entity shape, ALL_ENTITY_TYPES, effective status
  Relation shape (from/to/polarity/strength)
  Confidence, Provenance, confidence-label boundaries
  5-state lifecycle transition table
Plus the invariants enforced here:
  volatile ⇒ expiresAt, confidence bounds.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from theloom.model import (
    ALL_ENTITY_STATUSES,
    ALL_ENTITY_TYPES,
    ALL_RELATION_TYPES,
    CAUSAL_POLARITY_DEFAULTS,
    CAUSAL_RELATION_TYPES,
    VALID_TRANSITIONS,
    Confidence,
    Entity,
    Provenance,
    Relation,
    RelationType,
    confidence_label,
    is_valid_transition,
)

NOW = "2026-07-10T00:00:00.000Z"


def make_entity(**overrides: object) -> Entity:
    base: dict[str, object] = {
        "id": "11111111-1111-4111-8111-111111111111",
        "name": "Systems Thinking",
        "entityType": "concept",
        "observations": ["A discipline for seeing wholes"],
        "created_at": NOW,
        "updated_at": NOW,
    }
    base.update(overrides)
    return Entity.model_validate(base)


def make_relation(**overrides: object) -> Relation:
    base: dict[str, object] = {
        "id": "22222222-2222-4222-8222-222222222222",
        "from": "11111111-1111-4111-8111-111111111111",
        "to": "33333333-3333-4333-8333-333333333333",
        "relationType": "causes",
        "polarity": "+",
        "strength": "moderate",
        "evidence": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    base.update(overrides)
    return Relation.model_validate(base)


# =============================================================================
# Enum inventories
# =============================================================================


def test_all_19_entity_types_match_reference() -> None:
    assert [t.value for t in ALL_ENTITY_TYPES] == [
        "concept",
        "claim",
        "source",
        "question",
        "evidence",
        "pattern",
        "insight",
        "tension",
        "convergence",
        "system",
        "variable",
        "loop",
        "leverage_point",
        "event",
        "procedure",
        "hypothesis",
        "inference_rule",
        "inference_trace",
        "research_session",
    ]


def test_all_17_relation_types_match_reference() -> None:
    assert [t.value for t in ALL_RELATION_TYPES] == [
        "related_to",
        "instance_of",
        "part_of",
        "sources",
        "calls",
        "references",
        "supports",
        "contradicts",
        "questions",
        "supersedes",
        "causes",
        "enables",
        "requires",
        "inhibits",
        "amplifies",
        "dampens",
        "crystallized_from",
    ]


def test_code_relation_types_are_non_causal() -> None:
    """calls/references are structural: no polarity, ever."""
    for value in ("calls", "references"):
        relation_type = RelationType(value)
        assert relation_type not in CAUSAL_RELATION_TYPES
        assert relation_type not in CAUSAL_POLARITY_DEFAULTS


def test_all_5_entity_statuses_match_reference() -> None:
    assert [s.value for s in ALL_ENTITY_STATUSES] == [
        "active",
        "superseded",
        "deprecated",
        "retracted",
        "investigating",
    ]


# =============================================================================
# Confidence (bounds enforced; label boundaries)
# =============================================================================


def test_confidence_score_bounds_enforced() -> None:
    Confidence.model_validate({"score": 0.5, "basis": "inference", "lastEvaluated": NOW})  # ok
    with pytest.raises(ValidationError):
        Confidence.model_validate({"score": 1.2, "basis": "inference", "lastEvaluated": NOW})
    with pytest.raises(ValidationError):
        Confidence.model_validate({"score": -0.1, "basis": "inference", "lastEvaluated": NOW})


def test_confidence_rejects_unknown_basis() -> None:
    with pytest.raises(ValidationError):
        Confidence.model_validate({"score": 0.5, "basis": "gut_feeling", "lastEvaluated": NOW})


@pytest.mark.parametrize(
    ("score", "label"),
    [
        (1.0, "very_high"),
        (0.9, "very_high"),
        (0.89, "high"),
        (0.7, "high"),
        (0.5, "moderate"),
        (0.3, "low"),
        (0.29, "speculative"),
        (0.0, "speculative"),
    ],
)
def test_confidence_label_boundaries_match_reference(score: float, label: str) -> None:
    assert confidence_label(score) == label


def test_confidence_label_rejects_out_of_range() -> None:
    with pytest.raises(ValueError):
        confidence_label(1.5)
    with pytest.raises(ValueError):
        confidence_label(-0.1)


# =============================================================================
# Entity
# =============================================================================


def test_minimal_entity_constructs_and_defaults_to_active() -> None:
    entity = make_entity()
    assert entity.entity_type == "concept"
    assert entity.status is None  # stored as unset
    assert entity.effective_status == "active"  # effective-status semantics


def test_entity_rejects_unknown_type() -> None:
    with pytest.raises(ValidationError):
        make_entity(entityType="vibe")


def test_entity_accepts_empty_name_like_reference() -> None:
    # The name schema is a plain string — empty names are accepted
    # (this is the behavior we intend, even if a stricter invariant is tempting).
    assert make_entity(name="").name == ""


def test_entity_rejects_malformed_timestamp() -> None:
    with pytest.raises(ValidationError):
        make_entity(created_at="yesterday")


def test_volatile_requires_expires_at() -> None:
    # The invariant enforced here: volatile entities must set expiresAt.
    with pytest.raises(ValidationError):
        make_entity(durability="volatile")
    entity = make_entity(durability="volatile", expiresAt=NOW)
    assert entity.expires_at == NOW


def test_non_volatile_does_not_require_expires_at() -> None:
    assert make_entity(durability="stable").expires_at is None


def test_entity_dump_uses_reference_wire_keys() -> None:
    # exclude_unset: explicitly-set nulls survive, unset optionals disappear —
    # the distinction the wire format relies on.
    dumped = make_entity().model_dump(by_alias=True, exclude_unset=True)
    assert set(dumped) == {
        "id",
        "name",
        "entityType",
        "observations",
        "created_at",
        "updated_at",
    }


def test_entity_round_trips_epistemic_metadata() -> None:
    entity = make_entity(
        confidence={"score": 0.8, "basis": "multiple_sources", "lastEvaluated": NOW},
        provenance={
            "sourceType": "document",
            "sourceId": None,
            "externalRef": "https://example.com",
            "extractionDate": NOW,
            "extractor": "test",
            "extractionMethod": "manual",
        },
        memoryType="knowledge",
        domain="research",
        durability="stable",
    )
    dumped = entity.model_dump(by_alias=True, exclude_unset=True)
    assert dumped["confidence"]["lastEvaluated"] == NOW
    assert dumped["provenance"]["sourceType"] == "document"
    assert dumped["memoryType"] == "knowledge"
    assert Entity.model_validate(dumped) == entity


# =============================================================================
# Relation
# =============================================================================


def test_relation_wire_format_uses_from_and_to() -> None:
    relation = make_relation()
    assert relation.from_ == "11111111-1111-4111-8111-111111111111"
    dumped = relation.model_dump(by_alias=True, exclude_unset=True)
    assert dumped["from"] == "11111111-1111-4111-8111-111111111111"
    assert "from_" not in dumped


def test_relation_polarity_is_plus_minus_or_null() -> None:
    assert make_relation(polarity=None).polarity is None
    assert make_relation(polarity="-").polarity == "-"
    with pytest.raises(ValidationError):
        make_relation(polarity="±")


def test_relation_rejects_unknown_type_and_strength() -> None:
    with pytest.raises(ValidationError):
        make_relation(relationType="loves")
    with pytest.raises(ValidationError):
        make_relation(strength="overwhelming")


# =============================================================================
# Provenance
# =============================================================================


def test_provenance_nullable_fields_are_required_keys() -> None:
    # Provenance: sourceId/externalRef/extractionMethod are nullable but required.
    with pytest.raises(ValidationError):
        Provenance.model_validate(
            {"sourceType": "document", "extractionDate": NOW, "extractor": "x"}
        )


# =============================================================================
# Status lifecycle
# =============================================================================


def test_transition_table_matches_reference() -> None:
    table = {k.value: sorted(v.value for v in vs) for k, vs in VALID_TRANSITIONS.items()}
    assert table == {
        "active": ["deprecated", "investigating", "retracted", "superseded"],
        "superseded": ["deprecated", "investigating", "retracted"],
        "deprecated": ["investigating", "retracted", "superseded"],
        "investigating": ["active", "deprecated", "retracted", "superseded"],
        "retracted": [],
    }


@pytest.mark.parametrize(
    ("from_status", "to_status", "valid"),
    [
        ("active", "deprecated", True),
        ("retracted", "active", False),  # terminal
        (None, "superseded", True),  # undefined treated as active
        ("investigating", "active", True),  # only path back to active
        ("superseded", "active", False),
        ("retracted", "retracted", True),  # same status is always a no-op
        ("deprecated", "superseded", True),
    ],
)
def test_transition_validity_matches_reference(
    from_status: str | None, to_status: str, valid: bool
) -> None:
    assert is_valid_transition(from_status, to_status) is valid  # type: ignore[arg-type]
