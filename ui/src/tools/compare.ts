import type { Compare } from "../server/api";
import { utcInput } from "../server/replay";

/** Dash and colour per session, by its position in the pick: two cues, never colour alone (UI-03). No purple: that is REPLAY (D-UI-07). */
export const DASHES: number[][] = [[], [8, 4], [2, 3], [8, 3, 2, 3], [14, 4], [2, 6]];
export const COLOURS = ["#0072B2", "#D55E00", "#009E73", "#B8860B", "#56B4E9", "#444444"];

/** "T+hh:mm:ss" for seconds since the session start (hours may pass 24: a window is at most 24 h). */
export function tPlus(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  const p = (n: number) => String(n).padStart(2, "0");
  return `T+${p(Math.floor(s / 3600))}:${p(Math.floor(s / 60) % 60)}:${p(s % 60)}`;
}

export type ParamPlot = { x: number[]; lines: (number | null)[][]; los: (number | null)[][]; his: (number | null)[][] };

/** One parameter: a shared x of every session's bucket times, and mean, min and max per session (null where it has no bucket). */
export function alignParam(c: Compare, param: string): ParamPlot {
  const mine = c.sessions.map((s) => c.series.find((x) => x.session_id === s.id && x.param === param)?.buckets ?? []);
  const x = [...new Set(mine.flatMap((b) => b.map((k) => k.t_rel_s)))].sort((a, b) => a - b);
  const col = (pick: "mean" | "lo" | "hi") =>
    mine.map((b) => {
      const at = new Map(b.map((k) => [k.t_rel_s, k[pick]]));
      return x.map((t) => at.get(t) ?? null);
    });
  return { x, lines: col("mean"), los: col("lo"), his: col("hi") };
}

const TIME_HINT = "a UTC time ending in Z, like 2026-11-20T08:05Z";
export type ExportForm = { vehicle: string; from: string; to: string; session: string; params: string[]; format: "csv" | "parquet" };

/** The /api/export query: a session, or vehicle + From/To (the replay form's parser: Z required). */
export function exportQuery(f: ExportForm): { ok: true; query: string } | { ok: false; error: string } {
  const q = new URLSearchParams();
  if (f.session) q.set("session", f.session);
  else {
    const [from, to] = [utcInput(f.from), utcInput(f.to)];
    if (!f.vehicle) return { ok: false, error: "Choose a vehicle or a session" };
    if (from === null) return { ok: false, error: `From must be ${TIME_HINT}` };
    if (to === null) return { ok: false, error: `To must be ${TIME_HINT}` };
    q.set("vehicle", f.vehicle);
    q.set("from", from);
    q.set("to", to);
  }
  if (f.params.length > 0) q.set("params", f.params.join(","));
  q.set("format", f.format);
  return { ok: true, query: q.toString() };
}
