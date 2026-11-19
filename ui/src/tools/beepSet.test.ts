import { expect, test } from "vitest";
import { beepsFor } from "./audio";
import { createKeySet } from "../state/keySet";
import type { AlarmView } from "../state/store";

const memoryStorage = () => {
  const m = new Map<string, string>();
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v) };
};
const red = (key: string, alert = true): AlarmView =>
  ({ alarm_id: key, state: "UNACK", level: "RED_HIGH", last: { key, alert, kind: "RAISE" } }) as unknown as AlarmView;

test("beeps once per alert key, also after a reload", () => {
  const store = memoryStorage();
  const s1 = createKeySet("beeps", store);
  expect(beepsFor([red("k1")], s1)).toEqual(["k1"]);
  expect(beepsFor([red("k1")], s1)).toEqual([]);
  expect(beepsFor([red("k1")], createKeySet("beeps", store))).toEqual([]); // reload
});

test("a resync snapshot with an unbeeped RED alert beeps once (D-UI-18)", () => {
  const s = createKeySet("beeps", memoryStorage());
  beepsFor([red("k1")], s);
  expect(beepsFor([red("k1"), red("k2")], s)).toEqual(["k2"]);
  expect(beepsFor([red("k1"), red("k2")], s)).toEqual([]);
});

test("yellow, RTN and ACK transitions (alert false) never beep", () => {
  expect(beepsFor([red("y1", false), red("r1", false)], createKeySet("beeps", memoryStorage()))).toEqual([]);
});

test("the key set survives broken storage: memory only, no throw", () => {
  const broken = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("full"); } };
  const s = createKeySet("beeps", broken);
  expect(s.add("a")).toBe(true);
  expect(s.add("a")).toBe(false);
  s.delete("a");
  expect(s.has("a")).toBe(false);
  expect(createKeySet("x", { getItem: () => "{oops", setItem() {} }).has("a")).toBe(false);
  expect(createKeySet("x", null).add("a")).toBe(true);
});
