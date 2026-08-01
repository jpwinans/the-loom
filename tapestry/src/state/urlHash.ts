import { useTapestry, type Filters, type View } from "./store";

export interface HashState {
  view: View;
  selection: string | null;
  filters: Filters;
  /** The Chronicle scrubber position, epoch ms; absent ⇒ end/current. */
  time?: number | null;
}

export function serializeState(state: HashState): string {
  return "#s=" + encodeURIComponent(JSON.stringify(state));
}

export function parseHash(hash: string): Partial<HashState> {
  if (!hash.startsWith("#s=")) return {};
  try {
    return JSON.parse(decodeURIComponent(hash.slice(3))) as HashState;
  } catch {
    return {};
  }
}

/**
 * Apply a serialized hash live: stamp it onto the URL, then push its view,
 * selection, filters, and Chronicle time position into the store. Shared by the
 * initial-mount restore (App) and applying a saved view (Explorer) so both go
 * through one path — a saved view takes effect immediately, with no reload.
 */
export function applyHash(hash: string): void {
  window.history.replaceState(null, "", hash);
  const parsed = parseHash(hash);
  const state = useTapestry.getState();
  if (parsed.view) state.setView(parsed.view);
  if ("selection" in parsed) state.select(parsed.selection ?? null);
  if (parsed.filters) state.setFilters(parsed.filters);
  if ("time" in parsed) state.setTime(parsed.time ?? null);
}
