/**
 * HelpOverlay — the `?` keyboard-shortcuts dialog.
 *
 * A real modal: `role="dialog" aria-modal="true"`, opened from the `?` key or a
 * header button, focus moved to its close control on open, Tab/Shift+Tab wrapped
 * within it, and `Escape` closing it. App owns the open state and restores focus
 * to the trigger in `onClose`, so a keyboard user never loses their place.
 *
 * The content is a shortcut sheet grouped by scope. Each key is a `<kbd>` keycap
 * (mono face, a hair of bottom-edge depth) — the one distinctive treatment, the
 * rest deliberately quiet so the reference reads at a glance.
 */
import { useEffect, useRef, type KeyboardEvent as ReactKeyboardEvent } from "react";
import "./HelpOverlay.css";

interface Shortcut {
  keys: string[];
  action: string;
}

interface ShortcutGroup {
  scope: string;
  items: Shortcut[];
}

/** Grouped by where each shortcut applies; keys are the literal `event.key`s. */
const GROUPS: ShortcutGroup[] = [
  {
    scope: "Global",
    items: [
      { keys: ["?"], action: "Show this help" },
      { keys: ["←", "→", "Home", "End"], action: "Move within the tab bar and theme control" },
    ],
  },
  {
    scope: "Explorer",
    items: [
      { keys: ["/"], action: "Focus search" },
      { keys: ["p"], action: "Toggle path mode" },
      { keys: ["f"], action: "Fit the graph to view" },
      { keys: ["←", "↑", "→", "↓"], action: "Walk to a neighbour" },
      { keys: ["Esc"], action: "Clear selection and path" },
    ],
  },
  {
    scope: "Chronicle",
    items: [
      { keys: ["←", "→"], action: "Move the time scrubber" },
      { keys: ["Space"], action: "Play or pause replay" },
    ],
  },
  {
    scope: "Systems",
    items: [{ keys: ["Enter"], action: "Isolate a loop or toggle flow" }],
  },
  {
    scope: "Semantic",
    items: [{ keys: ["Enter"], action: "Brush a cluster from the list" }],
  },
];

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function HelpOverlay({ open, onClose }: { open: boolean; onClose: () => void }) {
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);

  // On open, move focus into the dialog (its close button).
  useEffect(() => {
    if (open) closeRef.current?.focus();
  }, [open]);

  if (!open) return null;

  const onKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key !== "Tab") return;
    const dialog = dialogRef.current;
    if (!dialog) return;
    const focusables = Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE));
    if (focusables.length === 0) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    const active = document.activeElement;
    if (event.shiftKey) {
      if (active === first || !dialog.contains(active)) {
        event.preventDefault();
        last.focus();
      }
    } else if (active === last || !dialog.contains(active)) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <div
      className="help"
      // A backdrop click (outside the dialog) dismisses, like any modal.
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className="help__dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="help-title"
        ref={dialogRef}
        onKeyDown={onKeyDown}
      >
        <header className="help__head">
          <div>
            <p className="help__eyebrow">Reference</p>
            <h2 className="help__title" id="help-title">
              Keyboard shortcuts
            </h2>
          </div>
          <button
            ref={closeRef}
            type="button"
            className="help__close"
            aria-label="Close help"
            onClick={onClose}
          >
            <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
              <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </button>
        </header>

        <div className="help__groups">
          {GROUPS.map((group) => (
            <section className="help__group" key={group.scope}>
              <h3 className="help__scope">{group.scope}</h3>
              <dl className="help__list">
                {group.items.map((shortcut) => (
                  <div className="help__row" key={shortcut.action}>
                    <dt className="help__action">{shortcut.action}</dt>
                    <dd className="help__keys">
                      {shortcut.keys.map((key) => (
                        <kbd className="help__key" key={key}>
                          {key}
                        </kbd>
                      ))}
                    </dd>
                  </div>
                ))}
              </dl>
            </section>
          ))}
        </div>

        <p className="help__foot">
          Every on-screen control is reachable with <kbd className="help__key">Tab</kbd> — activate it
          with <kbd className="help__key">Enter</kbd> or <kbd className="help__key">Space</kbd>. In any
          graph view, <strong>drag a node</strong> to reposition it — its edges follow and it stays
          where you drop it. The Semantic lasso is pointer-only; use <strong>Brush cluster</strong> for
          a keyboard selection.
        </p>
      </div>
    </div>
  );
}
