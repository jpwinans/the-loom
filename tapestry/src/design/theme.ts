import type { Theme } from "../state/store";

export type ResolvedTheme = "dark" | "light";

/** True when the OS/browser reports a dark color-scheme preference. */
export function systemPrefersDark(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-color-scheme: dark)").matches
  );
}

/** Collapse the tri-state `theme` down to the concrete theme actually rendered. */
export function resolveTheme(theme: Theme): ResolvedTheme {
  if (theme === "auto") return systemPrefersDark() ? "dark" : "light";
  return theme;
}

/**
 * Stamp the resolved theme onto the document root so `tokens.css` can swap the
 * custom properties. Mirrors the viewer-theme contract used elsewhere in the app.
 */
export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = resolveTheme(theme);
}
