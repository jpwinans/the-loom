/**
 * eventWindow — the pure windowing arithmetic behind the Chronicle event list's
 * virtualization.
 *
 * A 100k-row event log cannot mount 100k `<li>`s without stalling the main
 * thread, so `EventList` renders only the rows the scroll viewport can show
 * (plus a small overscan margin), padded above and below by spacers so the
 * scrollbar keeps its true length. This module holds just the index maths so it
 * is unit-testable without a DOM: given the scroll offset and a fixed row
 * height, it returns the half-open `[start, end)` slice to render.
 *
 * The window is computed from the *unclamped* first index (`floor(scrollTop /
 * rowHeight) - overscan`) so the trailing edge always covers the viewport even
 * when the leading edge is clamped to zero; only the returned bounds are clamped
 * into `[0, count]`.
 */
export interface Window {
  start: number;
  end: number;
}

export function visibleRange(
  scrollTop: number,
  rowHeight: number,
  viewportHeight: number,
  count: number,
  overscan: number,
): Window {
  if (rowHeight <= 0) return { start: 0, end: count };
  const first = Math.floor(scrollTop / rowHeight) - overscan;
  const visible = Math.ceil(viewportHeight / rowHeight);
  const last = first + visible + overscan * 2;
  return {
    start: Math.min(count, Math.max(0, first)),
    end: Math.min(count, Math.max(0, last)),
  };
}
