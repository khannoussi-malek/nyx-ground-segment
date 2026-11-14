/** Value and vehicle state words, exactly the design's vocabulary (D-UI-06, D-UI-04/15).
 *  Day 3 feeds the states from SSE; nothing here derives a state from limits. */
export type Acq = "OK" | "STALE" | "FROZEN" | "NO_DATA";
export type Level = "NOMINAL" | "YELLOW" | "RED";
export type Side = "LOW" | "HIGH";
export type ParamState = { acq: Acq; level: Level; side?: Side };
export type Banner =
  | "NO LIVE CONNECTION"
  | "GROUND PROCESSING DOWN"
  | "GROUND PROCESSING DOWN – BROKER"
  | "LOSS OF SIGNAL";
export type Glyph = "red" | "yellow" | "stale" | "frozen" | "nodata";

export const NOMINAL_OK: ParamState = { acq: "OK", level: "NOMINAL" };

const ACQ: Record<Exclude<Acq, "OK">, { text: string; glyph: Glyph }> = {
  STALE: { text: "STALE", glyph: "stale" },
  FROZEN: { text: "FROZEN", glyph: "frozen" },
  NO_DATA: { text: "NO DATA", glyph: "nodata" },
};

export const acqMark = (acq: Acq) => (acq === "OK" ? null : ACQ[acq]);

/** A live violation is filled; a value that is not fresh keeps its last limit as "was …". */
export function limitBadge(s: ParamState): { tone: "red" | "yellow" | "was"; text: string } | null {
  if (s.level === "NOMINAL") return null;
  const words = s.side ? `${s.level} ${s.side}` : s.level;
  if (s.acq !== "OK") return { tone: "was", text: `was ${words}` };
  return { tone: s.level === "RED" ? "red" : "yellow", text: words };
}

export type Summary = { tone: "red" | "yellow" | "muted" | "nominal" | "down"; text: string };

export function vehicleSummary(states: readonly ParamState[], banner?: Banner): Summary {
  if (banner) return { tone: "down", text: banner };
  const n = (f: (s: ParamState) => boolean) => states.filter(f).length;
  const counts: [number, string][] = [
    [n((s) => s.level === "RED"), "RED"],
    [n((s) => s.level === "YELLOW"), "YELLOW"],
    [n((s) => s.acq === "FROZEN"), "FROZEN"],
    [n((s) => s.acq === "STALE"), "STALE"],
    [n((s) => s.acq === "NO_DATA"), "NO DATA"],
  ];
  const parts = counts.filter(([c]) => c > 0).map(([c, w]) => `${c} ${w}`);
  if (parts.length === 0) return { tone: "nominal", text: "NOMINAL" };
  const tone = counts[0][0] ? "red" : counts[1][0] ? "yellow" : "muted";
  return { tone, text: parts.join(" · ") };
}
