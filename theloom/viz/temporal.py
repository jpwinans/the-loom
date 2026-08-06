"""Temporal section — the graph's event stream shaped for client-side replay."""

from __future__ import annotations

from theloom.store.multigraph import MultiGraph
from theloom.viz.schema import TemporalEvent, TemporalSection


def assemble_temporal(
    graph: str | None, multi: MultiGraph, as_of: str | None = None
) -> TemporalSection:
    events = []
    for event in multi.event_log(graph).read_all():
        at = event.timestamp
        if as_of is not None and at > as_of:
            continue
        events.append(TemporalEvent(id=event.id, at=at, type=event.type, payload=event.payload))
    return TemporalSection(events=events)
