"""Reflect — the reading half of work memory.

``record-outcome`` leaves usage evidence in the graph; ``reflect`` turns that
pile of individual episodes into standing lessons, and writes them back onto
the entities they are about so the next session reads them for free.

It is **deterministic** — no LLM, no sampling. The whole judgement is three
rules over the citation edges:

1. **Decay.** A citation's weight halves every ``halfLifeDays``, so what was
   useful last year stops outvoting what was useful last week. Ages are
   measured against ``asOf`` (default: now), which makes the arithmetic
   reproducible.
2. **Corroboration.** One good experience is an anecdote. An entity is only
   called ``preferred`` once at least ``minCorroboration`` distinct useful
   citations agree *and* the decayed net is positive. Symmetrically, a single
   bad experience makes something ``contested``; only a corroborated run of
   dead ends earns ``dead_end``.
3. **Staleness.** A lesson about code is only as good as the code it was
   learned from. Entities carrying a ``File path:`` observation get a content
   fingerprint stored the first time they are reflected on; when the file's
   content later differs, the entity is flagged ``usage_stale``. The stored
   fingerprint is deliberately *not* refreshed — it records the state that was
   actually verified, and only re-verification (a re-extract) should move it.

Statuses are applied through the normal ``update-entity`` path, so every
lesson is versioned and event-sourced; a new ``usage_status`` observation
replaces the previous one rather than accumulating a contradictory pile, and a
reflection that reaches no verdict removes the previous one instead of leaving
a lesson the current evidence no longer supports.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import Field

from theloom.errors import ValidationError
from theloom.extraction.encoding import parse_file_path
from theloom.model import EntityFilter, EntityType, RelationFilter, RelationType, UsageOutcome
from theloom.operations.common import CommandInput
from theloom.operations.entity import UpdateEntityInput, update_entity
from theloom.operations.work_memory import (
    FINGERPRINT_PREFIX,
    OUTCOME_PREFIX,
    RECORDED_PREFIX,
    USAGE_LAYER_TAG,
    USAGE_STALE_PREFIX,
    USAGE_STATUS_PREFIX,
)
from theloom.store.falkor import FalkorGraphStore
from theloom.store.multigraph import MultiGraph
from theloom.timeutil import iso_now

DEFAULT_HALF_LIFE_DAYS = 30.0
DEFAULT_MIN_CORROBORATION = 2
SECONDS_PER_DAY = 86400.0
ANCHOR_MAX_CHARS = 200
FINGERPRINT_CHARS = 16
STALE_MARKER = f"{USAGE_STALE_PREFIX}file changed since last verification"

PREFERRED = "preferred"
CONTESTED = "contested"
DEAD_END = "dead_end"

#: Bookkeeping tags never make a good anchor line — they say nothing about
#: what the entity *is*.
_BOOKKEEPING_PREFIXES = (
    "map_layer:",
    "module_group:",
    "file path:",
    "line range:",
    "symbol kind:",
    USAGE_STATUS_PREFIX.strip(),
    USAGE_STALE_PREFIX.strip(),
    FINGERPRINT_PREFIX.strip(),
)

#: The citation edge types record-outcome writes.
_CITATION_TYPES = (RelationType.SUPPORTS, RelationType.QUESTIONS)


class ReflectInput(CommandInput):
    graph: str | None = None
    half_life_days: float | None = Field(default=None, gt=0, alias="halfLifeDays")
    min_corroboration: int | None = Field(default=None, ge=1, alias="minCorroboration")
    project_path: str | None = Field(default=None, alias="projectPath")
    #: The instant ages are measured from; defaults to now. Supplying it makes
    #: a reflection reproducible.
    as_of: str | None = Field(default=None, alias="asOf")
    dry_run: bool | None = Field(default=None, alias="dryRun")


@dataclass
class _Tally:
    """Decayed citation evidence accumulated for one cited entity."""

    score: float = 0.0
    useful: int = 0
    dead_ends: int = 0
    corrections: int = 0
    questions: list[str] = field(default_factory=list)

    @property
    def uses(self) -> int:
        return self.useful + self.dead_ends + self.corrections

    @property
    def negatives(self) -> int:
        return self.dead_ends + self.corrections


def _parse_iso(value: str, *, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(
            f"'{field_name}' must be an ISO 8601 datetime (e.g. 2026-01-01T00:00:00.000Z), "
            f"got {value!r}."
        ) from exc
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _observation_value(observations: list[str], prefix: str) -> str | None:
    lowered = prefix.lower()
    for text in observations:
        if text.lower().startswith(lowered):
            return text[len(prefix) :].strip()
    return None


def _anchor(observations: list[str]) -> str:
    for text in observations:
        lowered = text.lower()
        if any(lowered.startswith(prefix.lower()) for prefix in _BOOKKEEPING_PREFIXES):
            continue
        collapsed = " ".join(text.split())
        if len(collapsed) > ANCHOR_MAX_CHARS:
            return collapsed[: ANCHOR_MAX_CHARS - 1] + "…"
        return collapsed
    return ""


def _observations_of(doc: dict[str, Any]) -> list[str]:
    return [str(text) for text in (doc.get("observations") or [])]


# =============================================================================
# Aggregation
# =============================================================================


def _usage_records(store: FalkorGraphStore) -> list[dict[str, Any]]:
    docs = store.list_entity_docs(
        EntityFilter.model_validate({"entityType": EntityType.EVIDENCE.value})
    )
    return [doc for doc in docs if USAGE_LAYER_TAG in _observations_of(doc)]


def _citations_by_source(store: FalkorGraphStore) -> dict[str, list[str]]:
    """Distinct cited entity ids per usage-record id, over the citation edge
    types.

    Deduplicated per record because corroboration counts *experiences*: two
    edges from one record to one entity are one record's opinion, however they
    got there, and must weigh exactly as much as one.
    """
    cited: dict[str, list[str]] = defaultdict(list)
    for relation_type in _CITATION_TYPES:
        for relation in store.list_relation_docs(
            RelationFilter.model_validate({"relationType": relation_type.value})
        ):
            cited[str(relation["from"])].append(str(relation["to"]))
    return {source: list(dict.fromkeys(targets)) for source, targets in cited.items()}


def _weight(recorded: datetime, as_of: datetime, half_life_days: float) -> float:
    age_days = (as_of - recorded).total_seconds() / SECONDS_PER_DAY
    if age_days <= 0:  # recorded at or after asOf — no decay yet.
        return 1.0
    return float(0.5 ** (age_days / half_life_days))


def _tally(
    records: list[dict[str, Any]],
    citations: dict[str, list[str]],
    as_of: datetime,
    half_life_days: float,
) -> dict[str, _Tally]:
    tallies: dict[str, _Tally] = defaultdict(_Tally)
    for record in records:
        observations = _observations_of(record)
        raw_outcome = _observation_value(observations, OUTCOME_PREFIX)
        if raw_outcome is None:
            continue  # a usage record with no readable outcome teaches nothing
        try:
            outcome = UsageOutcome(raw_outcome)
        except ValueError:
            continue
        recorded_raw = _observation_value(observations, RECORDED_PREFIX) or str(
            record.get("created_at") or record.get("createdAt") or ""
        )
        recorded = _parse_iso(recorded_raw, field_name="recorded") if recorded_raw else as_of
        weight = _weight(recorded, as_of, half_life_days)
        for entity_id in citations.get(str(record["id"]), []):
            tally = tallies[entity_id]
            if outcome is UsageOutcome.USEFUL:
                tally.score += weight
                tally.useful += 1
            else:
                tally.score -= weight
                if outcome is UsageOutcome.DEAD_END:
                    tally.dead_ends += 1
                else:
                    tally.corrections += 1
    return dict(tallies)


def _classify(tally: _Tally, min_corroboration: int) -> str | None:
    """The corroboration rules. ``None`` means "not enough to say anything"."""
    if tally.score > 0 and tally.useful >= min_corroboration:
        return PREFERRED
    if tally.score < 0:
        if tally.negatives >= min_corroboration and tally.dead_ends >= tally.corrections:
            return DEAD_END
        return CONTESTED
    return None


def _status_observation(status: str, tally: _Tally) -> str:
    return f"{USAGE_STATUS_PREFIX}{status} (score {tally.score:.2f}, {tally.uses} uses)"


# =============================================================================
# Staleness
# =============================================================================


def _file_path(observations: list[str]) -> str | None:
    return parse_file_path(observations)


def _fingerprint(path: Path) -> str | None:
    try:
        payload = path.read_bytes()
    except OSError:
        return None
    return hashlib.sha256(payload).hexdigest()[:FINGERPRINT_CHARS]


def _resolve(project_path: str, relative: str) -> Path:
    candidate = Path(relative)
    return candidate if candidate.is_absolute() else Path(project_path) / candidate


# =============================================================================
# Application
# =============================================================================


def _next_observations(
    current: list[str], status_line: str | None, fingerprint_line: str | None, stale: bool
) -> list[str] | None:
    """The entity's observations after this reflection, or None if unchanged.

    The previous status is dropped whatever this reflection concluded: when it
    reaches no verdict the old line is retracted rather than left standing, so
    the stored lesson never outlives the evidence for it.
    """
    updated = [text for text in current if not text.startswith(USAGE_STATUS_PREFIX)]
    if status_line is not None:
        updated.append(status_line)
    if fingerprint_line is not None and not any(
        text.startswith(FINGERPRINT_PREFIX) for text in updated
    ):
        updated.append(fingerprint_line)
    if stale and STALE_MARKER not in updated:
        updated.append(STALE_MARKER)
    return updated if updated != current else None


def _row(doc: dict[str, Any], tally: _Tally) -> dict[str, Any]:
    return {
        "id": doc["id"],
        "name": doc["name"],
        "entityType": doc["entityType"],
        "score": round(tally.score, 4),
        "uses": tally.uses,
        "useful": tally.useful,
        "deadEnds": tally.dead_ends,
        "corrections": tally.corrections,
        "anchor": _anchor(_observations_of(doc)),
    }


def reflect(params: ReflectInput, multi: MultiGraph) -> dict[str, Any]:
    """Aggregate usage evidence into standing lessons and write them back."""
    store = multi.get_store(params.graph)
    half_life = (
        params.half_life_days if params.half_life_days is not None else DEFAULT_HALF_LIFE_DAYS
    )
    min_corroboration = (
        params.min_corroboration
        if params.min_corroboration is not None
        else DEFAULT_MIN_CORROBORATION
    )
    as_of_raw = params.as_of if params.as_of is not None else iso_now()
    as_of = _parse_iso(as_of_raw, field_name="asOf")
    dry_run = bool(params.dry_run)

    records = _usage_records(store)
    tallies = _tally(records, _citations_by_source(store), as_of, half_life)
    docs = store.read_entity_docs(sorted(tallies))

    buckets: dict[str, list[dict[str, Any]]] = {PREFERRED: [], CONTESTED: [], DEAD_END: []}
    stale_rows: list[dict[str, Any]] = []
    updated = 0

    for entity_id in sorted(tallies, key=lambda key: (-tallies[key].score, key)):
        doc = docs.get(entity_id)
        if doc is None:  # cited entity has since been hard-deleted
            continue
        tally = tallies[entity_id]
        observations = _observations_of(doc)

        status = _classify(tally, min_corroboration)
        status_line = _status_observation(status, tally) if status is not None else None
        if status is not None:
            buckets[status].append(_row(doc, tally))

        fingerprint_line: str | None = None
        stale = False
        relative = _file_path(observations) if params.project_path else None
        if relative is not None and params.project_path is not None:
            current = _fingerprint(_resolve(params.project_path, relative))
            stored = _observation_value(observations, FINGERPRINT_PREFIX)
            if stored is None and current is not None:
                fingerprint_line = f"{FINGERPRINT_PREFIX}{current}"
            elif stored is not None and current != stored:
                stale = True
            if stale:
                row = _row(doc, tally)
                row["filePath"] = relative
                stale_rows.append(row)

        changed = _next_observations(observations, status_line, fingerprint_line, stale)
        if changed is not None and not dry_run:
            update_entity(
                UpdateEntityInput.model_validate(
                    {
                        "id": entity_id,
                        "observations": changed,
                        "changeReason": "reflect: usage aggregation over recorded outcomes",
                        "graph": params.graph,
                    }
                ),
                multi,
            )
            updated += 1

    return {
        "preferred": buckets[PREFERRED],
        "contested": buckets[CONTESTED],
        "deadEnds": buckets[DEAD_END],
        "stale": stale_rows,
        "summary": {
            "usageRecords": len(records),
            "citedEntities": len(tallies),
            "updated": updated,
            "halfLifeDays": half_life,
            "minCorroboration": min_corroboration,
            "asOf": as_of_raw,
            "dryRun": dry_run,
        },
    }
