/**
 * LoopPanel — the Systems view's right rail: every feedback loop the analytics
 * pass found, each tagged reinforcing (R) or balancing (B). Clicking a row
 * isolates that loop on the canvas; clicking it again (or "Clear isolation")
 * releases it. The R/B letter badge is the primary cue — colour (the shared
 * polarity poles) only reinforces it — so the classification is never read from
 * colour alone. When the bundle carries no analytics, or none of its loops are
 * feedback cycles, the rail explains why rather than sitting empty.
 */
import type { LoopInfo } from "./systems";

interface LoopPanelProps {
  loops: LoopInfo[];
  hasAnalytics: boolean;
  selected: string | number | null;
  onSelect: (id: number | null) => void;
}

export function LoopPanel({ loops, hasAnalytics, selected, onSelect }: LoopPanelProps) {
  return (
    <aside className="loops" aria-label="Feedback loops">
      <header className="loops__head">
        <h2 className="loops__title">Feedback loops</h2>
        {loops.length > 0 && <span className="loops__count">{loops.length}</span>}
      </header>

      {!hasAnalytics ? (
        <p className="loops__empty">
          Re-export with the analytics section to surface feedback loops.
        </p>
      ) : loops.length === 0 ? (
        <p className="loops__empty">No feedback loops in this causal scope.</p>
      ) : (
        <>
          <ul className="loops__list">
            {loops.map((loop, i) => {
              const active = selected === i;
              const reinforcing = loop.classification === "reinforcing";
              return (
                <li key={loop.id ?? i}>
                  <button
                    type="button"
                    className={`loops__row${active ? " loops__row--active" : ""}`}
                    aria-pressed={active}
                    onClick={() => onSelect(active ? null : i)}
                  >
                    <span
                      className={`loops__badge loops__badge--${reinforcing ? "r" : "b"}`}
                      aria-hidden="true"
                    >
                      {reinforcing ? "R" : "B"}
                    </span>
                    <span className="loops__text">
                      <span className="loops__name">{loop.name}</span>
                      <span className="loops__meta">
                        {reinforcing ? "Reinforcing" : "Balancing"} · {loop.memberCount} variables
                      </span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>

          <div className="loops__foot">
            <ul className="loops__legend">
              <li className="loops__legenditem">
                <span className="loops__badge loops__badge--r loops__badge--sm" aria-hidden="true">
                  R
                </span>
                reinforcing
              </li>
              <li className="loops__legenditem">
                <span className="loops__badge loops__badge--b loops__badge--sm" aria-hidden="true">
                  B
                </span>
                balancing
              </li>
            </ul>
            <button
              type="button"
              className="loops__clear"
              disabled={selected === null}
              onClick={() => onSelect(null)}
            >
              Clear isolation
            </button>
          </div>
        </>
      )}
    </aside>
  );
}
