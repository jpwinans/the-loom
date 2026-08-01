/**
 * roving — the pure keyboard math for a roving-tabindex composite widget
 * (a `tablist` or a `radiogroup`).
 *
 * Given the currently active index, the item count, and a pressed key, it
 * returns the index focus/selection should move to, or `null` when the key is
 * not a navigation key (so the caller leaves the event untouched — Tab, for
 * one, must fall through so the widget stays a single tab stop). Movement wraps
 * at both ends, matching the WAI-ARIA tabs and radio-group patterns:
 *
 *  - ArrowRight / ArrowDown → next (wrapping past the last back to 0)
 *  - ArrowLeft  / ArrowUp   → previous (wrapping before 0 to the last)
 *  - Home → first, End → last
 *  - anything else → null
 *
 * Kept DOM-free so vitest covers it directly; `App` maps a non-null result to
 * `setView`/`setTheme` plus a `.focus()` on the chosen control.
 */
export function nextRovingIndex(current: number, count: number, key: string): number | null {
  if (count <= 0) return null;
  switch (key) {
    case "ArrowRight":
    case "ArrowDown":
      return (current + 1) % count;
    case "ArrowLeft":
    case "ArrowUp":
      return (current - 1 + count) % count;
    case "Home":
      return 0;
    case "End":
      return count - 1;
    default:
      return null;
  }
}
