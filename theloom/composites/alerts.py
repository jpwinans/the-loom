"""Session alerts seam (desire 13 x 14): ``collect_alerts`` is the one place
``since-last-session`` (``theloom.composites.since_last_session``, this
module's sole caller) gathers every session-level alert a waking mind should
see, in one pass.

This module owns the seam's fixed contract and this build's own provider(s).
It plugs in the calibration loop's own provider --
``theloom.operations.calibration_alerts.provide_alerts`` -- via a direct,
unconditional import inside ``collect_alerts`` (not module-level, purely to
keep this module's own import list free of a dependency it only needs for
one call). Both sides of the seam are merged, so there is nothing left to
guard: no ``try/except ImportError``, and no behavior here that differs
depending on whether the other module happens to be present.

Alert doc shape (every provider, present and future): ``{"code": str,
"severity": "info"|"warning", "message": str, "entityIds": list[str],
"entityNames": list[str], "data": dict}``. This is deliberately NOT a
``notice()`` doc: an alert is not scoped to one command's own mutation
outcome the way a notice is (see ``theloom.operations.notices``'s module
docstring), so alert codes are free-form strings a UI groups/filters on,
not entries enforced through ``NOTICE_CATALOG``.

**Discoverability.** Being outside ``NOTICE_CATALOG`` means alert codes get
none of its enforcement (no emit-time ``ValueError`` for an uncataloged
code) or its reachability walk -- so ``alert_catalog()`` below is a
hand-kept, much smaller parallel: every alert code this seam can surface
and its meaning, merged from this module's own provider
(``DREAM_EXPIRING_SOON``) and the calibration provider's
(``theloom.operations.calibration_alerts.ALERT_MEANINGS``), read by
``theloom.cli.notices_catalog.notices_catalog`` into that command's
``alerts`` section -- the same discoverability ``notices-catalog`` already
gives ``notice()`` codes, extended to this sibling vocabulary.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from theloom.store.multigraph import MultiGraph
from theloom.store.worlds import DOMAIN_ACTIVE, WORLD_KIND

Doc = dict[str, Any]

DREAM_EXPIRING_SOON = "DREAM_EXPIRING_SOON"

#: A dream world whose TTL falls inside this window of "now", still
#: unreviewed (domainStatus == active, ref not reaped), earns a warning.
#: "Unreviewed dream worlds expire by TTL -- an unexamined dream evaporates"
#: (claude-desires.md desire 13) is correct behavior for the *substrate*, but
#: silent evaporation of unreviewed findings is exactly the kind of thing a
#: waking session should be told about before it happens, not after.
DREAM_EXPIRY_WARNING_SECONDS = 48 * 3600

#: This provider's own alert code and its meaning -- merged with the
#: calibration provider's ``ALERT_MEANINGS`` by ``alert_catalog()`` below.
_LOCAL_ALERT_MEANINGS: dict[str, str] = {
    DREAM_EXPIRING_SOON: (
        "An unreviewed dream world's TTL falls within the warning window -- its "
        "findings evaporate with it (fork-world's TTL is informational, not "
        "swept early) unless merged or abandoned, or a fresh consolidate re-forks "
        "before then."
    ),
}


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
                    "code": DREAM_EXPIRING_SOON,
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

    from theloom.operations.calibration_alerts import provide_alerts

    alerts.extend(provide_alerts(graph, multi, since))

    return alerts


def alert_catalog() -> list[Doc]:
    """Every alert code this seam can surface (through ``collect_alerts``,
    reached only via ``since-last-session``), its meaning, and which command
    surfaces it -- the ``notices-catalog`` command's ``alerts`` section,
    making these codes discoverable the way ``NOTICE_CATALOG`` already makes
    ``notice()`` codes discoverable. See the module docstring: this is a
    hand-kept parallel, not a reachability walk -- there is no ``alert()``
    builder enforcing a code against a catalog the way ``notice()`` does."""
    from theloom.operations.calibration_alerts import ALERT_MEANINGS as _calibration_meanings

    merged = {**_LOCAL_ALERT_MEANINGS, **_calibration_meanings}
    return [
        {"code": code, "meaning": meaning, "commands": ["since-last-session"]}
        for code, meaning in sorted(merged.items())
    ]
