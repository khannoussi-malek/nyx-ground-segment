type LatencySample = { vehicle: string; seq: number; age_ms: number | null; paint_ms: number };
declare global {
  interface Window {
    __nyxLatency?: LatencySample[];
  }
}

export const MAX_SAMPLES = 100_000;

/** Screen-latency probe (D-UI-14, page half of UI-01): `receipt` stamps the SSE receipt, `painted` (called after React
 *  committed the frame) waits for the second requestAnimationFrame, a heuristic for paint, and records
 *  `paint_ms = paint − receipt`. A frame displaced before it was ever painted still gets its sample, at the paint that
 *  showed something newer. Samples go to `window.__nyxLatency` for Playwright.
 *  Opt-in (P-pf28): the probe is active only if `window.__nyxLatency` was already an array when it was created (the perf
 *  run sets it with addInitScript); otherwise it does nothing: no animation frames, no table. While the tab is hidden
 *  nothing is sampled, and a seq lower than the pending ones (SEQ_RESET) starts that vehicle's table afresh. */
export function createProbe(
  now: () => number = () => performance.now(),
  raf: (cb: () => void) => unknown = (cb) => requestAnimationFrame(cb),
  hidden: () => boolean = () => typeof document !== "undefined" && document.hidden,
  active: boolean = typeof window !== "undefined" && Array.isArray(window.__nyxLatency),
) {
  const pending = new Map<string, { vehicle: string; seq: number; at: number; age: number | null }>();
  const newest = new Map<string, number>(); // highest seq seen per vehicle, so a receipt costs O(1)
  const forget = (vehicle: string) => {
    for (const [k, p] of pending) if (p.vehicle === vehicle) pending.delete(k);
  };
  return {
    receipt(vehicle: string, seq: number, ageMs: number | null): void {
      if (!active || hidden()) return;
      if (seq < (newest.get(vehicle) ?? -Infinity)) forget(vehicle); // the sequence restarted: the old numbers mean nothing now
      newest.set(vehicle, seq);
      pending.set(`${vehicle}/${seq}`, { vehicle, seq, at: now(), age: ageMs });
      // frames that never paint must not grow the table without bound
      if (pending.size > MAX_SAMPLES) pending.delete(pending.keys().next().value as string);
    },
    painted(vehicle: string, seq: number): void {
      if (!active) return;
      raf(() =>
        raf(() => {
          const t = now();
          const out = (window.__nyxLatency ??= []);
          const gone = hidden();
          for (const [k, p] of pending) {
            if (p.vehicle !== vehicle || p.seq > seq) continue;
            pending.delete(k);
            if (!gone) out.push({ vehicle, seq: p.seq, age_ms: p.age, paint_ms: t - p.at });
          }
          if (out.length > MAX_SAMPLES) out.splice(0, out.length - MAX_SAMPLES);
        }),
      );
    },
    /** for tests: how many frames wait for a paint */
    get waiting(): number {
      return pending.size;
    },
  };
}

export type Probe = ReturnType<typeof createProbe>;
