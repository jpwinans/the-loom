"""The calibration-alerts seam for ``since-last-session`` (desire 14, the
last of its four deliverables: "calibration alerts in since-last-session").

This module is a fixed contract another part of the build (the
``since-last-session``/``theloom.composites.alerts`` machinery) try-imports:

    def provide_alerts(graph: str, multi: MultiGraph, since: str | None) -> list[dict[str, Any]]

Kept deliberately standalone -- it imports only
``theloom.operations.calibration`` (this desire's own module) and
``theloom.store.multigraph``, nothing from whatever composite calls it, so it
can be built, tested, and reasoned about with zero knowledge of that
caller's own shape. Two kinds of alert, both read-only (this never mutates):

- ``CLAIM_RESOLVED`` (``info``): one per claim/hypothesis whose ``resolves``
  edge was created on or after ``since`` (or every resolved claim, when
  ``since`` is ``None``) -- "here's what got judged since you last looked."
- ``CALIBRATION_GAP`` (``warning``): one per author bucket currently over
  the configured gap threshold (``theloom.config``'s
  ``calibrationGapThreshold``), but only when at least one of the claims
  contributing to that bucket resolved on or after ``since`` -- "this bucket
  crossed the threshold since you last looked," not "this bucket has always
  been bad and you've seen it before." A bucket over threshold purely from
  old history that hasn't moved recently stays silent; nothing here persists
  a bucket's *previous* gap to diff against, so "crossed since last review"
  is operationalized as "currently over, and freshly evidenced" rather than
  a stored before/after comparison.

Bucketing is always by author here (not basis/domain): calibration alerts
are about *whose* judgement needs a second look, matching the dimension
``propagate-credit``'s calibrated damping already keys reliability by.
"""

from __future__ import annotations

from typing import Any

from theloom.config import load_config
from theloom.operations import calibration
from theloom.store.multigraph import MultiGraph

Doc = dict[str, Any]

_CLAIM_RESOLVED = "CLAIM_RESOLVED"
_CALIBRATION_GAP = "CALIBRATION_GAP"


#: ISO timestamps sort lexicographically; this is the same epoch fallback
#: ``theloom.operations.epistemic.session_changelog`` uses for an open-ended
#: "since the beginning" window.
_EPOCH = "1970-01-01T00:00:00.000Z"


def _since_or_epoch(since: str | None) -> str:
    return since if since is not None else _EPOCH


def _resolved_claim_alerts(claims: list[calibration.ResolvedClaim], since: str | None) -> list[Doc]:
    cutoff = _since_or_epoch(since)
    fresh = [claim for claim in claims if claim.resolved_at >= cutoff]
    fresh.sort(key=lambda claim: claim.resolved_at)
    return [
        {
            "code": _CLAIM_RESOLVED,
            "severity": "info",
            "message": (f"'{claim.claim_name}' resolved {claim.resolution} ({claim.resolved_at})."),
            "entityIds": [claim.claim_id],
            "entityNames": [claim.claim_name],
            "data": {
                "resolution": claim.resolution,
                "resolvedAt": claim.resolved_at,
                "assertedScore": claim.asserted_score,
                "session": claim.session,
            },
        }
        for claim in fresh
    ]


def _calibration_gap_alerts(
    claims: list[calibration.ResolvedClaim], since: str | None, floor: int, threshold: float
) -> list[Doc]:
    cutoff = _since_or_epoch(since)
    buckets = calibration.bucket_stats(claims, lambda claim: claim.session, floor)
    by_author: dict[str, list[calibration.ResolvedClaim]] = {}
    for claim in claims:
        by_author.setdefault(claim.session, []).append(claim)

    alerts: list[Doc] = []
    for row in buckets:
        if row["insufficientData"]:
            continue
        gap = row["gap"]
        if abs(gap) < threshold:
            continue
        author = row["key"]
        bucket_claims = by_author.get(author, [])
        if not any(claim.resolved_at >= cutoff for claim in bucket_claims):
            continue  # over threshold, but nothing fresh evidenced it
        alerts.append(
            {
                "code": _CALIBRATION_GAP,
                "severity": "warning",
                "message": (
                    f"'{author}' asserts {row['meanAssertedConfidence']:.2f} on average "
                    f"but resolves at {row['empiricalHitRate']:.2f} (n={row['n']}, "
                    f"gap={gap:+.2f})."
                ),
                "entityIds": [claim.claim_id for claim in bucket_claims],
                "entityNames": [claim.claim_name for claim in bucket_claims],
                "data": {
                    "author": author,
                    "n": row["n"],
                    "meanAssertedConfidence": row["meanAssertedConfidence"],
                    "empiricalHitRate": row["empiricalHitRate"],
                    "brierScore": row["brierScore"],
                    "gap": gap,
                },
            }
        )
    alerts.sort(key=lambda alert: str(alert["data"]["author"]))
    return alerts


def provide_alerts(graph: str, multi: MultiGraph, since: str | None) -> list[dict[str, Any]]:
    """Calibration alerts since ``since`` (ISO timestamp or ``None`` for
    all): newly resolved claims, and any bucket whose gap crossed the
    threshold. Alert doc shape: ``{"code": str, "severity": "info"|"warning",
    "message": str, "entityIds": list[str], "entityNames": list[str],
    "data": dict}``. Never mutates."""
    store = multi.get_store(graph)
    claims = calibration.resolved_claims(store)
    config = load_config()
    return [
        *_resolved_claim_alerts(claims, since),
        *_calibration_gap_alerts(
            claims, since, config.calibration_min_bucket_n, config.calibration_gap_threshold
        ),
    ]


__all__ = ["provide_alerts"]
