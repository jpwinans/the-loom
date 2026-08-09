"""``since-last-session`` (desire 13): the waking surface. One call, one
envelope: every unreviewed dream world's consolidation report (``consolidate``,
``theloom.composites.consolidate``) plus a fresh diff-worlds summary,
contradictions touching anything recently active, and calibration alerts --
the named seam (``theloom.composites.alerts.collect_alerts``; desire 14 /
Part 7 plugs its own provider in there).

Morning review is a merge decision, not this command's job: this only reads.
Endorsing a finding is ``merge-world {strategy: "select", entityIds: [...]}}``
against the ``diffWorldsHandle``/``worldId`` this response names; abandoning
the rest is ``abandon-world``. Unreviewed dream worlds otherwise expire by
TTL on their own (Part 5's ``fork-world`` TTL), which is why the
``DREAM_EXPIRING_SOON`` alert (``theloom.composites.alerts``) exists -- an
evaporating dream should be announced before it's gone, not silently after.

**The hard cap.** The response is meant to be small enough to inject into a
context window: ``_shrink_to_budget`` measures the assembled envelope's own
JSON size against ``MAX_ENVELOPE_CHARS`` and, only if it's over, trims in a
fixed, cheapest-first order (alerts, then contradictions, then each dream's
own finding detail, then older dreams themselves) until it fits or there is
nothing left to cut. A trimmed response always carries ``truncated: true``
plus a ``SESSION_SURFACE_TRUNCATED`` notice naming what was cut -- honest per
the contract, never a silent drop.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from theloom.composites import alerts as alerts_composite
from theloom.composites.consolidate import (
    DREAM_WORLD_NAME_PREFIX,
    read_report,
)
from theloom.operations import epistemic as epistemic_ops
from theloom.operations import worlds as worlds_ops
from theloom.operations.common import CommandInput
from theloom.operations.notices import Doc, notice, with_notices
from theloom.store.multigraph import MultiGraph
from theloom.timeutil import iso_now

MAX_ENVELOPE_CHARS = 8000
DEFAULT_LOOKBACK_SECONDS = 7 * 24 * 3600


class SinceLastSessionInput(CommandInput):
    graph: str | None = None


def _has_consolidation_history(multi: MultiGraph, graph: str) -> bool:
    """Whether ``consolidate`` has EVER been run against ``graph`` -- any
    dream-named world ref forked from it, in ANY status (active, merged, or
    abandoned/reaped), not just the still-unreviewed ones ``_unreviewed_dreams``
    lists. This is what distinguishes "genuinely never consolidated" from
    "consolidated, but every dream has since been reviewed (merged or
    abandoned)" -- the two states ``NO_CONSOLIDATION_HISTORY`` used to
    conflate by firing whenever ``_unreviewed_dreams`` was merely empty,
    regardless of which of the two was actually true."""
    for record in multi.list_worlds(include_reaped=True):
        if record["baseGraph"] != graph:
            continue
        if str(record.get("name") or "").startswith(DREAM_WORLD_NAME_PREFIX):
            return True
    return False


def _unreviewed_dreams(multi: MultiGraph, graph: str) -> list[Doc]:
    """Every active (not merged/abandoned), unreviewed dream world forked
    from ``graph``, newest first -- (d)'s independence guarantee means two
    consolidations without an intervening merge both show up here, not
    collapsed into one."""
    out: list[Doc] = []
    for record in multi.list_worlds(include_reaped=False):
        if record["baseGraph"] != graph:
            continue
        if not str(record.get("name") or "").startswith(DREAM_WORLD_NAME_PREFIX):
            continue
        report = read_report(multi, record["worldId"])
        if report is None:
            continue  # a dream whose consolidate call never reached persisting a report
        parent_label = str(report.get("parentWorld") or "main")
        diff = worlds_ops.diff_worlds(
            worlds_ops.DiffWorldsInput(a=parent_label, b=record["worldId"]), multi
        )
        out.append(
            {
                "worldId": record["worldId"],
                "name": record["name"],
                "forkedAt": record["forkedAt"],
                "expiresAt": record["expiresAt"],
                "report": report,
                "diffSummary": {"changeCount": diff["count"]},
                "diffWorldsHandle": {"a": parent_label, "b": record["worldId"]},
            }
        )
    out.sort(key=lambda d: str(d["forkedAt"]), reverse=True)
    return out


def _default_lookback() -> str:
    cutoff = datetime.now(UTC) - timedelta(seconds=DEFAULT_LOOKBACK_SECONDS)
    return cutoff.strftime("%Y-%m-%dT%H:%M:%S.") + f"{cutoff.microsecond // 1000:03d}Z"


def _since_boundary(dreams: list[Doc]) -> str:
    """Since the last time this graph was consolidated -- the oldest
    unreviewed dream's fork point (waking up may mean catching up on more
    than one dream), or a fixed lookback window when there is no dream to
    anchor on at all (a graph that has never been consolidated is not left
    with an unusably huge "recent" window)."""
    if dreams:
        return str(dreams[-1]["forkedAt"])
    return _default_lookback()


def _recent_contradictions(multi: MultiGraph, graph: str, since: str) -> list[Doc]:
    result = epistemic_ops.contested_claims(
        epistemic_ops.EpistemicQueryInput.model_validate(
            {"graph": graph, "includeAllStatuses": True}
        ),
        multi,
    )
    recent: list[Doc] = []
    for row in result["items"]:
        entity = row["entity"]
        touched_at = (
            entity.get("statusChangedAt") or entity.get("updated_at") or entity.get("created_at")
        )
        if touched_at is not None and str(touched_at) >= since:
            recent.append(
                {
                    "entityId": entity["id"],
                    "entityName": entity["name"],
                    "supportCount": row["supportCount"],
                    "contradictCount": row["contradictCount"],
                    "touchedAt": touched_at,
                }
            )
    return recent


def _envelope_size(envelope: Doc) -> int:
    return len(json.dumps(envelope, default=str))


def _shrink_to_budget(envelope: Doc) -> tuple[Doc, bool]:
    """Cheapest-first, deterministic trimming until the envelope's own JSON
    size fits under ``MAX_ENVELOPE_CHARS`` -- alerts and contradictions are
    ranked signal a waking session can re-fetch in full (calibration-alerts,
    contested-claims) far more cheaply than a dream's own findings, which
    only exist inside that one dream world, so they are cut last."""
    if _envelope_size(envelope) <= MAX_ENVELOPE_CHARS:
        return envelope, False

    envelope["calibrationAlerts"] = envelope["calibrationAlerts"][:10]
    if _envelope_size(envelope) <= MAX_ENVELOPE_CHARS:
        return envelope, True

    envelope["recentContradictions"] = envelope["recentContradictions"][:10]
    if _envelope_size(envelope) <= MAX_ENVELOPE_CHARS:
        return envelope, True

    for dream in envelope["unreviewedDreams"]:
        report = dream["report"]
        dream["report"] = {
            "worldId": report.get("worldId"),
            "generatedAt": report.get("generatedAt"),
            "counts": report.get("counts"),
            "totalFindings": report.get("totalFindings"),
            "topFindings": (report.get("topFindings") or [])[:3],
        }
    if _envelope_size(envelope) <= MAX_ENVELOPE_CHARS:
        return envelope, True

    envelope["unreviewedDreams"] = envelope["unreviewedDreams"][:3]
    if _envelope_size(envelope) <= MAX_ENVELOPE_CHARS:
        return envelope, True

    # Pathological case (still over cap after every prior cut): collapse to
    # the single most recent dream and a minimal tail, rather than looping
    # indefinitely -- always terminates, always honest via `truncated`.
    envelope["unreviewedDreams"] = envelope["unreviewedDreams"][:1]
    envelope["recentContradictions"] = envelope["recentContradictions"][:3]
    envelope["calibrationAlerts"] = envelope["calibrationAlerts"][:3]
    return envelope, True


def since_last_session(params: SinceLastSessionInput, multi: MultiGraph) -> Doc:
    graph = params.graph or multi.default_graph
    dreams = _unreviewed_dreams(multi, graph)
    since = _since_boundary(dreams)
    contradictions = _recent_contradictions(multi, graph, since)
    calibration_alerts = alerts_composite.collect_alerts(graph, multi, since)

    envelope: Doc = {
        "graph": graph,
        "generatedAt": iso_now(),
        "sinceLastConsolidation": dreams[-1]["forkedAt"] if dreams else None,
        "unreviewedDreams": dreams,
        "recentContradictions": contradictions,
        "calibrationAlerts": calibration_alerts,
    }
    envelope, truncated = _shrink_to_budget(envelope)

    notices: list[Doc] = []
    if not dreams:
        if _has_consolidation_history(multi, graph):
            notices.append(
                notice(
                    "ALL_DREAMS_REVIEWED",
                    f"Every consolidation report for '{graph}' has already been reviewed "
                    "(merged or abandoned); there is nothing new to surface.",
                    hint="Call consolidate to fork a fresh dream world.",
                )
            )
        else:
            notices.append(
                notice(
                    "NO_CONSOLIDATION_HISTORY",
                    f"No consolidation report has ever been generated for '{graph}'; run "
                    "'consolidate' first.",
                    hint="Call consolidate to fork a dream world and populate a report.",
                )
            )
    if truncated:
        envelope["truncated"] = True
        notices.append(
            notice(
                "SESSION_SURFACE_TRUNCATED",
                "The since-last-session response exceeded its context-window size budget and "
                "was trimmed; some findings/contradictions/alerts were cut.",
                hint="Call diff-worlds directly against a specific dream world's "
                "diffWorldsHandle for the untrimmed detail.",
            )
        )
    return with_notices(envelope, notices)
