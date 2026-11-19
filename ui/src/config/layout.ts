/** The operator's own arrangement of the console panes: order, width and height. Pure; Board.tsx keeps it in
 *  localStorage (a per-browser convenience: losing it only brings back the default). */
export const PANES = ["alarms", "live", "chart", "commands", "sessions", "events"] as const;
export type PaneId = (typeof PANES)[number];
export type Layout = {
  order: readonly PaneId[];
  wide: Readonly<Partial<Record<PaneId, boolean>>>; // full width; otherwise half
  heights: Readonly<Partial<Record<PaneId, number>>>; // px, set by the pane's resize handle
};

export const DEFAULT_LAYOUT: Layout = {
  order: ["alarms", "live", "chart", "commands", "sessions", "events"],
  wide: { alarms: true, live: true, chart: true, events: true },
  heights: {},
};

const isPane = (x: unknown): x is PaneId => (PANES as readonly unknown[]).includes(x);

export function moveBy(l: Layout, id: PaneId, delta: -1 | 1): Layout {
  const i = l.order.indexOf(id);
  const j = i + delta;
  if (i < 0 || j < 0 || j >= l.order.length) return l;
  const order = [...l.order];
  [order[i], order[j]] = [order[j]!, order[i]!];
  return { ...l, order };
}

/** Drag and drop: the dragged pane takes the target's place, the target and those after it move one on. */
export function moveBefore(l: Layout, id: PaneId, target: PaneId): Layout {
  if (id === target || !l.order.includes(id) || !l.order.includes(target)) return l;
  const from = l.order.indexOf(id);
  const rest = l.order.filter((p) => p !== id);
  const at = rest.indexOf(target) + (from < l.order.indexOf(target) ? 1 : 0);
  return { ...l, order: [...rest.slice(0, at), id, ...rest.slice(at)] };
}

export const toggleWide = (l: Layout, id: PaneId): Layout => ({ ...l, wide: { ...l.wide, [id]: !l.wide[id] } });

export function setHeight(l: Layout, id: PaneId, px: number | null): Layout {
  const { [id]: _, ...rest } = l.heights;
  return { ...l, heights: px === null ? rest : { ...rest, [id]: Math.round(px) } };
}

/** Whatever localStorage held: invalid parts fall back, so a bad entry can never hide a pane. */
export function parseLayout(raw: string | null): Layout {
  let x: unknown;
  try {
    x = raw === null ? null : JSON.parse(raw);
  } catch {
    return DEFAULT_LAYOUT;
  }
  if (typeof x !== "object" || x === null) return DEFAULT_LAYOUT;
  const o = x as { order?: unknown; wide?: unknown; heights?: unknown };
  const listed = Array.isArray(o.order) ? [...new Set(o.order.filter(isPane))] : [];
  const order = [...listed, ...DEFAULT_LAYOUT.order.filter((p) => !listed.includes(p))];
  const pick = <T,>(src: unknown, ok: (v: unknown) => v is T): Partial<Record<PaneId, T>> =>
    typeof src === "object" && src !== null
      ? Object.fromEntries(Object.entries(src).filter(([k, v]) => isPane(k) && ok(v)))
      : {};
  const wide = Array.isArray(o.order) ? pick(o.wide, (v): v is boolean => typeof v === "boolean") : DEFAULT_LAYOUT.wide;
  const heights = pick(o.heights, (v): v is number => typeof v === "number" && Number.isFinite(v) && v > 0);
  return { order, wide, heights };
}
