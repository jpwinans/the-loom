export interface TapestryBundleRaw {
  schemaVersion: number;
  meta: {
    graph: string;
    title?: string;
    theme?: string;
    /** Bi-temporal bound: when set, the bundle is the graph as of this instant. */
    asOf?: string;
    scope: string;
    generatedAt: string;
    entityCount: number;
    relationCount: number;
    sections: string[];
    /** Present only when `maxEntities` capped the scope — a "showing top N of
     * M" signal for a degree-ranked truncation (see `theloom/viz/bundle.py`'s
     * `_truncate_by_degree`). Absent on every bundle the cap didn't fire on. */
    truncated?: { total: number; kept: number; by: string };
  };
  entities: Record<string, unknown>[];
  relations: Record<string, unknown>[];
  analytics?: {
    /** measure -> entityId -> score (`degree`, `betweenness`, `pagerank`, ...). */
    centrality: Record<string, Record<string, number>>;
    components: string[][];
    loops: Record<string, unknown>[];
    leveragePoints: Record<string, unknown>[];
    bridges: Record<string, unknown>[];
  };
  temporal?: {
    events: { id: string; at: string; type: string; payload: Record<string, unknown> }[];
  };
  semantic?: {
    method: string;
    projection: Record<string, number[]>;
    clusters?: { id: number; label: string; entityIds: string[]; size: number }[];
  };
}

import { detectLive } from "./live";

export function parseInlineBundle(text: string): TapestryBundleRaw | null {
  // Dev mode ships the raw `__TAPESTRY_BUNDLE__` sentinel (not valid JSON) in
  // index.html; a built/rendered page has real JSON injected by render_html.
  // Detecting via a JSON.parse failure — rather than comparing against the
  // sentinel string literal — means the literal text never has to appear in
  // application source, so it can never be constant-folded into the built JS
  // bundle: html.py's render_html does an unbounded string replace of every
  // sentinel occurrence in the template, and a second occurrence inside the
  // minified script would get overwritten with the bundle payload and break it.
  try {
    return JSON.parse(text) as TapestryBundleRaw;
  } catch {
    return null;
  }
}

export async function loadBundle(graph?: string): Promise<TapestryBundleRaw> {
  const live = detectLive();
  if (live) {
    const url = graph
      ? `${live.apiBase}/bundle?graph=${encodeURIComponent(graph)}`
      : `${live.apiBase}/bundle`;
    const response = await fetch(url);
    return (await response.json()) as TapestryBundleRaw;
  }
  const block = document.getElementById("tapestry-data");
  const inline = block ? parseInlineBundle(block.textContent ?? "") : null;
  if (inline) return inline;
  const response = await fetch("/fixtures/dev-bundle.json");
  return (await response.json()) as TapestryBundleRaw;
}
