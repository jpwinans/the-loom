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
    /** Present only when `meta.asOf` is set: analytics are never recomputed
     * historically, so this section is always the graph's *current* state even
     * though `entities`/`relations` are bounded to `meta.asOf`. Absent on a
     * current-time (non-asOf) bundle. */
    temporalScope?: string;
  };
  temporal?: {
    events: { id: string; at: string; type: string; payload: Record<string, unknown> }[];
  };
  semantic?: {
    method: string;
    projection: Record<string, number[]>;
    clusters?: { id: number; label: string; entityIds: string[]; size: number }[];
    /** Same current-time stamp as `analytics.temporalScope`, and for the same
     * reason: the embedding projection is not recomputed as of the bundle's
     * historical bound. */
    temporalScope?: string;
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

/** Thrown by every `loadBundle` failure path — `source` names which of the
 * three branches (live API, inline block, dev fixture) failed, so the app
 * shell's error state can say something more useful than "something broke". */
export class BundleLoadError extends Error {
  readonly source: string;

  constructor(source: string, message: string) {
    super(message);
    this.name = "BundleLoadError";
    this.source = source;
  }
}

/** Fetch + parse a bundle from `url`, raising a `BundleLoadError` naming
 * `source` for a network failure, a non-2xx response, or malformed JSON —
 * the three ways a bundle load can fail silently without this wrapper. */
async function fetchBundle(url: string, source: string): Promise<TapestryBundleRaw> {
  let response: Response;
  try {
    response = await fetch(url);
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    throw new BundleLoadError(source, `Could not reach ${source}: ${detail}`);
  }
  if (!response.ok) {
    throw new BundleLoadError(
      source,
      `${source} returned ${response.status} ${response.statusText}`,
    );
  }
  try {
    return (await response.json()) as TapestryBundleRaw;
  } catch {
    throw new BundleLoadError(source, `${source} returned malformed JSON`);
  }
}

export async function loadBundle(graph?: string): Promise<TapestryBundleRaw> {
  const live = detectLive();
  if (live) {
    const url = graph
      ? `${live.apiBase}/bundle?graph=${encodeURIComponent(graph)}`
      : `${live.apiBase}/bundle`;
    return fetchBundle(url, "the live API");
  }
  const block = document.getElementById("tapestry-data");
  const inline = block ? parseInlineBundle(block.textContent ?? "") : null;
  if (inline) return inline;
  return fetchBundle("/fixtures/dev-bundle.json", "the dev fixture");
}
