/**
 * format — the two instant formatters the Chronicle scrubber and event list
 * share, kept pure so the timeline label and each event row read the same way.
 */

/** Compact absolute instant, e.g. "Jul 11 · 17:48:06". `"—"` for a bad value. */
export function formatInstant(ms: number): string {
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) return "—";
  const date = d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  const time = d.toLocaleTimeString(undefined, { hour12: false });
  return `${date} · ${time}`;
}

/** Elapsed offset from `base`, e.g. "+0.0s", "+9.9s", "+3m 12s", "+2h 24m". */
export function formatOffset(ms: number, base: number): string {
  const total = Math.max(0, ms - base);
  const seconds = total / 1000;
  if (seconds < 60) return `+${seconds < 10 ? seconds.toFixed(1) : String(Math.round(seconds))}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `+${minutes}m ${Math.round(seconds % 60)}s`;
  const hours = Math.floor(minutes / 60);
  return `+${hours}h ${minutes % 60}m`;
}
