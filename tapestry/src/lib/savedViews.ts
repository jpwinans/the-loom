/**
 * savedViews — named bookmarks of the Explorer's deep-link hash, kept in
 * `localStorage` per graph so switching between two bundles never mixes their
 * views. Each entry is `{name, hash, savedAt}`; saving a name that already
 * exists overwrites it (last save wins) rather than accumulating duplicates.
 */
export interface SavedView {
  name: string;
  hash: string;
  savedAt: string;
}

/** The portable export/import envelope for a graph's saved views. */
export interface ViewsEnvelope {
  schema: "tapestry-views@1";
  graph: string;
  views: SavedView[];
}

function storageKey(graph: string): string {
  return `tapestry:views:${graph}`;
}

/** All saved views for `graph`, oldest to newest. Corrupt storage reads as empty. */
export function listViews(graph: string): SavedView[] {
  const raw = localStorage.getItem(storageKey(graph));
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as SavedView[]) : [];
  } catch {
    return [];
  }
}

/** Replace or append `view` by name — the one storage-write primitive every mutator shares. */
function putView(graph: string, view: SavedView): void {
  const views = listViews(graph).filter((v) => v.name !== view.name);
  views.push(view);
  localStorage.setItem(storageKey(graph), JSON.stringify(views));
}

/** Save (or overwrite) a named view of `hash` for `graph`. */
export function saveView(graph: string, name: string, hash: string): void {
  putView(graph, { name, hash, savedAt: new Date().toISOString() });
}

/** Remove a named view for `graph`, if present. */
export function deleteView(graph: string, name: string): void {
  const views = listViews(graph).filter((v) => v.name !== name);
  localStorage.setItem(storageKey(graph), JSON.stringify(views));
}

/**
 * Rename a saved view in place, preserving its `hash`/`savedAt`. Refuses (and
 * mutates nothing) when `from` doesn't exist, `to` is blank, or `to` already
 * names a different saved view — last-write-wins only applies within a single
 * write, never by silently clobbering a rename target.
 */
export function renameView(graph: string, from: string, to: string): boolean {
  const trimmed = to.trim();
  if (!trimmed) return false;
  const views = listViews(graph);
  const existing = views.find((v) => v.name === from);
  if (!existing) return false;
  if (trimmed !== from && views.some((v) => v.name === trimmed)) return false;
  const next = views.map((v) => (v.name === from ? { ...v, name: trimmed } : v));
  localStorage.setItem(storageKey(graph), JSON.stringify(next));
  return true;
}

/** The stored hash for a named view, or `null` if no such view is saved for `graph`. */
export function resolveViewHash(graph: string, name: string): string | null {
  return listViews(graph).find((v) => v.name === name)?.hash ?? null;
}

/**
 * A portable JSON snapshot of `graph`'s saved views — download it, hand it to
 * a teammate, or archive it alongside a static export. See `importViews`.
 */
export function serializeViews(graph: string): string {
  const envelope: ViewsEnvelope = { schema: "tapestry-views@1", graph, views: listViews(graph) };
  return JSON.stringify(envelope);
}

/**
 * Parse a `serializeViews` payload and merge its entries into `graph`'s saved
 * views (last-write-wins by name, same as `saveView`/`putView`; an entry's
 * original `savedAt` is kept rather than restamped). Never throws — a
 * malformed payload (bad JSON, wrong shape, or no `views` array) reports
 * `{ added: 0, error }` and mutates nothing.
 */
export function importViews(graph: string, json: string): { added: number; error?: string } {
  let parsed: unknown;
  try {
    parsed = JSON.parse(json);
  } catch {
    return { added: 0, error: "That file isn't valid JSON." };
  }
  const views = (parsed as Partial<ViewsEnvelope> | null)?.views;
  if (typeof parsed !== "object" || parsed === null || !Array.isArray(views)) {
    return { added: 0, error: "That file isn't a recognized saved-views export." };
  }
  let added = 0;
  for (const entry of views) {
    if (
      !entry ||
      typeof entry !== "object" ||
      typeof (entry as Partial<SavedView>).name !== "string" ||
      typeof (entry as Partial<SavedView>).hash !== "string" ||
      typeof (entry as Partial<SavedView>).savedAt !== "string"
    ) {
      continue;
    }
    const { name, hash, savedAt } = entry as SavedView;
    putView(graph, { name, hash, savedAt });
    added += 1;
  }
  if (added === 0 && views.length > 0) {
    return { added: 0, error: "No valid saved views were found in that file." };
  }
  return { added };
}
