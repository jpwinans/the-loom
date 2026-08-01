import { create } from "zustand";

export interface Filters {
  entityTypes: string[];
  relationTypes: string[];
  confidenceMin: number;
  statuses: string[];
}

export type View = "explorer" | "overview" | "systems" | "chronicle" | "semantic";
export type Theme = "auto" | "dark" | "light";
/** The path tool's two clicked endpoints, in click order: [from, to]. Either
 * (or both) may still be unset while the user is picking. */
export type PathEndpoints = [string | null, string | null];

export const EMPTY_FILTERS: Filters = {
  entityTypes: [],
  relationTypes: [],
  confidenceMin: 0,
  statuses: [],
};

export const EMPTY_PATH_ENDPOINTS: PathEndpoints = [null, null];

interface TapestryState {
  view: View;
  theme: Theme;
  selection: string | null;
  filters: Filters;
  pathMode: boolean;
  pathEndpoints: PathEndpoints;
  /** The Systems view's isolated feedback loop; null ⇒ the whole causal graph. */
  selectedLoop: string | number | null;
  /** The Chronicle scrubber position, epoch ms; null ⇒ the end (current state). */
  time: number | null;
  /** Whether the Chronicle scrubber is playing through construction. */
  playing: boolean;
  /** The Chronicle diff anchor (the first-picked instant); null ⇒ diff mode off. */
  diffAnchor: number | null;
  /** The Semantic Map lasso's brushed entity ids; null ⇒ no active brush. */
  brushedIds: string[] | null;
  setBrushed: (ids: string[] | null) => void;
  setView: (view: View) => void;
  setTheme: (theme: Theme) => void;
  select: (id: string | null) => void;
  setFilters: (partial: Partial<Filters>) => void;
  setPathMode: (pathMode: boolean) => void;
  setPathEndpoints: (endpoints: PathEndpoints) => void;
  clearPath: () => void;
  selectLoop: (id: string | number | null) => void;
  setTime: (time: number | null) => void;
  setPlaying: (playing: boolean) => void;
  setDiffAnchor: (anchor: number | null) => void;
}

export const useTapestry = create<TapestryState>((set) => ({
  view: "explorer",
  theme: "auto",
  selection: null,
  filters: EMPTY_FILTERS,
  pathMode: false,
  pathEndpoints: EMPTY_PATH_ENDPOINTS,
  selectedLoop: null,
  time: null,
  playing: false,
  diffAnchor: null,
  brushedIds: null,
  setBrushed: (brushedIds) => set({ brushedIds }),
  setView: (view) => set({ view }),
  setTheme: (theme) => set({ theme }),
  select: (selection) => set({ selection }),
  setFilters: (partial) => set((s) => ({ filters: { ...s.filters, ...partial } })),
  setPathMode: (pathMode) => set({ pathMode }),
  setPathEndpoints: (pathEndpoints) => set({ pathEndpoints }),
  clearPath: () => set({ pathEndpoints: EMPTY_PATH_ENDPOINTS }),
  selectLoop: (selectedLoop) => set({ selectedLoop }),
  setTime: (time) => set({ time }),
  setPlaying: (playing) => set({ playing }),
  setDiffAnchor: (diffAnchor) => set({ diffAnchor }),
}));
