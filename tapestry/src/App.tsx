import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";
import { useBundle, useLiveControls } from "./lib/BundleContext";
import { useTapestry, type Theme, type View } from "./state/store";
import { applyTheme } from "./design/theme";
import { applyHash, serializeState } from "./state/urlHash";
import { resolveViewHash } from "./lib/savedViews";
import { nextRovingIndex } from "./lib/roving";
import { useKeyboard } from "./lib/keyboard";
import { HelpOverlay } from "./views/HelpOverlay";
import { ENTITY_TYPES } from "./design/palette";
import { Explorer } from "./views/explorer/Explorer";
import { Overview } from "./views/overview/Overview";
import { Systems } from "./views/systems/SystemsView";
import { Chronicle } from "./views/chronicle/Chronicle";
import { SemanticMap } from "./views/semantic/SemanticView";
import "./App.css";

interface ViewDef {
  id: View;
  label: string;
  color: string;
}

const VIEWS: ViewDef[] = [
  { id: "explorer", label: "Explorer", color: "var(--type-concept)" },
  { id: "overview", label: "Overview", color: "var(--type-pattern)" },
  { id: "systems", label: "Systems", color: "var(--type-loop)" },
  { id: "chronicle", label: "Chronicle", color: "var(--type-event)" },
  { id: "semantic", label: "Semantic", color: "var(--type-source)" },
];

/** Compact absolute instant for the "as of" header note. */
function formatAsOf(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const THEMES: { id: Theme; label: string; icon: ReactNode }[] = [
  {
    id: "auto",
    label: "Auto",
    icon: (
      <svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden="true">
        <circle cx="8" cy="8" r="6.25" stroke="currentColor" strokeWidth="1.4" />
        <path d="M8 1.75a6.25 6.25 0 0 1 0 12.5z" fill="currentColor" />
      </svg>
    ),
  },
  {
    id: "light",
    label: "Light",
    icon: (
      <svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden="true">
        <circle cx="8" cy="8" r="3.1" stroke="currentColor" strokeWidth="1.4" />
        <path
          d="M8 1.4v1.6M8 13v1.6M1.4 8h1.6M13 8h1.6M3.3 3.3l1.13 1.13M11.57 11.57l1.13 1.13M12.7 3.3l-1.13 1.13M4.43 11.57 3.3 12.7"
          stroke="currentColor"
          strokeWidth="1.4"
          strokeLinecap="round"
        />
      </svg>
    ),
  },
  {
    id: "dark",
    label: "Dark",
    icon: (
      <svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden="true">
        <path
          d="M13.4 9.7A5.6 5.6 0 0 1 6.3 2.6a5.6 5.6 0 1 0 7.1 7.1z"
          stroke="currentColor"
          strokeWidth="1.4"
          strokeLinejoin="round"
        />
      </svg>
    ),
  },
];

/** The Loom's mark: beam, hanging warp, and a weft mid-pass — the thread runs
 * over the first warp, behind the second (the gap), over the third, with the
 * shuttle leading the pass. The instrument, not the finished cloth. */
function BrandMark() {
  return (
    <svg
      className="brand__mark"
      width="18"
      height="18"
      viewBox="0 0 18 18"
      fill="none"
      aria-hidden="true"
    >
      <path d="M2 2.8h14" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <path
        d="M3.6 5.2v10.3M7.2 5.2v10.3M10.8 5.2v10.3M14.4 5.2v10.3"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        opacity="0.5"
      />
      <path
        d="M1.8 10.4h3.4M8.6 10.4h3.4"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
      <path d="M13.4 10.4 15.1 9.3 16.8 10.4 15.1 11.5Z" fill="currentColor" />
    </svg>
  );
}

/** A question glyph in a ring — the header's help affordance, matching the
 * thin-stroke, round-cap idiom of the other header icons. */
function HelpIcon() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      aria-hidden="true"
    >
      <circle cx="8" cy="8" r="6.25" />
      <path
        d="M6.15 6.1a1.9 1.9 0 1 1 2.55 1.79c-.5.2-.95.6-.95 1.21v.35"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="7.75" cy="11.5" r="0.55" fill="currentColor" stroke="none" />
    </svg>
  );
}

/** A circular reload arrow, in the header icon idiom (thin stroke, round caps). */
function RefreshIcon() {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <polyline points="23 4 23 10 17 10" />
      <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
    </svg>
  );
}

export function App() {
  const bundle = useBundle();
  const { live, graphs, currentGraph, setGraph, refresh } = useLiveControls();
  const view = useTapestry((s) => s.view);
  const theme = useTapestry((s) => s.theme);
  const setView = useTapestry((s) => s.setView);
  const setTheme = useTapestry((s) => s.setTheme);

  // Roving-focus refs for the two composite widgets: a tablist and a
  // radiogroup are each ONE tab stop, and arrow keys move focus within them.
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const themeRefs = useRef<(HTMLButtonElement | null)[]>([]);

  // A single polite live region for async surface changes. Content present at
  // load is never announced, so it stays empty until a graph switch or a
  // live-mode refresh actually reloads the bundle. `bundle` is the reload
  // signal; comparing the freshly-loaded graph name against the last one
  // announced tells a switch ("Loaded …") from a refetch ("Refreshed …").
  const [announcement, setAnnouncement] = useState("");
  const lastAnnouncedGraph = useRef<string | null>(null);
  useEffect(() => {
    const graph = bundle.meta.graph;
    const prev = lastAnnouncedGraph.current;
    lastAnnouncedGraph.current = graph;
    if (prev === null) return; // first load — nothing to announce
    setAnnouncement(prev === graph ? `Refreshed ${graph}` : `Loaded ${graph}`);
  }, [bundle]);

  const currentTabIndex = VIEWS.findIndex((v) => v.id === view);
  const onTabKeyDown = (event: ReactKeyboardEvent<HTMLElement>) => {
    const next = nextRovingIndex(currentTabIndex, VIEWS.length, event.key);
    if (next === null) return;
    event.preventDefault();
    setView(VIEWS[next].id);
    tabRefs.current[next]?.focus();
  };

  const currentThemeIndex = THEMES.findIndex((t) => t.id === theme);
  const onThemeKeyDown = (event: ReactKeyboardEvent<HTMLElement>) => {
    const next = nextRovingIndex(currentThemeIndex, THEMES.length, event.key);
    if (next === null) return;
    event.preventDefault();
    setTheme(THEMES[next].id);
    themeRefs.current[next]?.focus();
  };

  // The `?` help overlay. `useKeyboard` already ignores modifiers and typing
  // targets, so `?` opens it globally without fighting a focused field; App
  // owns the state and restores focus to the trigger when it closes.
  const [helpOpen, setHelpOpen] = useState(false);
  const helpButtonRef = useRef<HTMLButtonElement | null>(null);
  useKeyboard({ "?": () => setHelpOpen(true) });
  const closeHelp = () => {
    setHelpOpen(false);
    helpButtonRef.current?.focus();
  };

  // Restore deep-linked state from the URL hash on first mount. Two hash
  // shapes are recognized: `#view=<name>` names a saved view for this graph —
  // resolved to its stored `#s=` hash and applied (which immediately rewrites
  // the URL, so the subscriber below stays in sync); an unresolved or absent
  // name falls through to the plain `#s=` path, same as any other hash. Saved
  // views (Explorer) apply live through the same `applyHash` so both paths
  // agree.
  useEffect(() => {
    const hash = window.location.hash;
    if (!hash) return;
    const namedView = /^#view=(.+)$/.exec(hash);
    if (namedView) {
      const resolved = resolveViewHash(bundle.meta.graph, decodeURIComponent(namedView[1]));
      if (resolved) {
        applyHash(resolved);
        return;
      }
    }
    applyHash(hash);
  }, []);

  // Keep the URL hash current so the static page is deep-linkable on reload.
  useEffect(() => {
    const write = () => {
      const { view: v, selection, filters, time } = useTapestry.getState();
      const hash = serializeState({ view: v, selection, filters, time });
      if (hash !== window.location.hash) {
        window.history.replaceState(null, "", hash);
      }
    };
    write();
    return useTapestry.subscribe(write);
  }, []);

  // Resolve + apply the theme; track OS changes while on "auto".
  useEffect(() => {
    applyTheme(theme);
    if (theme !== "auto") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => applyTheme("auto");
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [theme]);

  return (
    <div className="app">
      <header className="app__header">
        <div className="brand">
          <BrandMark />
          <span className="brand__name">The Loom</span>
          <span className="brand__sep" aria-hidden="true" />
          <span className="brand__context">
            <span className="brand__graph">{bundle.meta.title ?? bundle.meta.graph}</span>
            <span className="brand__counts">
              {bundle.meta.entityCount.toLocaleString()} entities ·{" "}
              {bundle.meta.relationCount.toLocaleString()} relations
              {bundle.meta.asOf && (
                <span className="brand__asof"> · as of {formatAsOf(bundle.meta.asOf)}</span>
              )}
            </span>
          </span>
          {live && (
            <span className="brand__live" role="status" aria-label="Live server">
              <span className="brand__livedot" aria-hidden="true" />
              Live
            </span>
          )}
        </div>

        <nav className="tabs" role="tablist" aria-label="View" onKeyDown={onTabKeyDown}>
          {VIEWS.map((v, i) => (
            <button
              key={v.id}
              id={`tab-${v.id}`}
              ref={(el) => {
                tabRefs.current[i] = el;
              }}
              className="tabs__tab"
              role="tab"
              type="button"
              aria-selected={v.id === view}
              aria-controls={`panel-${v.id}`}
              tabIndex={v.id === view ? 0 : -1}
              onClick={() => setView(v.id)}
            >
              <span className="tabs__dot" style={{ background: v.color }} />
              {v.label}
            </button>
          ))}
        </nav>

        {live && (
          <div className="live__controls">
            {graphs.length > 1 && (
              <label className="live__switcher">
                <span className="live__switcherlabel">Graph</span>
                <select
                  className="live__select"
                  value={currentGraph}
                  onChange={(e) => setGraph(e.target.value)}
                >
                  {graphs.map((g) => (
                    <option key={g} value={g}>
                      {g}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <button
              type="button"
              className="live__refresh"
              onClick={refresh}
              aria-label="Refresh from the server"
              title="Refresh from the server"
            >
              <RefreshIcon />
            </button>
          </div>
        )}

        <button
          type="button"
          ref={helpButtonRef}
          className="help-trigger"
          aria-label="Keyboard shortcuts"
          aria-keyshortcuts="?"
          title="Keyboard shortcuts (?)"
          onClick={() => setHelpOpen(true)}
        >
          <HelpIcon />
        </button>

        <div className="theme" role="radiogroup" aria-label="Theme" onKeyDown={onThemeKeyDown}>
          {THEMES.map((t, i) => (
            <button
              key={t.id}
              ref={(el) => {
                themeRefs.current[i] = el;
              }}
              className="theme__opt"
              role="radio"
              type="button"
              aria-checked={t.id === theme}
              tabIndex={t.id === theme ? 0 : -1}
              title={`${t.label} theme`}
              onClick={() => setTheme(t.id)}
            >
              {t.icon}
              {t.label}
            </button>
          ))}
        </div>
      </header>

      <div className="sr-only" aria-live="polite">
        {announcement}
      </div>

      <div className="ribbon" aria-hidden="true">
        {ENTITY_TYPES.map((type) => (
          <span
            key={type}
            className="ribbon__thread"
            style={{ background: `var(--type-${type})` }}
          />
        ))}
      </div>

      <main className="app__main">
        {view === "overview" ? (
          <Overview key="overview" />
        ) : view === "systems" ? (
          <Systems key="systems" />
        ) : view === "chronicle" ? (
          <Chronicle key="chronicle" />
        ) : view === "semantic" ? (
          <SemanticMap key="semantic" />
        ) : (
          <Explorer key="explorer" />
        )}
      </main>

      <HelpOverlay open={helpOpen} onClose={closeHelp} />
    </div>
  );
}
