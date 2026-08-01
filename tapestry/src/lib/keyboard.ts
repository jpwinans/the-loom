/**
 * keyboard — a small declarative keydown dispatcher for app-wide shortcuts.
 *
 * `useKeyboard(bindings)` attaches one `keydown` listener to `window` for the
 * lifetime of the calling component and calls the handler for `event.key`, if
 * any. Bindings are read from a ref updated on every render, so the listener
 * itself is attached exactly once — callers can pass a fresh object each
 * render without paying for a remove/add cycle.
 *
 * Two guards keep shortcuts from fighting real typing: a modified keystroke
 * (Cmd/Ctrl/Alt held) is never treated as a bare-letter shortcut, and any
 * keydown whose target is a form control or a `contenteditable` element is
 * ignored outright — the caller's own field handles its own keys (see
 * `SearchBox`'s Escape-to-blur).
 */
import { useEffect, useRef } from "react";

export type KeyBindings = Record<string, (event: KeyboardEvent) => void>;

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  return target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT";
}

export function useKeyboard(bindings: KeyBindings): void {
  const bindingsRef = useRef(bindings);
  bindingsRef.current = bindings;

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
      if (isTypingTarget(event.target)) return;
      const handler = bindingsRef.current[event.key];
      if (handler) handler(event);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);
}
