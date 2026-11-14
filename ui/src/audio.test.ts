import { expect, test, vi } from "vitest";
import { armAudio, beep, tone } from "./audio";

type Osc = { type: string; frequency: { value: number }; stopAt: number };
function fakeCtx() {
  const oscs: Osc[] = [];
  const ramps: number[] = []; // times of the first linear ramp (end of the attack)
  const ctx = {
    currentTime: 10,
    destination: {},
    createOscillator() {
      const o = { type: "", frequency: { value: 0 }, stopAt: 0, connect: (n: unknown) => n, start() {}, stop(t: number) { o.stopAt = t; } };
      oscs.push(o);
      return o;
    },
    createGain() {
      let first = true;
      return {
        gain: {
          setValueAtTime() {},
          linearRampToValueAtTime(_v: number, t: number) { if (first) ramps.push(t); first = false; },
        },
        connect: (n: unknown) => n,
      };
    },
  };
  return { ctx: ctx as unknown as AudioContext, oscs, ramps };
}

test("tone and beep differ in pitch, wave, attack and length", () => {
  const b = fakeCtx();
  const t = fakeCtx();
  beep(b.ctx);
  tone(t.ctx);
  expect([b.oscs[0]!.type, b.oscs[0]!.frequency.value, b.oscs[0]!.stopAt]).toEqual(["square", 880, 10.6]);
  expect([t.oscs[0]!.type, t.oscs[0]!.frequency.value, t.oscs[0]!.stopAt]).toEqual(["sine", 440, 10.8]);
  expect(b.ramps[0]).toBeCloseTo(10.01); // 10 ms attack
  expect(t.ramps[0]).toBeCloseTo(10.2); // 200 ms attack
});

test("armAudio creates and resumes a context inside the call; no AudioContext, or a throwing one, gives null", () => {
  const resume = vi.fn(() => Promise.resolve());
  vi.stubGlobal("AudioContext", class { resume = resume; });
  expect(armAudio()).not.toBeNull();
  expect(resume).toHaveBeenCalledOnce();
  vi.stubGlobal("AudioContext", class { constructor() { throw new Error("no audio"); } });
  expect(armAudio()).toBeNull();
  vi.stubGlobal("AudioContext", undefined);
  expect(armAudio()).toBeNull();
  vi.unstubAllGlobals();
});
