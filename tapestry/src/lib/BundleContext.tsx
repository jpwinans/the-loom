/**
 * BundleContext — load the TapestryBundle once and share it, plus the memoized
 * graphology model built from it, with every view.
 *
 * `BundleProvider` performs `loadBundle()` (inline JSON when rendered, dev
 * fixture otherwise, or the live `/api/bundle` when the server injected the live
 * marker) and gates its children on the result, so consumers can treat the
 * bundle and graph as always-present. `buildGraph` runs once per bundle via
 * `useMemo`; the Explorer and Overview read the same graph instance.
 *
 * In live mode the provider also tracks the requested graph and a reload nonce
 * (so the switcher and refresh button re-fetch), and fetches the graph list once.
 * Static and dev modes are unchanged: `live` is `false`, `graphs` is `[]`, and
 * `setGraph`/`refresh` re-run `loadBundle`, which returns the same inline bundle —
 * a harmless no-op refetch.
 */
import Graph from "graphology";
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { buildGraph } from "../views/explorer/buildGraph";
import { loadBundle, type TapestryBundleRaw } from "./data";
import { detectLive, fetchGraphs } from "./live";

interface BundleContextValue {
  bundle: TapestryBundleRaw;
  graph: Graph;
  live: boolean;
  graphs: string[];
  currentGraph: string;
  setGraph: (name: string) => void;
  refresh: () => void;
}

const BundleContext = createContext<BundleContextValue | null>(null);

export function BundleProvider({ children }: { children: ReactNode }) {
  const live = useMemo(() => detectLive(), []);
  const [bundle, setBundle] = useState<TapestryBundleRaw | null>(null);
  const [requestedGraph, setRequestedGraph] = useState<string | undefined>(undefined);
  const [graphs, setGraphs] = useState<string[]>([]);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let alive = true;
    loadBundle(live ? requestedGraph : undefined).then((loaded) => {
      if (alive) setBundle(loaded);
    });
    return () => {
      alive = false;
    };
  }, [live, requestedGraph, nonce]);

  useEffect(() => {
    if (!live) return;
    fetchGraphs(live.apiBase)
      .then(setGraphs)
      .catch(() => setGraphs([]));
  }, [live]);

  if (!bundle) {
    return <div className="app__loading">Loading graph…</div>;
  }
  return (
    <ReadyProvider
      bundle={bundle}
      live={live !== null}
      graphs={graphs}
      currentGraph={requestedGraph ?? bundle.meta.graph}
      setGraph={setRequestedGraph}
      refresh={() => setNonce((n) => n + 1)}
    >
      {children}
    </ReadyProvider>
  );
}

/** Split out so `buildGraph` is memoized under an unconditional hook. */
function ReadyProvider({
  bundle,
  live,
  graphs,
  currentGraph,
  setGraph,
  refresh,
  children,
}: {
  bundle: TapestryBundleRaw;
  live: boolean;
  graphs: string[];
  currentGraph: string;
  setGraph: (name: string) => void;
  refresh: () => void;
  children: ReactNode;
}) {
  const graph = useMemo(() => buildGraph(bundle), [bundle]);
  const value = useMemo(
    () => ({ bundle, graph, live, graphs, currentGraph, setGraph, refresh }),
    [bundle, graph, live, graphs, currentGraph, setGraph, refresh],
  );
  return <BundleContext.Provider value={value}>{children}</BundleContext.Provider>;
}

function useBundleContext(): BundleContextValue {
  const ctx = useContext(BundleContext);
  if (!ctx) throw new Error("useBundle/useGraph must be used within a BundleProvider");
  return ctx;
}

export function useBundle(): TapestryBundleRaw {
  return useBundleContext().bundle;
}

export function useGraph(): Graph {
  return useBundleContext().graph;
}

export interface LiveControls {
  live: boolean;
  graphs: string[];
  currentGraph: string;
  setGraph: (name: string) => void;
  refresh: () => void;
}

/** Live-mode surface for the header: indicator flag, graph list, current graph,
 * and the switch/refresh actions. In static/dev mode `live` is false and
 * `graphs` is empty, so the header renders none of the live cluster. */
export function useLiveControls(): LiveControls {
  const { live, graphs, currentGraph, setGraph, refresh } = useBundleContext();
  return { live, graphs, currentGraph, setGraph, refresh };
}
