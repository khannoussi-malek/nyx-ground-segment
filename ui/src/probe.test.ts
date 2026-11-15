import { beforeEach, expect, test } from "vitest";
import { createProbe, MAX_SAMPLES } from "./probe";

// window only needs to be an object here
beforeEach(() => {
  (globalThis as { window?: unknown }).window = {};
});

function rig() {
  let t = 0;
  const frames: (() => void)[] = [];
  const probe = createProbe(() => t, (cb) => void frames.push(cb));
  return {
    probe,
    advance: (ms: number) => void (t += ms),
    /** run the callbacks queued so far, the way a browser runs one animation frame */
    frame: () => frames.splice(0).forEach((cb) => cb()),
    samples: () => (globalThis as unknown as { window: { __nyxLatency?: unknown[] } }).window.__nyxLatency ?? [],
  };
}

test("one sample per frame: paint_ms runs from the receipt to the second animation frame", () => {
  const r = rig();
  r.probe.receipt("NYX-TA1", 7, 12);
  r.advance(5);
  r.probe.painted("NYX-TA1", 7);
  r.advance(16);
  r.frame(); // first rAF: nothing yet
  expect(r.samples()).toEqual([]);
  r.advance(17);
  r.frame(); // second rAF
  expect(r.samples()).toEqual([{ vehicle: "NYX-TA1", seq: 7, age_ms: 12, paint_ms: 38 }]);
});

test("a frame superseded before it was painted still gets its sample, at the paint that showed the newer one", () => {
  const r = rig();
  r.probe.receipt("NYX-TA1", 1, 10);
  r.advance(4);
  r.probe.receipt("NYX-TA1", 2, 11); // arrives before frame 1 ever committed
  r.advance(4);
  r.probe.painted("NYX-TA1", 2);
  r.frame();
  r.advance(16);
  r.frame();
  expect(r.samples()).toEqual([
    { vehicle: "NYX-TA1", seq: 1, age_ms: 10, paint_ms: 24 },
    { vehicle: "NYX-TA1", seq: 2, age_ms: 11, paint_ms: 20 },
  ]);
});

test("a paint of one vehicle, or of an older frame, does not take another's sample", () => {
  const r = rig();
  r.probe.receipt("NYX-TA1", 5, null);
  r.probe.receipt("NYX-TA2", 5, 1);
  r.probe.receipt("NYX-TA1", 6, 1);
  r.probe.painted("NYX-TA1", 5);
  r.frame();
  r.frame();
  expect(r.samples()).toEqual([{ vehicle: "NYX-TA1", seq: 5, age_ms: null, paint_ms: 0 }]);
  r.probe.painted("NYX-TA2", 5);
  r.frame();
  r.frame();
  expect(r.samples()).toHaveLength(2);
});

test("a frame is sampled once", () => {
  const r = rig();
  r.probe.receipt("V", 1, 1);
  r.probe.painted("V", 1);
  r.probe.painted("V", 1);
  r.frame();
  r.frame();
  expect(r.samples()).toHaveLength(1);
});

test("the sample list is bounded", () => {
  const r = rig();
  (globalThis as unknown as { window: { __nyxLatency: unknown[] } }).window.__nyxLatency = Array.from({ length: MAX_SAMPLES }, (_, i) => ({ seq: -i }));
  r.probe.receipt("V", 1, 1);
  r.probe.painted("V", 1);
  r.frame();
  r.frame();
  expect(r.samples()).toHaveLength(MAX_SAMPLES);
  expect((r.samples().at(-1) as { seq: number }).seq).toBe(1); // the newest is kept, the oldest dropped
  expect((r.samples()[0] as { seq: number }).seq).toBe(-1);
});

test("frames that never paint (a hidden tab) cannot grow the pending table without bound", () => {
  const r = rig();
  for (let i = 0; i < MAX_SAMPLES + 5; i++) r.probe.receipt("V", i, 1);
  r.probe.painted("V", MAX_SAMPLES + 5);
  r.frame();
  r.frame();
  expect(r.samples()).toHaveLength(MAX_SAMPLES); // the 5 oldest were dropped
});
