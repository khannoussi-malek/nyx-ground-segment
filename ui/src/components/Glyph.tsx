import type { Glyph as Kind } from "../tools/status";

// Shape carries the state as well as colour and text (D-UI-07): diamond RED, triangle YELLOW.
const PATHS: Record<Kind, string> = {
  red: "M8 1.5 14.5 8 8 14.5 1.5 8Z",
  yellow: "M8 2 15 14H1Z",
  stale: "M8 2a6 6 0 1 0 0 12A6 6 0 0 0 8 2Zm0 1.8v4.6l3 1.8",
  frozen: "M5 3v10M11 3v10",
  nodata: "M8 2a6 6 0 1 0 0 12A6 6 0 0 0 8 2ZM4 12 12 4",
};
const FILLED: ReadonlySet<Kind> = new Set(["red", "yellow"]);

export function Glyph({ kind }: { kind: Kind }) {
  const filled = FILLED.has(kind);
  return (
    <svg className="glyph" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <path
        d={PATHS[kind]}
        fill={filled ? "currentColor" : "none"}
        stroke={filled ? "none" : "currentColor"}
        strokeWidth="1.8"
        strokeLinecap="round"
      />
    </svg>
  );
}
