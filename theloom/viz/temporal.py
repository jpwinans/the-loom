"""Temporal section — the graph's event stream shaped for client-side replay."""

from __future__ import annotations

from datetime import UTC, datetime

from theloom.store.multigraph import MultiGraph
from theloom.viz.schema import TemporalEvent, TemporalSection


def _stream_id_to_iso(stream_id: str) -> str:
    milliseconds = int(stream_id.split("-", maxsplit=1)[0])
    return datetime.fromtimestamp(milliseconds / 1000, tz=UTC).isoformat()


def assemble_temporal(
    graph: str | None, multi: MultiGraph, as_of: str | None = None
) -> TemporalSection:
    events = []
    for event in multi.event_log(graph).read_all():
        at = _stream_id_to_iso(event.id)
        if as_of is not None and at > as_of:
            continue
        events.append(TemporalEvent(id=event.id, at=at, type=event.type, payload=event.payload))
    return TemporalSection(events=events)
