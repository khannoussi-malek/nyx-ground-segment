import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { DEFAULT_LAYOUT, moveBefore, moveBy, parseLayout, setHeight, toggleWide, type Layout, type PaneId } from "./layout";

export const LAYOUT_KEY = "nyx.layout.v1";

/** The operator's arrangement, kept in this browser; storage that is blocked or full only loses the arrangement. */
export function useLayout() {
  const [layout, setLayout] = useState<Layout>(() => {
    try {
      return parseLayout(localStorage.getItem(LAYOUT_KEY));
    } catch {
      return DEFAULT_LAYOUT;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout));
    } catch {
      // ponytail: private mode or full storage; the console works with the arrangement for this page only
    }
  }, [layout]);
  const update = useCallback((f: (l: Layout) => Layout) => setLayout(f), []);
  const reset = useCallback(() => setLayout(DEFAULT_LAYOUT), []);
  return { layout, update, reset };
}

const Icon = ({ d }: { d: string }) => (
  <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
    <path d={d} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);
const EARLIER = "M10 3 5 8l5 5";
const LATER = "M6 3l5 5-5 5";
const WIDTH = "M1.5 8h13M4.5 5l-3 3 3 3M11.5 5l3 3-3 3";
const GRIP = "M6 3.5h.01M10 3.5h.01M6 8h.01M10 8h.01M6 12.5h.01M10 12.5h.01";

export type PaneDef = { title: string; node: ReactNode };

/** The console panes in the operator's order, each under its title. In edit mode (the top bar's Edit layout) each bar
 *  also has a grip to drag the pane onto another, buttons to move it with the keyboard and a full / half width
 *  toggle, and the pane's resize handle (bottom-right) sets its height. Outside edit mode nothing moves by accident. */
export function Board({ panes, layout, update, editing }: {
  panes: Readonly<Record<PaneId, PaneDef>>;
  layout: Layout;
  update: (f: (l: Layout) => Layout) => void;
  editing: boolean;
}) {
  const board = useRef<HTMLDivElement>(null);
  const dragged = useRef<PaneId | null>(null);
  const [over, setOver] = useState<PaneId | null>(null);

  // The native handle writes an inline height; on release it becomes the pane's min-height instead, so every pane of
  // a grid row still stretches to the tallest one (a fixed height would not follow its neighbour).
  useEffect(() => {
    const release = () => {
      for (const el of board.current?.querySelectorAll<HTMLElement>(".pane") ?? []) {
        if (!el.style.height) continue;
        const h = el.getBoundingClientRect().height;
        el.style.height = "";
        update((l) => setHeight(l, el.dataset.pane as PaneId, h));
      }
    };
    document.addEventListener("pointerup", release);
    document.addEventListener("mouseup", release); // the resize handle sends no pointerup in some engines
    return () => {
      document.removeEventListener("pointerup", release);
      document.removeEventListener("mouseup", release);
    };
  }, [update]);

  const last = layout.order.length - 1;
  return (
    <div ref={board} className="board" data-editing={editing || undefined}>
      {layout.order.map((id, i) => {
        const { title, node } = panes[id];
        const height = layout.heights[id];
        return (
          <div
            key={id}
            className="pane"
            data-pane={id}
            data-wide={layout.wide[id] || undefined}
            data-drop={over === id || undefined}
            style={height ? { minHeight: height } : undefined}
            onDragOver={(e) => {
              if (!dragged.current || dragged.current === id) return;
              e.preventDefault(); // accept the drop
              setOver(id);
            }}
            onDragLeave={(e) => {
              if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOver(null);
            }}
            onDrop={(e) => {
              e.preventDefault();
              const from = dragged.current;
              dragged.current = null;
              setOver(null);
              if (from) update((l) => moveBefore(l, from, id));
            }}
          >
            <div className="pane-bar">
              {editing && (
                <span
                  className="grip"
                  draggable
                  title={`Drag to move ${title}`}
                  onDragStart={(e) => {
                    dragged.current = id;
                    e.dataTransfer.effectAllowed = "move";
                    e.dataTransfer.setData("text/plain", id); // some engines start no drag without data
                    const pane = e.currentTarget.closest(".pane");
                    if (pane) e.dataTransfer.setDragImage(pane, 16, 12);
                  }}
                  onDragEnd={() => {
                    dragged.current = null;
                    setOver(null);
                  }}
                >
                  <Icon d={GRIP} />
                </span>
              )}
              <span className="pane-title" aria-hidden="true">{title}</span>
              {editing && (
                <>
                  <button type="button" aria-label={`Move ${title} earlier`} title="Move earlier" disabled={i === 0} onClick={() => update((l) => moveBy(l, id, -1))}>
                    <Icon d={EARLIER} />
                  </button>
                  <button type="button" aria-label={`Move ${title} later`} title="Move later" disabled={i === last} onClick={() => update((l) => moveBy(l, id, 1))}>
                    <Icon d={LATER} />
                  </button>
                  <button type="button" aria-label={`${title} full width`} aria-pressed={!!layout.wide[id]} title="Full width" onClick={() => update((l) => toggleWide(l, id))}>
                    <Icon d={WIDTH} />
                  </button>
                </>
              )}
            </div>
            {node}
          </div>
        );
      })}
    </div>
  );
}
