/** What the chart shows: a span of time ending either now (live: it follows the clock) or at a fixed instant (the
 *  operator zoomed or scrolled into the history). Pure; Chart.tsx turns it into a request per range (D-UI-11). */
export type View = { span: number; end: number | null }; // ms; end null = live

export const MIN_SPAN = 10_000; // 10 s: below that the 100 ms buckets hold one frame each
export const MAX_SPAN = 86_400_000; // the server's longest range per request (24 h)

const clampSpan = (ms: number) => Math.min(MAX_SPAN, Math.max(MIN_SPAN, Math.round(ms)));

/** A view whose end is at or after now follows the clock again. */
const settle = (span: number, end: number, now: number): View => (end >= now ? { span, end: null } : { span, end });

export const rangeOf = (v: View, now: number): [number, number] => {
  const end = v.end ?? now;
  return [end - v.span, end];
};

/** factor < 1 zooms in. `at` is where the anchor sits across the plot (0 left .. 1 right); its time stays put. */
export function zoom(v: View, factor: number, at: number, now: number): View {
  const span = clampSpan(v.span * factor);
  if (v.end === null && at >= 1) return { span, end: null };
  const [from] = rangeOf(v, now);
  const anchor = from + at * v.span;
  return settle(span, Math.round(anchor + (1 - at) * span), now);
}

/** Move by a share of the span: negative goes back in time. */
export function pan(v: View, share: number, now: number): View {
  const [, end] = rangeOf(v, now);
  return settle(v.span, Math.min(now, Math.round(end + share * v.span)), now);
}

/** A range dragged on the plot. */
export function select(from: number, to: number, now: number): View {
  const span = clampSpan(to - from);
  const end = Math.round((from + to) / 2 + span / 2);
  return settle(span, end, now);
}
