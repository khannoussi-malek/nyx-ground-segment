export type LatencySample = { vehicle: string; seq: number; age_ms: number | null; paint_ms: number };
declare global {
  interface Window {
    __nyxLatency?: LatencySample[];
  }
}

export const MAX_SAMPLES = 100_000;

/** Screen-latency probe (D-UI-14, page half of UI-01): `receipt` stamps the SSE receipt, `painted` (called after React
 *  committed the frame) waits for the second requestAnimationFrame, a heuristic for paint, and records
 *  `paint_ms = paint − receipt`. A frame displaced before it was ever painted still gets its sample, at the paint that
 *  showed something newer. Samples go to `window.__nyxLatency` for Playwright. */
export function createProbe(now: () => number = () => performance.now(), raf: (cb: () => void) => unknown = (cb) => requestAnimationFrame(cb)) {
  const pending = new Map<string, { vehicle: string; seq: number; at: number; age: number | null }>();
  const sink = (): LatencySample[] => (window.__nyxLatency ??= []);
  return {
    receipt(vehicle: string, seq: number, ageMs: number | null): void {
      pending.set(`${vehicle}/${seq}`, { vehicle, seq, at: now(), age: ageMs });
      // a hidden tab paints nothing: the table must not grow without bound
      if (pending.size > MAX_SAMPLES) pending.delete(pending.keys().next().value as string);
    },
    painted(vehicle: string, seq: number): void {
      raf(() =>
        raf(() => {
          const t = now();
          const out = sink();
          for (const [k, p] of pending) {
            if (p.vehicle !== vehicle || p.seq > seq) continue;
            pending.delete(k);
            out.push({ vehicle, seq: p.seq, age_ms: p.age, paint_ms: t - p.at });
          }
          if (out.length > MAX_SAMPLES) out.splice(0, out.length - MAX_SAMPLES);
        }),
      );
    },
  };
}

export type Probe = ReturnType<typeof createProbe>;
/** The page's own probe. */
export const probe: Probe = createProbe();
