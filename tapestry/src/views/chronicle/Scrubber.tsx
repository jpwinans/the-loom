/**
 * Scrubber — the Chronicle's signature control: a full-width timeline along the
 * bottom. Dragging the range replays construction; the ticks behind it mark
 * where mutations fired, so clusters of activity are legible at a glance. The
 * play button hands off to the parent's rAF loop (Chronicle.tsx); dragging the
 * range takes control back by pausing. The current instant is shown as both an
 * absolute time and an offset from the first event.
 */
import { useMemo } from "react";
import { useTapestry } from "../../state/store";
import type { Timeline } from "./replay";
import { formatInstant, formatOffset } from "./format";

function PlayIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
      <path d="M4.5 3.2v9.6L12.5 8z" fill="currentColor" />
    </svg>
  );
}

function PauseIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
      <rect x="4" y="3.2" width="3" height="9.6" rx="1" fill="currentColor" />
      <rect x="9" y="3.2" width="3" height="9.6" rx="1" fill="currentColor" />
    </svg>
  );
}

export function Scrubber({ timeline }: { timeline: Timeline }) {
  const time = useTapestry((s) => s.time);
  const playing = useTapestry((s) => s.playing);
  const setTime = useTapestry((s) => s.setTime);
  const setPlaying = useTapestry((s) => s.setPlaying);
  const diffAnchor = useTapestry((s) => s.diffAnchor);
  const setDiffAnchor = useTapestry((s) => s.setDiffAnchor);

  const { start, end } = timeline;
  const current = time ?? end;
  const span = Math.max(1, end - start);
  const diffActive = diffAnchor !== null;

  // Toggling diff on anchors the current instant as A; the scrubber then picks B.
  const toggleDiff = () => setDiffAnchor(diffActive ? null : current);

  // Distinct event instants — the density ticks behind the range track.
  const ticks = useMemo(() => {
    const seen = new Set<number>();
    const out: number[] = [];
    for (const event of timeline.events) {
      if (!seen.has(event.t)) {
        seen.add(event.t);
        out.push(event.t);
      }
    }
    return out;
  }, [timeline]);

  return (
    <div className="scrubber" role="group" aria-label="Time scrubber">
      <button
        type="button"
        className="scrubber__play"
        aria-pressed={playing}
        aria-label={playing ? "Pause replay" : "Play replay"}
        onClick={() => setPlaying(!playing)}
      >
        {playing ? <PauseIcon /> : <PlayIcon />}
      </button>

      <div className="scrubber__track">
        <div className="scrubber__ticks" aria-hidden="true">
          {ticks.map((t) => (
            <span
              key={t}
              className="scrubber__tick"
              style={{ left: `${((t - start) / span) * 100}%` }}
            />
          ))}
          {diffActive && (
            <span
              className="scrubber__anchor"
              style={{ left: `${((diffAnchor - start) / span) * 100}%` }}
              title="Diff anchor (A)"
            >
              A
            </span>
          )}
        </div>
        <input
          className="scrubber__range"
          type="range"
          min={start}
          max={end}
          step="any"
          value={current}
          aria-label="Scrub through the graph's construction"
          aria-valuetext={formatInstant(current)}
          onChange={(event) => {
            setPlaying(false);
            setTime(Number(event.target.value));
          }}
        />
      </div>

      <span className="scrubber__label">
        {formatInstant(current)}
        <span className="scrubber__labelrel"> · {formatOffset(current, start)}</span>
      </span>

      <button
        type="button"
        className="scrubber__diff"
        aria-pressed={diffActive}
        title={
          diffActive
            ? "Exit diff mode"
            : "Diff mode: anchor this instant, then scrub to compare"
        }
        onClick={toggleDiff}
      >
        Diff
      </button>
    </div>
  );
}
