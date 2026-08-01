/**
 * EventList — the Chronicle's right rail: the event stream reshaped from
 * `temporal.events`, newest activity readable at a glance. Each row names what
 * happened (a typed glyph + a humanized entity/relation name) and when (offset
 * from the first event). The row at or just before the scrubber's instant is the
 * "you are here" marker and stays scrolled into view; later rows recede. Clicking
 * a row jumps the scrubber to that event (and pauses any playback).
 *
 * The header also hosts the Chronicle's PNG/SVG export controls (Task 5) — this
 * rail is the one fixed-position surface with header room to spare; the
 * callbacks are handed down from Chronicle.tsx, which owns the sigma instance.
 * Only rendered while `hasEvents`, so the controls only exist alongside it — a
 * bundle with no temporal events has nothing this view adds over the Explorer's
 * own export, which stays available there.
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type UIEvent } from "react";
import { useBundle } from "../../lib/BundleContext";
import { useTapestry } from "../../state/store";
import type { Timeline } from "./replay";
import { visibleRange } from "./eventWindow";
import { formatOffset } from "./format";

/**
 * Above this many events the list virtualizes; at or below it every row mounts,
 * so the dev fixture (35 events) renders byte-for-byte as before and the e2e
 * row-count assertions hold. Chosen far above any hand-authored timeline yet far
 * below the 100k rows the guardrails admit — the exact value never gates a test.
 */
const VIRTUALIZE_THRESHOLD = 200;
/** Fixed per-row stride (px) the virtual window assumes; matches Chronicle.css. */
const ROW_HEIGHT = 46;
/** Rows rendered beyond each viewport edge, so a fast scroll never flashes blank. */
const OVERSCAN = 8;
/** Fallback viewport height (px) before the rail measures itself on mount. */
const FALLBACK_VIEWPORT = ROW_HEIGHT * 20;

function DownloadIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" aria-hidden="true">
      <path
        d="M8 2v7.5M8 9.5 5 6.5M8 9.5l3-3"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M2.5 11.5v1.5a1 1 0 0 0 1 1h9a1 1 0 0 0 1-1v-1.5"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

const TYPE_GLYPH: Record<string, string> = {
  entity_created: "+",
  entity_updated: "~",
  entity_status_changed: "!",
  entity_deleted: "×",
  relation_created: "→",
  relation_updated: "~",
  relation_deleted: "×",
  entities_merged: "⋈",
};

const TYPE_LABEL: Record<string, string> = {
  entity_created: "Created",
  entity_updated: "Updated",
  entity_status_changed: "Status changed",
  entity_deleted: "Deleted",
  relation_created: "Linked",
  relation_updated: "Link updated",
  relation_deleted: "Unlinked",
  entities_merged: "Merged",
};

function humanize(value: string): string {
  return value.replace(/_/g, " ");
}

interface RelationInfo {
  from: string;
  to: string;
  rel: string;
}

export function EventList({
  timeline,
  onExportPng,
  onExportSvg,
}: {
  timeline: Timeline;
  onExportPng: () => void;
  onExportSvg: () => void;
}) {
  const bundle = useBundle();
  const time = useTapestry((s) => s.time);
  const setTime = useTapestry((s) => s.setTime);
  const setPlaying = useTapestry((s) => s.setPlaying);
  const current = time ?? timeline.end;

  const entityName = useMemo(() => {
    const map = new Map<string, string>();
    for (const entity of bundle.entities) {
      const id = entity.id as string | undefined;
      if (id) map.set(id, (entity.name as string) ?? id);
    }
    return map;
  }, [bundle]);

  const relationInfo = useMemo(() => {
    const map = new Map<string, RelationInfo>();
    for (const relation of bundle.relations) {
      const id = relation.id as string | undefined;
      if (!id) continue;
      map.set(id, {
        from: (relation.from as string) ?? "?",
        to: (relation.to as string) ?? "?",
        rel: humanize((relation.relationType as string) ?? "related to"),
      });
    }
    return map;
  }, [bundle]);

  // The most recent event at or before the scrubber — the current row.
  const currentIdx = useMemo(() => {
    let idx = -1;
    for (let i = 0; i < timeline.events.length; i += 1) {
      if (timeline.events[i].t <= current) idx = i;
      else break;
    }
    return idx;
  }, [timeline, current]);

  const count = timeline.events.length;
  const virtual = count > VIRTUALIZE_THRESHOLD;

  // Virtualization state — only consulted on the virtual path. `scrollTop` and
  // the measured viewport height drive `visibleRange`; below the threshold the
  // full list mounts and neither is read.
  const listRef = useRef<HTMLUListElement | null>(null);
  const currentRowRef = useRef<HTMLLIElement | null>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportH, setViewportH] = useState(FALLBACK_VIEWPORT);

  // Track the rail's client height so the window covers exactly what's on screen.
  useLayoutEffect(() => {
    if (!virtual) return;
    const el = listRef.current;
    if (!el) return;
    const measure = () => setViewportH(el.clientHeight || FALLBACK_VIEWPORT);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [virtual]);

  const range = virtual
    ? visibleRange(scrollTop, ROW_HEIGHT, viewportH, count, OVERSCAN)
    : { start: 0, end: count };

  // Keep the current row on screen. The small list uses the browser's own
  // scroll-into-view on the mounted row; the virtual list computes the offset
  // directly, since the target row may be unmounted inside a spacer.
  useEffect(() => {
    if (currentIdx < 0) return;
    if (virtual) {
      const el = listRef.current;
      if (!el) return;
      const rowTop = currentIdx * ROW_HEIGHT;
      const rowBottom = rowTop + ROW_HEIGHT;
      if (rowTop < el.scrollTop || rowBottom > el.scrollTop + el.clientHeight) {
        el.scrollTop = Math.max(0, rowTop - el.clientHeight / 2 + ROW_HEIGHT / 2);
      }
    } else {
      currentRowRef.current?.scrollIntoView({ block: "nearest" });
    }
  }, [currentIdx, virtual]);

  const onScroll = (event: UIEvent<HTMLUListElement>): void => {
    setScrollTop(event.currentTarget.scrollTop);
  };

  const visibleEvents = virtual ? timeline.events.slice(range.start, range.end) : timeline.events;

  const describe = (kind: string, id: string): { name: string } => {
    if (kind === "edge") {
      const info = relationInfo.get(id);
      if (info) {
        const from = entityName.get(info.from) ?? "?";
        const to = entityName.get(info.to) ?? "?";
        return { name: `${from} → ${to}` };
      }
      return { name: id };
    }
    return { name: entityName.get(id) ?? id };
  };

  return (
    <aside className="events" aria-label="Event stream">
      <header className="events__head">
        <h2 className="events__title">Event stream</h2>
        {timeline.events.length > 0 && (
          <span className="events__count">{timeline.events.length}</span>
        )}
        <span className="events__exports">
          <button
            type="button"
            className="events__exportbtn"
            title="Export PNG. Status badges are an on-screen overlay and are not included."
            onClick={onExportPng}
          >
            <DownloadIcon />
            PNG
          </button>
          <button
            type="button"
            className="events__exportbtn"
            title="Export SVG with a live/invalidated legend. Status badges are an on-screen overlay and are not included."
            onClick={onExportSvg}
          >
            <DownloadIcon />
            SVG
          </button>
        </span>
      </header>

      {timeline.events.length === 0 ? (
        <p className="events__meta">No temporal events in this bundle.</p>
      ) : (
        <ul
          className={`events__list${virtual ? " events__list--virtual" : ""}`}
          ref={listRef}
          onScroll={virtual ? onScroll : undefined}
        >
          {virtual && range.start > 0 && (
            <li
              className="events__spacer"
              style={{ height: range.start * ROW_HEIGHT }}
              aria-hidden="true"
            />
          )}
          {visibleEvents.map((event, offset) => {
            const i = range.start + offset;
            const glyph = TYPE_GLYPH[event.type] ?? "•";
            const typeLabel = TYPE_LABEL[event.type] ?? humanize(event.type);
            const { name } = describe(event.kind, event.id);
            const isCurrent = i === currentIdx;
            const isFuture = i > currentIdx;
            let className = "events__row";
            if (isCurrent) className += " events__row--current";
            else if (isFuture) className += " events__row--future";
            const relDetail =
              event.kind === "edge" && relationInfo.has(event.id)
                ? ` · ${relationInfo.get(event.id)?.rel}`
                : "";
            return (
              <li
                key={`${event.id}-${event.t}-${i}`}
                ref={!virtual && isCurrent ? currentRowRef : undefined}
                style={virtual ? { height: ROW_HEIGHT } : undefined}
              >
                <button
                  type="button"
                  className={className}
                  aria-current={isCurrent ? "true" : undefined}
                  onClick={() => {
                    setPlaying(false);
                    setTime(event.t);
                  }}
                >
                  <span className="events__glyph" aria-hidden="true">
                    {glyph}
                  </span>
                  <span className="events__text">
                    <span className="events__name">{name}</span>
                    <span className="events__meta">
                      {typeLabel}
                      {relDetail}
                    </span>
                  </span>
                  <span className="events__time">{formatOffset(event.t, timeline.start)}</span>
                </button>
              </li>
            );
          })}
          {virtual && range.end < count && (
            <li
              className="events__spacer"
              style={{ height: (count - range.end) * ROW_HEIGHT }}
              aria-hidden="true"
            />
          )}
        </ul>
      )}
    </aside>
  );
}
