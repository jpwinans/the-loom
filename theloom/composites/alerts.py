"""Session alerts seam (desire 13 x 14): ``collect_alerts`` is the one place
``since-last-session`` (``theloom.composites.since_last_session``, this
module's sole caller) gathers every session-level alert a waking mind should
see, in one pass.

This module owns the seam's fixed contract and this build's own provider(s).
Part 7 (built in a parallel worktree, off the same commit this module first
landed in) plugs in the calibration-loop's own provider --
``theloom.operations.calibration_alerts.provide_alerts`` -- imported inside a
``try/except ImportError`` so this module, and every test of it, behaves
identically whether or not that module has been merged yet: the two builds
are developed in parallel and neither should have to wait on the other to be
tested in isolation.

Alert doc shape (every provider, present and future): ``{"code": str,
"severity": "info"|"warning", "message": str, "entityIds": list[str],
"entityNames": list[str], "data": dict}``. This is deliberately NOT a
``notice()`` doc: an alert is not scoped to one command's own mutation
outcome the way a notice is (see ``theloom.operations.notices``'s module
docstring), so alert codes are free-form strings a UI groups/filters on,
not entries enforced through ``NOTICE_CATALOG``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from theloom.store.multigraph import MultiGraph
from theloom.store.worlds import DOMAIN_ACTIVE, WORLD_KIND

Doc = dict[str, Any]

#: A dream world whose TTL falls inside this window of "now", still
#: unreviewed (domainStatus == active, ref not reaped), earns a warning.
#: "Unreviewed dream worlds expire by TTL -- an unexamined dream evaporates"
#: (claude-desires.md desire 13) is correct behavior for the *substrate*, but
#: silent evaporation of unreviewed findings is exactly the kind of thing a
#: waking session should be told about before it happens, not after.
DREAM_EXPIRY_WARNING_SECONDS = 48 * 3600


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _dream_expiry_alerts(graph: str, multi: MultiGraph) -> list[Doc]:
    """This build's own alert provider: unreviewed dream worlds (from
    ``consolidate``, ``theloom.composites.consolidate``) whose TTL is about
    to lapse. A world past its TTL is not swept automatically (``fork-world``
    TTL is informational -- ``theloom.store.refs``'s own docstring), so this
    is a genuine advance warning, not a report of something already gone."""
    now = datetime.now(UTC)
    alerts: list[Doc] = []
    for record in multi.refs.list(WORLD_KIND):
        if record.metadata.get("baseGraph") != graph:
            continue
        if record.status == "reaped" or record.metadata.get("domainStatus") != DOMAIN_ACTIVE:
            continue
        if record.expires_at is None:
            continue
        expires = _parse_iso(record.expires_at)
        if expires is None:
            continue
        remaining = (expires - now).total_seconds()
        if 0 < remaining <= DREAM_EXPIRY_WARNING_SECONDS:
            alerts.append(
                {
                    "code": "DREAM_EXPIRING_SOON",
                    "severity": "warning",
                    "message": (
                        f"Dream world '{record.name or record.id}' expires in "
                        f"{round(remaining / 3600, 1)}h and has not been reviewed -- its "
                        "findings evaporate with it unless merged, or a fresh consolidate "
                        "re-forks before then."
                    ),
                    "entityIds": [],
                    "entityNames": [],
                    "data": {"worldId": record.id, "expiresAt": record.expires_at},
                }
            )
    alerts.sort(key=lambda a: str(a["data"]["expiresAt"]))
    return alerts


def collect_alerts(graph: str, multi: MultiGraph, since: str | None) -> list[dict[str, Any]]:
    """Aggregate session alerts. Alert doc shape: {"code": str, "severity":
    "info"|"warning", "message": str, "entityIds": list[str], "entityNames":
    list[str], "data": dict}."""
    alerts: list[Doc] = [*_dream_expiry_alerts(graph, multi)]

    try:
        # Part 7's module, built in a parallel worktree off the same base --
        # not merged yet as of this module's own landing, so this import is
        # expected to fail today. mypy --strict resolves the installed
        # `theloom` distribution far enough to know this submodule doesn't
        # exist yet and reports it as an untyped/missing stub rather than a
        # hard error; the ignore is the one line of this contract that is
        # expected to become unnecessary (and should be deleted, not kept)
        # once that module lands.
        from theloom.operations.calibration_alerts import (  # type: ignore[import-untyped]
            provide_alerts,
        )
    except ImportError:
        provide_alerts = None
    if provide_alerts is not None:
        alerts.extend(provide_alerts(graph, multi, since))

    return alerts
