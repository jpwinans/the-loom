"""The closed calibration loop (desire 14): confidence that earns its numbers.

``record-outcome``/``reflect`` (Work Memory) grade whether a piece of *work*
was useful. This module grades whether a *belief* was right: a claim or
hypothesis resolves via ``resolve-claim`` (one atomic mutation: an outcome
entity, a ``resolves`` link to the claim, and the claim's status transition),
and ``calibration-profile`` folds every resolution into per-bucket Brier
scores and asserted-vs-empirical gaps -- the fold two other features build on
(``create-entity``'s ``CONFIDENCE_OUT_OF_LINE`` feedback and
``propagate-credit``'s ``dampingFactor: "calibrated"``).

**Assertion time, not read time.** Every bucket is built from each claim's
confidence *as it stood when the claim was created*
(``theloom.store.falkor.FalkorGraphStore.read_entity_as_of`` at the claim's
own ``created_at``) -- never its current confidence, which propagate-credit
or an ordinary update-entity may have moved since. This is bi-temporal
history doing exactly what it is for: recalibration must never rewrite what
was actually asserted.

**Expired claims are not scored.** Confirmed=1, refuted=0 is the binary
Brier target; ``expired`` means the claim went moot before anyone could
tell, so it carries no truth value to score against. Expired claims still
count toward a bucket's resolved total (``expiredCount``) but never enter
``n``, the mean asserted confidence, the empirical hit rate, or the Brier
score -- fabricating a truth value for them would be worse than excluding
them. A claim resolved without ever having a confidence score is excluded
the same way (see ``skippedNoConfidence``).

**The floor.** A bucket with fewer than ``calibrationMinBucketN`` (see
``theloom.config``) judged claims reports ``INSUFFICIENT_DATA`` rather than
a number computed from too little evidence -- desire 14's explicit
invariant.

Two more entry points are consumed *by other modules*, deliberately kept
here rather than duplicated:

- ``assertion_time_gap`` -- the single-bucket lookup ``create-entity``
  (``theloom.operations.entity``) uses for its ``CONFIDENCE_OUT_OF_LINE``
  feedback. The notice itself is built in ``entity.py``, not here: the
  notices-catalog reachability walk (``theloom.cli.notices_catalog``) only
  follows same-module calls from a command's own handler, so a code emitted
  from a helper this deep would never be attributed to ``create-entity``.
- ``author_reliability`` -- the per-author scalar (``1 - Brier``)
  ``propagate-credit`` (``theloom.operations.epistemic``) resolves per hop
  under ``dampingFactor: "calibrated"``.

Both are pure reads over the same ``resolved_claims`` fold; neither one
writes anything, matching the invariant that calibration reads never mutate.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import Field

from theloom.config import load_config
from theloom.errors import NotFoundError, OperationError, ValidationError
from theloom.model import (
    ClaimResolution,
    EntityStatus,
    EntityType,
    Relation,
    RelationCreate,
    RelationFilter,
    is_valid_transition,
)
from theloom.operations.common import CommandInput, UuidStr
from theloom.operations.notices import notice, with_notices
from theloom.store.falkor import FalkorGraphStore
from theloom.store.multigraph import MultiGraph
from theloom.timeutil import iso_now

# =============================================================================
# The shared observation vocabulary for resolve-claim's outcome entity
# =============================================================================

CALIBRATION_LAYER_TAG = "map_layer: calibration"
RESOLUTION_PREFIX = "resolution: "
RESOLUTION_EVIDENCE_PREFIX = "evidence: "
RESOLVED_AT_PREFIX = "resolved: "
RESOLVE_EXTRACTOR = "resolve-claim"
MAX_NAME_CHARS = 80

#: Entities calibration ever grades -- claims and hypotheses, the two types
#: a resolution can meaningfully attach to.
_RESOLVABLE_TYPES = frozenset({EntityType.CLAIM.value, EntityType.HYPOTHESIS.value})

#: A resolution moves the claim's lifecycle status: confirmed settles it as
#: active (correct, standing), refuted retracts it (wrong -- the same status
#: update-entity's own docstring uses for "withdrawn due to error"), expired
#: deprecates it (moot, not wrong). Routed through the ordinary 5-state
#: transition table (``theloom.model.is_valid_transition``), so e.g.
#: refuting an already-retracted claim is a no-op, not an error, and
#: confirming a deprecated one is refused exactly as update-entity would
#: refuse it directly.
_RESOLUTION_TARGET_STATUS: dict[ClaimResolution, EntityStatus] = {
    ClaimResolution.CONFIRMED: EntityStatus.ACTIVE,
    ClaimResolution.REFUTED: EntityStatus.RETRACTED,
    ClaimResolution.EXPIRED: EntityStatus.DEPRECATED,
}

_JUDGED_RESOLUTIONS = (ClaimResolution.CONFIRMED.value, ClaimResolution.REFUTED.value)


def _truncate(text: str, limit: int = MAX_NAME_CHARS) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1].rstrip() + "…"


def _observation_value(observations: Sequence[str], prefix: str) -> str | None:
    for text in observations:
        if text.startswith(prefix):
            return text[len(prefix) :].strip()
    return None


# =============================================================================
# resolve-claim
# =============================================================================


class ResolveClaimInput(CommandInput):
    """Resolves a claim/hypothesis: creates the outcome entity, links it to
    the claim with a ``resolves`` edge, and transitions the claim's status --
    one all-or-nothing write (see ``resolve_claim``'s docstring for the
    compensation strategy, the same one ``record-outcome`` uses)."""

    claim_id: UuidStr = Field(alias="claimId")
    resolution: ClaimResolution
    evidence: str
    session: str | None = None
    graph: str | None = None


def resolve_claim(params: ResolveClaimInput, multi: MultiGraph) -> dict[str, Any]:
    """Resolve a claim/hypothesis: outcome entity + ``resolves`` link + the
    claim's status transition, as one write a caller either sees whole or
    not at all.

    Not a single Cypher transaction (the store has no primitive that spans
    an entity create, a relation create, *and* a second entity's update as
    one atomic query) -- instead the same compensating-write pattern
    ``theloom.operations.work_memory.record_outcome`` already uses: the
    outcome entity is created first, and any failure in the two writes that
    follow deletes it again (``hard=True``, which cascades its edges), so a
    caller never observes a half-resolved claim -- an outcome entity with no
    link, or a link with no status change.
    """
    store = multi.get_store(params.graph)
    claim = store.read_entity(params.claim_id)
    if claim is None:
        raise NotFoundError(
            f"Claim not found: {params.claim_id}. Use list-entities to verify the id."
        )
    if claim.entity_type.value not in _RESOLVABLE_TYPES:
        raise OperationError(
            f"Entity {params.claim_id} is not a claim or hypothesis "
            f"(type: {claim.entity_type.value}); resolve-claim only grades claims/hypotheses."
        )
    target_status = _RESOLUTION_TARGET_STATUS[params.resolution]
    if not is_valid_transition(claim.status, target_status):
        current = claim.status.value if claim.status is not None else "active"
        raise ValidationError(
            f"Cannot resolve {params.claim_id} as '{params.resolution.value}': "
            f"invalid status transition from '{current}' to '{target_status.value}'."
        )

    # Deferred: entity.py's create_entity calls back into this module for the
    # CONFIDENCE_OUT_OF_LINE check, so a top-level import here would cycle.
    from theloom.operations.entity import (
        CreateEntityInput,
        UpdateEntityInput,
        create_entity,
        update_entity,
    )

    resolved_at = iso_now()
    outcome = create_entity(
        CreateEntityInput.model_validate(
            {
                "name": f"resolution: {_truncate(claim.name)}",
                "entityType": "evidence",
                "observations": [
                    CALIBRATION_LAYER_TAG,
                    f"{RESOLUTION_PREFIX}{params.resolution.value}",
                    f"{RESOLUTION_EVIDENCE_PREFIX}{params.evidence}",
                    f"{RESOLVED_AT_PREFIX}{resolved_at}",
                ],
                "provenance": {
                    "sourceType": "observation",
                    "sourceId": params.claim_id,
                    "externalRef": None,
                    "extractionDate": resolved_at,
                    "extractor": RESOLVE_EXTRACTOR,
                    "extractionMethod": "manual",
                },
                "memoryType": "decision",
                "session": params.session,
                "graph": params.graph,
            }
        ),
        multi,
    )
    outcome_id = str(outcome["id"])

    try:
        relations = store.create_relations(
            [
                RelationCreate.model_validate(
                    {
                        "from": outcome_id,
                        "to": params.claim_id,
                        "relationType": "resolves",
                        "polarity": None,
                        "strength": "strong",
                        "evidence": f"resolve-claim: {params.resolution.value} — {params.evidence}",
                    }
                )
            ]
        )
    except Exception:
        store.delete_entity(outcome_id, hard=True)
        raise

    try:
        updated = update_entity(
            UpdateEntityInput.model_validate(
                {
                    "id": params.claim_id,
                    "status": target_status.value,
                    "changeReason": f"resolve-claim: {params.resolution.value}",
                    "graph": params.graph,
                }
            ),
            multi,
        )
    except Exception:
        store.delete_entity(outcome_id, hard=True)
        raise

    return with_notices(
        {
            "outcome": outcome,
            "relation": relations[0].model_dump(by_alias=True, exclude_unset=True),
            "claim": updated["entity"],
        },
        applied=True,
    )


# =============================================================================
# The resolved-claims fold (shared by calibration-profile, assertion_time_gap,
# author_reliability, and theloom.operations.calibration_alerts)
# =============================================================================


@dataclass(frozen=True)
class ResolvedClaim:
    """One resolved claim, as it stood at ASSERTION time (not now)."""

    claim_id: str
    claim_name: str
    resolution: str
    resolved_at: str
    outcome_id: str
    asserted_score: float | None
    basis: str | None
    domain: str | None
    session: str


def resolved_claims(store: FalkorGraphStore) -> list[ResolvedClaim]:
    """Every claim/hypothesis with a live ``resolves`` edge, at its
    assertion-time confidence.

    A claim resolved more than once (a later resolve-claim call superseding
    an earlier one -- e.g. re-resolving after reopening via 'investigating')
    is counted once, by its most recently created ``resolves`` edge; the
    superseded resolution no longer represents the claim's current judged
    outcome.
    """
    relations = store.list_relations(RelationFilter.model_validate({"relationType": "resolves"}))
    latest_by_claim: dict[str, Relation] = {}
    for relation in relations:
        current = latest_by_claim.get(relation.to)
        if current is None or relation.created_at > current.created_at:
            latest_by_claim[relation.to] = relation

    claims: list[ResolvedClaim] = []
    for claim_id, relation in latest_by_claim.items():
        claim = store.read_entity(claim_id)
        if claim is None or claim.entity_type.value not in _RESOLVABLE_TYPES:
            continue  # hard-deleted, or a resolves edge created outside resolve-claim
        outcome = store.read_entity(relation.from_)
        if outcome is None:
            continue
        resolution = _observation_value(outcome.observations, RESOLUTION_PREFIX)
        if resolution not in {r.value for r in ClaimResolution}:
            continue
        resolved_at = (
            _observation_value(outcome.observations, RESOLVED_AT_PREFIX) or relation.created_at
        )

        # Assertion time, not now: the claim's confidence/domain/session as
        # they stood at its own creation, immune to any update-entity or
        # propagate-credit write since (bi-temporal read at created_at).
        snapshot = store.read_entity_as_of(claim_id, claim.created_at) or claim
        confidence = snapshot.confidence
        claims.append(
            ResolvedClaim(
                claim_id=claim_id,
                claim_name=claim.name,
                resolution=resolution,
                resolved_at=resolved_at,
                outcome_id=str(relation.from_),
                asserted_score=confidence.score if confidence is not None else None,
                basis=confidence.basis.value if confidence is not None else None,
                domain=snapshot.domain.value if snapshot.domain is not None else None,
                session=snapshot.session or load_config().default_session,
            )
        )
    return claims


def bucket_stats(
    claims: Sequence[ResolvedClaim], key: Callable[[ResolvedClaim], str], floor: int
) -> list[dict[str, Any]]:
    """One row per distinct ``key(claim)``, sorted by key -- ``n``/Brier/gap
    over the judged (confirmed/refuted, confidence-bearing) subset only;
    ``expiredCount`` reported alongside but never scored (see the module
    docstring)."""
    groups: dict[str, list[ResolvedClaim]] = defaultdict(list)
    for claim in claims:
        groups[key(claim)].append(claim)

    rows: list[dict[str, Any]] = []
    for bucket_key in sorted(groups):
        bucket = groups[bucket_key]
        judged: list[tuple[float, str]] = [
            (claim.asserted_score, claim.resolution)
            for claim in bucket
            if claim.resolution in _JUDGED_RESOLUTIONS and claim.asserted_score is not None
        ]
        expired_count = sum(1 for claim in bucket if claim.resolution == ClaimResolution.EXPIRED)
        n = len(judged)
        row: dict[str, Any] = {"key": bucket_key, "n": n, "expiredCount": expired_count}
        if n < floor:
            row.update(
                meanAssertedConfidence=None,
                empiricalHitRate=None,
                brierScore=None,
                gap=None,
                insufficientData=True,
            )
        else:
            mean_asserted = sum(score for score, _res in judged) / n
            hits = sum(1 for _score, res in judged if res == ClaimResolution.CONFIRMED.value)
            empirical = hits / n
            brier = (
                sum(
                    (score - (1.0 if res == ClaimResolution.CONFIRMED.value else 0.0)) ** 2
                    for score, res in judged
                )
                / n
            )
            row.update(
                meanAssertedConfidence=round(mean_asserted, 6),
                empiricalHitRate=round(empirical, 6),
                brierScore=round(brier, 6),
                gap=round(mean_asserted - empirical, 6),
                insufficientData=False,
            )
        rows.append(row)
    return rows


# =============================================================================
# calibration-profile
# =============================================================================

_BUCKET_DIMENSIONS: dict[str, Callable[[ResolvedClaim], str]] = {
    "author": lambda claim: claim.session,
    "basis": lambda claim: claim.basis or "unknown",
    "domain": lambda claim: claim.domain or "unknown",
}


class CalibrationWindowInput(CommandInput):
    """Restricts the fold to claims RESOLVED (not created) in
    ``[since, until)`` -- open-ended on either side when omitted. This is
    what ``theloom.operations.calibration_alerts`` uses to ask "what
    resolved since I last looked"."""

    since: str | None = None
    until: str | None = None


class CalibrationProfileInput(CommandInput):
    graph: str | None = None
    by: Literal["basis", "domain", "author"] | None = Field(
        default=None,
        description="Which dimension to bucket resolved claims by. Omitted falls back to "
        "'author' -- the dimension propagate-credit's calibrated damping and the "
        "assertion-time feedback check both key reliability by.",
    )
    window: CalibrationWindowInput | None = None
    #: Overrides the server's configured calibration floor (theloom.config's
    #: calibrationMinBucketN) for this call only; omit to use the configured
    #: value, so a caller pinning exact numbers in a test never depends on
    #: ambient config drift.
    min_bucket_n: int | None = Field(default=None, ge=1, alias="minBucketN")


def _in_window(resolved_at: str, window: CalibrationWindowInput | None) -> bool:
    if window is None:
        return True
    if window.since is not None and resolved_at < window.since:
        return False
    return not (window.until is not None and resolved_at >= window.until)


def calibration_profile(params: CalibrationProfileInput, multi: MultiGraph) -> dict[str, Any]:
    """Fold every resolved claim into per-bucket Brier score, empirical hit
    rate, and the asserted-vs-empirical gap. A pure read: never mutates."""
    store = multi.get_store(params.graph)
    by = params.by or "author"
    floor = (
        params.min_bucket_n
        if params.min_bucket_n is not None
        else load_config().calibration_min_bucket_n
    )

    windowed = [
        claim for claim in resolved_claims(store) if _in_window(claim.resolved_at, params.window)
    ]
    skipped_no_confidence = sum(
        1
        for claim in windowed
        if claim.resolution in _JUDGED_RESOLUTIONS and claim.asserted_score is None
    )
    buckets = bucket_stats(windowed, _BUCKET_DIMENSIONS[by], floor)

    notices = [
        notice(
            "INSUFFICIENT_DATA",
            f"Bucket '{row['key']}' ({by}) has only {row['n']} judged resolved claim(s) "
            f"(floor: {floor}); no Brier score, hit rate, or gap is reported for it.",
        )
        for row in buckets
        if row["insufficientData"]
    ]
    return with_notices(
        {
            "by": by,
            "buckets": buckets,
            "totalResolvedClaims": len(windowed),
            "skippedNoConfidence": skipped_no_confidence,
            "minBucketN": floor,
        },
        notices,
    )


# =============================================================================
# Shared with theloom.operations.entity (CONFIDENCE_OUT_OF_LINE) and
# theloom.operations.epistemic (propagate-credit's calibrated damping)
# =============================================================================


@dataclass(frozen=True)
class GapResult:
    """One author's calibration bucket for a given basis (and domain, when
    narrowed) -- the empirical rate a newly asserted confidence is judged
    against. Never None-able by construction; callers get ``None`` back from
    ``assertion_time_gap`` instead of a ``GapResult`` when there isn't enough
    history (the floor)."""

    basis: str
    domain: str | None
    n: int
    empirical_hit_rate: float
    mean_asserted_confidence: float


def assertion_time_gap(
    store: FalkorGraphStore, *, session: str, basis: str, domain: str | None, floor: int
) -> GapResult | None:
    """This author's calibration bucket for ``basis`` (narrowed to ``domain``
    when given): resolved claims this author asserted with a matching basis
    (and domain, if not None). ``None`` when fewer than ``floor`` judged
    claims sit in that bucket -- too little history to say anything."""
    claims = [
        claim
        for claim in resolved_claims(store)
        if claim.session == session
        and claim.basis == basis
        and (domain is None or claim.domain == domain)
    ]
    rows = bucket_stats(claims, lambda _claim: "bucket", floor)
    if not rows or rows[0]["insufficientData"]:
        return None
    row = rows[0]
    return GapResult(
        basis=basis,
        domain=domain,
        n=row["n"],
        empirical_hit_rate=row["empiricalHitRate"],
        mean_asserted_confidence=row["meanAssertedConfidence"],
    )


def author_reliability(store: FalkorGraphStore, *, session: str, floor: int) -> float | None:
    """``1 - Brier score`` over every resolved claim this author has ever
    asserted, regardless of basis/domain -- the scalar
    ``dampingFactor: "calibrated"`` resolves per hop from the hop's SOURCE
    author. ``None`` when this author has fewer than ``floor`` judged
    resolved claims -- the caller falls back to the ordinary constant."""
    claims = [claim for claim in resolved_claims(store) if claim.session == session]
    rows = bucket_stats(claims, lambda _claim: "bucket", floor)
    if not rows or rows[0]["insufficientData"]:
        return None
    brier = rows[0]["brierScore"]
    assert brier is not None  # insufficientData already ruled out above
    return round(1.0 - float(brier), 6)


__all__ = [
    "CALIBRATION_LAYER_TAG",
    "CalibrationProfileInput",
    "CalibrationWindowInput",
    "GapResult",
    "ResolveClaimInput",
    "ResolvedClaim",
    "assertion_time_gap",
    "author_reliability",
    "bucket_stats",
    "calibration_profile",
    "resolve_claim",
    "resolved_claims",
]
