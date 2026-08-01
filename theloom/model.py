"""The Loom domain model — the single source of truth for all domain types.

Covers, in one place:
  - every enum value set, in a stable order
  - Entity shape, effective-status semantics
  - Relation shape (from/to/polarity/strength)
  - Confidence, Provenance, confidence-label scale
  - the 5-state lifecycle transition table

Python attributes are snake_case; wire names (aliases) are the JSON field names
(camelCase except the snake ``created_at``/``updated_at``).
Dump with ``model_dump(by_alias=True, exclude_unset=True)`` for wire output.

This model also *enforces* invariants at the type level: confidence bounds and
``volatile ⇒ expiresAt``.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

# =============================================================================
# Scalar validation
# =============================================================================

# ISO 8601 UTC with Z suffix — the canonical datetime wire format.
_ISO_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")


def _validate_iso_utc(value: str) -> str:
    if not _ISO_UTC_RE.match(value):
        raise ValueError(
            f"must be an ISO 8601 UTC datetime (e.g. 2026-01-01T00:00:00.000Z), got {value!r}"
        )
    return value


IsoUtcDatetime = Annotated[str, AfterValidator(_validate_iso_utc)]

# =============================================================================
# Enums (values and order are the stable wire contract)
# =============================================================================


class EntityType(StrEnum):
    CONCEPT = "concept"
    CLAIM = "claim"
    SOURCE = "source"
    QUESTION = "question"
    EVIDENCE = "evidence"
    PATTERN = "pattern"
    INSIGHT = "insight"
    TENSION = "tension"
    CONVERGENCE = "convergence"
    SYSTEM = "system"
    VARIABLE = "variable"
    LOOP = "loop"
    LEVERAGE_POINT = "leverage_point"
    EVENT = "event"
    PROCEDURE = "procedure"
    HYPOTHESIS = "hypothesis"
    INFERENCE_RULE = "inference_rule"
    INFERENCE_TRACE = "inference_trace"
    RESEARCH_SESSION = "research_session"


class RelationType(StrEnum):
    """Structural (no polarity): related_to, instance_of, part_of, sources.
    Epistemic (no polarity): supports, contradicts, questions, supersedes.
    Causal (WITH polarity): causes, enables, requires, inhibits, amplifies, dampens.
    Plus crystallized_from (reification lineage)."""

    RELATED_TO = "related_to"
    INSTANCE_OF = "instance_of"
    PART_OF = "part_of"
    SOURCES = "sources"
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    QUESTIONS = "questions"
    SUPERSEDES = "supersedes"
    CAUSES = "causes"
    ENABLES = "enables"
    REQUIRES = "requires"
    INHIBITS = "inhibits"
    AMPLIFIES = "amplifies"
    DAMPENS = "dampens"
    CRYSTALLIZED_FROM = "crystallized_from"


class MemoryType(StrEnum):
    """3D Memory Machine axis 1: what cognitive function does this serve?"""

    EXPERIENCE = "experience"
    KNOWLEDGE = "knowledge"
    TECHNIQUE = "technique"
    DECISION = "decision"
    INSIGHT = "insight"
    PRINCIPLE = "principle"
    INTENTION = "intention"
    ENCOUNTER = "encounter"


class Domain(StrEnum):
    """3D Memory Machine axis 2: what area of life does this belong to?"""

    ENGINEERING = "engineering"
    PRACTICE = "practice"
    RESEARCH = "research"
    RELATIONSHIP = "relationship"
    OPERATIONS = "operations"
    CREATIVE = "creative"


class Durability(StrEnum):
    """3D Memory Machine axis 3: how long will this remain valid?"""

    PERMANENT = "permanent"
    STABLE = "stable"
    CURRENT = "current"
    VOLATILE = "volatile"


class ConfidenceBasis(StrEnum):
    DIRECT_OBSERVATION = "direct_observation"
    PEER_REVIEWED = "peer_reviewed"
    MULTIPLE_SOURCES = "multiple_sources"
    SINGLE_SOURCE = "single_source"
    INFERENCE = "inference"
    SPECULATION = "speculation"
    LLM_EXTRACTION = "llm_extraction"
    CALCULATED = "calculated"


class ConfidenceLabel(StrEnum):
    VERY_HIGH = "very_high"
    HIGH = "high"
    MODERATE = "moderate"
    LOW = "low"
    SPECULATIVE = "speculative"


class EntityStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    DEPRECATED = "deprecated"
    RETRACTED = "retracted"
    INVESTIGATING = "investigating"


class StatusReason(StrEnum):
    OUTDATED_KNOWLEDGE = "outdated_knowledge"
    ERROR_CORRECTION = "error_correction"
    SOURCE_RETRACTED = "source_retracted"
    DUPLICATE = "duplicate"
    SCOPE_CHANGE = "scope_change"
    VERIFICATION_FAILED = "verification_failed"
    UNDER_REVIEW = "under_review"


class SourceType(StrEnum):
    DOCUMENT = "document"
    CONVERSATION = "conversation"
    OBSERVATION = "observation"
    INFERENCE = "inference"
    SYNTHESIS = "synthesis"
    EXTERNAL = "external"


class ExtractionMethod(StrEnum):
    MANUAL = "manual"
    LLM_PROMPTED = "llm_prompted"
    AUTOMATED = "automated"
    POSTMORTEM_GAP_RESOLUTION = "postmortem_gap_resolution"
    POSTMORTEM_PATTERN_REIFICATION = "postmortem_pattern_reification"
    POSTMORTEM_CREDIT_PROPAGATION = "postmortem_credit_propagation"
    INFERENCE_RULE_DERIVATION = "inference_rule_derivation"
    # Codebase extraction methods: tree-sitter genuinely IS an extraction
    # method, so these strings are accepted and stored in an entity's
    # provenance record.
    TREE_SITTER = "tree-sitter"
    TREE_SITTER_SCIP = "tree-sitter+scip"


class ChangeType(StrEnum):
    CREATED = "created"
    CONTENT_UPDATED = "content_updated"
    CONFIDENCE_UPDATED = "confidence_updated"
    STATUS_CHANGED = "status_changed"
    MERGED = "merged"


class EmbeddingStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    ERROR = "error"


class Strength(StrEnum):
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"
    FOUNDATIONAL = "foundational"


class RelationDirection(StrEnum):
    OUTGOING = "outgoing"
    INCOMING = "incoming"
    BOTH = "both"


# Polarity: direction of effect for causal relations. '+' same direction,
# '-' opposite, None not applicable (structural/epistemic relations).
Polarity = Literal["+", "-"]

# Runtime inventories derived from the enums — never a second hand-kept list.
ALL_ENTITY_TYPES: tuple[EntityType, ...] = tuple(EntityType)
ALL_RELATION_TYPES: tuple[RelationType, ...] = tuple(RelationType)
ALL_ENTITY_STATUSES: tuple[EntityStatus, ...] = tuple(EntityStatus)

# The causal subset (WITH polarity).
CAUSAL_RELATION_TYPES: tuple[RelationType, ...] = (
    RelationType.CAUSES,
    RelationType.ENABLES,
    RelationType.REQUIRES,
    RelationType.INHIBITS,
    RelationType.AMPLIFIES,
    RelationType.DAMPENS,
)

# Default polarity per causal type when the caller passes null.
CAUSAL_POLARITY_DEFAULTS: dict[RelationType, str] = {
    RelationType.CAUSES: "+",
    RelationType.ENABLES: "+",
    RelationType.REQUIRES: "+",
    RelationType.AMPLIFIES: "+",
    RelationType.INHIBITS: "-",
    RelationType.DAMPENS: "-",
}

# =============================================================================
# Confidence scale
# =============================================================================


def confidence_label(score: float) -> ConfidenceLabel:
    """Map a confidence score to its human-readable label."""
    if score < 0 or score > 1:
        raise ValueError(f"Confidence score must be between 0.0 and 1.0, got: {score}")
    if score >= 0.9:
        return ConfidenceLabel.VERY_HIGH
    if score >= 0.7:
        return ConfidenceLabel.HIGH
    if score >= 0.5:
        return ConfidenceLabel.MODERATE
    if score >= 0.3:
        return ConfidenceLabel.LOW
    return ConfidenceLabel.SPECULATIVE


# =============================================================================
# Status lifecycle
# =============================================================================

# retracted is terminal; only investigating may return to active.
VALID_TRANSITIONS: dict[EntityStatus, tuple[EntityStatus, ...]] = {
    EntityStatus.ACTIVE: (
        EntityStatus.SUPERSEDED,
        EntityStatus.DEPRECATED,
        EntityStatus.RETRACTED,
        EntityStatus.INVESTIGATING,
    ),
    EntityStatus.SUPERSEDED: (
        EntityStatus.DEPRECATED,
        EntityStatus.RETRACTED,
        EntityStatus.INVESTIGATING,
    ),
    EntityStatus.DEPRECATED: (
        EntityStatus.SUPERSEDED,
        EntityStatus.RETRACTED,
        EntityStatus.INVESTIGATING,
    ),
    EntityStatus.INVESTIGATING: (
        EntityStatus.ACTIVE,
        EntityStatus.SUPERSEDED,
        EntityStatus.DEPRECATED,
        EntityStatus.RETRACTED,
    ),
    EntityStatus.RETRACTED: (),
}


def is_valid_transition(
    from_status: EntityStatus | str | None, to_status: EntityStatus | str
) -> bool:
    """True iff the lifecycle transition is allowed.

    ``None`` is treated as 'active' (an entity with no status). A same-status
    transition is always a valid no-op — including retracted → retracted.
    """
    effective_from = EntityStatus(from_status) if from_status is not None else EntityStatus.ACTIVE
    to = EntityStatus(to_status)
    if effective_from == to:
        return True
    return to in VALID_TRANSITIONS[effective_from]


# =============================================================================
# Models
# =============================================================================


class LoomModel(BaseModel):
    """Base for all wire-facing domain models: alias round-tripping, no extras."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class Confidence(LoomModel):
    """Epistemic metadata about the reliability of an entity or relation."""

    score: float = Field(ge=0, le=1)
    basis: ConfidenceBasis
    last_evaluated: IsoUtcDatetime = Field(alias="lastEvaluated")


class Provenance(LoomModel):
    """Complete lineage of how knowledge entered the system.

    The nullable fields are *required keys* in the schema — a provenance
    without them is invalid, even if their value is null.
    """

    source_type: SourceType = Field(alias="sourceType")
    source_id: str | None = Field(alias="sourceId")
    external_ref: str | None = Field(alias="externalRef")
    extraction_date: IsoUtcDatetime = Field(alias="extractionDate")
    extractor: str
    extraction_method: ExtractionMethod | None = Field(alias="extractionMethod")


class Entity(LoomModel):
    """A node in the knowledge graph (Layer 1 + Layers 4/5 metadata).

    Note: name may be empty — the schema is a plain string that accepts it,
    and compatibility wins over a stricter invariant here.
    """

    id: str
    name: str
    entity_type: EntityType = Field(alias="entityType")
    observations: list[str]
    created_at: IsoUtcDatetime
    updated_at: IsoUtcDatetime

    # Layer 5: epistemic metadata
    confidence: Confidence | None = None
    status: EntityStatus | None = None
    status_reason: StatusReason | None = Field(default=None, alias="statusReason")
    status_changed_at: IsoUtcDatetime | None = Field(default=None, alias="statusChangedAt")
    provenance: Provenance | None = None
    # First-class session provenance: the research session that created this
    # record (supersedes the legacy "subgraph: {sid}-{qid}" observation tag).
    session: str | None = None

    # Layer 5: revision metadata
    version: int | None = Field(default=None, ge=1)
    previous_version_id: str | None = Field(default=None, alias="previousVersionId")
    change_type: ChangeType | None = Field(default=None, alias="changeType")
    change_reason: str | None = Field(default=None, alias="changeReason")

    # 3D Memory Machine (cognitive metadata)
    memory_type: MemoryType | None = Field(default=None, alias="memoryType")
    domain: Domain | None = None
    durability: Durability | None = None
    expires_at: IsoUtcDatetime | None = Field(default=None, alias="expiresAt")

    # Layer 4: embedding metadata
    embedding_status: EmbeddingStatus | None = Field(default=None, alias="embeddingStatus")
    content_hash: str | None = Field(default=None, alias="contentHash")
    last_embedded_at: IsoUtcDatetime | None = Field(default=None, alias="lastEmbeddedAt")
    embedding_version: str | None = Field(default=None, alias="embeddingVersion")
    embedding_error: str | None = Field(default=None, alias="embeddingError")

    @model_validator(mode="after")
    def _volatile_requires_expiry(self) -> Entity:
        # An invariant enforced here at the type level.
        if self.durability == Durability.VOLATILE and self.expires_at is None:
            raise ValueError("durability 'volatile' requires expiresAt")
        return self

    @property
    def effective_status(self) -> EntityStatus:
        """Effective status: unset status means 'active'."""
        return self.status if self.status is not None else EntityStatus.ACTIVE


class Relation(LoomModel):
    """A directed, typed, optionally polarized edge between entities (Layer 2)."""

    id: str
    from_: str = Field(alias="from")
    to: str
    relation_type: RelationType = Field(alias="relationType")
    # polarity/strength/evidence are all optional: store-direct creation
    # (e.g. simulate-change mutations) may omit any of them, and such edges are
    # stored with those fields undefined. Defaults let those bare edges
    # round-trip with the fields absent.
    polarity: Polarity | None = None
    strength: Strength | None = None
    evidence: str | None = None
    created_at: IsoUtcDatetime
    updated_at: IsoUtcDatetime
    confidence: Confidence | None = None
    provenance: Provenance | None = None
    session: str | None = None


# =============================================================================
# Create inputs (store generates id/timestamps; *Input schema shapes)
# =============================================================================


class ConfidenceInput(LoomModel):
    """Confidence for create/update input: lastEvaluated auto-populated when absent."""

    score: float = Field(ge=0, le=1)
    basis: ConfidenceBasis
    last_evaluated: IsoUtcDatetime | None = Field(default=None, alias="lastEvaluated")


class ProvenanceInput(LoomModel):
    """Provenance for create/update input: extractionDate auto-populated when absent."""

    source_type: SourceType = Field(alias="sourceType")
    source_id: str | None = Field(alias="sourceId")
    external_ref: str | None = Field(alias="externalRef")
    extraction_date: IsoUtcDatetime | None = Field(default=None, alias="extractionDate")
    extractor: str
    extraction_method: ExtractionMethod | None = Field(alias="extractionMethod")


class EntityCreate(LoomModel):
    """Input for store.create_entity — the entity minus generated fields."""

    name: str
    entity_type: EntityType = Field(alias="entityType")
    observations: list[str]
    confidence: ConfidenceInput | None = None
    status: EntityStatus | None = None
    status_reason: StatusReason | None = Field(default=None, alias="statusReason")
    provenance: ProvenanceInput | None = None
    session: str | None = None
    version: int | None = Field(default=None, ge=1)
    previous_version_id: str | None = Field(default=None, alias="previousVersionId")
    change_type: ChangeType | None = Field(default=None, alias="changeType")
    change_reason: str | None = Field(default=None, alias="changeReason")
    memory_type: MemoryType | None = Field(default=None, alias="memoryType")
    domain: Domain | None = None
    durability: Durability | None = None
    expires_at: IsoUtcDatetime | None = Field(default=None, alias="expiresAt")

    @model_validator(mode="after")
    def _volatile_requires_expiry(self) -> EntityCreate:
        if self.durability == Durability.VOLATILE and self.expires_at is None:
            raise ValueError("durability 'volatile' requires expiresAt")
        return self


class RelationCreate(LoomModel):
    """Input for store.create_relation — the relation minus generated fields."""

    from_: str = Field(alias="from")
    to: str
    relation_type: RelationType = Field(alias="relationType")
    polarity: Polarity | None = None
    # Optional at the store schema level (`strength.optional()`); the
    # create-relation *command* still requires it (CreateRelationInput), so this
    # only relaxes direct store calls like simulate-change mutations.
    strength: Strength | None = None
    evidence: str | None = None
    confidence: ConfidenceInput | None = None
    provenance: ProvenanceInput | None = None
    session: str | None = None


# =============================================================================
# Filters (EntityFilter and relation filters)
# =============================================================================


class EntityFilter(LoomModel):
    """Filter options for listing entities. Semantics implemented in
    theloom/store/filters.py with the ordering:
    status → type → name → query → version → session; exclude wins over
    sourcedFrom. The session filter matches the first-class field or the
    legacy "subgraph: {sid}-{qid}" observation tag."""

    entity_type: EntityType | None = Field(default=None, alias="entityType")
    name: str | None = None
    query: str | None = None
    sourced_from: list[str] | None = Field(default=None, alias="sourcedFrom")
    exclude_sourced_from: list[str] | None = Field(default=None, alias="excludeSourcedFrom")
    # When not provided the store defaults to ['active'].
    status_filter: list[EntityStatus] | None = Field(default=None, alias="statusFilter")
    version: int | None = None
    min_version: int | None = Field(default=None, alias="minVersion")
    memory_type: MemoryType | list[MemoryType] | None = Field(default=None, alias="memoryType")
    domain: Domain | list[Domain] | None = None
    durability: Durability | list[Durability] | None = None
    exclude_expired: bool | None = Field(default=None, alias="excludeExpired")
    session: str | None = None


class RelationFilter(LoomModel):
    """Filter options for listing relations."""

    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    relation_type: RelationType | None = Field(default=None, alias="relationType")
    polarity: Polarity | None = None
    session: str | None = None
