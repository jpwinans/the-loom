/**
 * layout — ForceAtlas2 force-directed layout with start/stop controls.
 *
 * Preferred path is graphology's worker supervisor, which runs FA2 off the main
 * thread. That supervisor builds its worker from a *stringified* function via a
 * Blob URL (`URL.createObjectURL(new Blob([fn.toString()]))`), not from a
 * separate `new URL("./worker.js", import.meta.url)` chunk — so it survives
 * vite-plugin-singlefile inlining, where no external worker file can be served.
 *
 * Where a Worker cannot be constructed (headless, or a hardened CSP that blocks
 * blob: workers), we fall back to running FA2 synchronously in small
 * rAF-chunked batches so the built artifact stays functional either way.
 */
import forceAtlas2 from "graphology-layout-forceatlas2";
import FA2Layout from "graphology-layout-forceatlas2/worker";
import type Graph from "graphology";

type FA2Settings = ReturnType<typeof forceAtlas2.inferSettings>;

/**
 * Above this node count the layout is tuned for scale: Barnes-Hut is forced on
 * with a coarser theta (fewer, cheaper far-field approximations per tick), and
 * the synchronous fallback drops to one FA2 iteration per frame so it never
 * blocks the main thread for long. `inferSettings` already flips
 * `barnesHutOptimize` on above ~2000 nodes; we make it explicit and add the
 * theta so the intent survives a library default change. Below the threshold —
 * every fixture and every existing test — settings are byte-for-byte unchanged.
 */
const SCALE_THRESHOLD = 3000;
/** Barnes-Hut approximation angle used at scale (higher = faster, coarser). */
const SCALE_BARNES_HUT_THETA = 0.6;

export interface LayoutController {
  start(): void;
  stop(): void;
  kill(): void;
  readonly running: boolean;
}

function workerConstructible(): boolean {
  return (
    typeof Worker !== "undefined" &&
    typeof Blob !== "undefined" &&
    typeof URL !== "undefined" &&
    typeof URL.createObjectURL === "function"
  );
}

/** Synchronous FA2 driven by requestAnimationFrame — the no-worker fallback. */
function createSyncLayout(
  graph: Graph,
  settings: FA2Settings,
  iterations: number,
): LayoutController {
  let running = false;
  let frame: number | null = null;

  const stop = (): void => {
    running = false;
    if (frame !== null) {
      cancelAnimationFrame(frame);
      frame = null;
    }
  };

  const step = (): void => {
    if (!running) return;
    forceAtlas2.assign(graph, { iterations, settings });
    frame = requestAnimationFrame(step);
  };

  const start = (): void => {
    if (running) return;
    running = true;
    frame = requestAnimationFrame(step);
  };

  return {
    start,
    stop,
    kill: stop,
    get running() {
      return running;
    },
  };
}

export function createLayout(graph: Graph): LayoutController {
  const settings = forceAtlas2.inferSettings(graph);
  const atScale = graph.order > SCALE_THRESHOLD;
  if (atScale) {
    settings.barnesHutOptimize = true;
    settings.barnesHutTheta = SCALE_BARNES_HUT_THETA;
  }

  if (workerConstructible()) {
    try {
      const supervisor = new FA2Layout(graph, { settings });
      return {
        start: () => supervisor.start(),
        stop: () => supervisor.stop(),
        kill: () => supervisor.kill(),
        get running() {
          return supervisor.isRunning();
        },
      };
    } catch {
      // Worker construction blocked — drop to the synchronous driver below.
    }
  }

  // A big graph runs one FA2 pass per frame so the fallback stays responsive;
  // a small one keeps the original five for a quick settle.
  return createSyncLayout(graph, settings, atScale ? 1 : 5);
}
