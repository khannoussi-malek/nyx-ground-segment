import { beforeEach, expect, test, vi } from "vitest";
import { connectEvents } from "./sse";

type Handler = (e: { data: string }) => void;
class FakeEventSource {
  static all: FakeEventSource[] = [];
  listeners = new Map<string, Handler>();
  closed = false;
  constructor(readonly url: string) {
    FakeEventSource.all.push(this);
  }
  addEventListener(kind: string, fn: Handler) {
    this.listeners.set(kind, fn);
  }
  close() {
    this.closed = true;
  }
  emit(kind: string, data: string) {
    this.listeners.get(kind)?.({ data });
  }
}
let timers: { fn: () => void; ms: number; id: number }[] = [];
const cleared: number[] = [];
const deps = {
  EventSource: FakeEventSource as unknown as typeof EventSource,
  setTimeout: ((fn: () => void, ms: number) => {
    timers.push({ fn, ms, id: timers.length + 1 });
    return timers.length;
  }) as unknown as typeof setTimeout,
  clearTimeout: ((id: number) => cleared.push(id)) as unknown as typeof clearTimeout,
};

beforeEach(() => {
  FakeEventSource.all = [];
  timers = [];
  cleared.length = 0;
});

test("dispatches snapshot and tm as parsed JSON, ignores hello and hb", () => {
  const got: [string, unknown][] = [];
  connectEvents("/api/events", (k, d) => got.push([k, d]), deps);
  const es = FakeEventSource.all[0]!;
  expect(es.url).toBe("/api/events");
  es.emit("snapshot", '{"kind":"snapshot","vehicles":{}}');
  es.emit("tm", '{"kind":"tm","seq":1}');
  expect([...es.listeners.keys()].sort()).toEqual(["error", "snapshot", "tm"]);
  expect(got).toEqual([["snapshot", { kind: "snapshot", vehicles: {} }], ["tm", { kind: "tm", seq: 1 }]]);
});

test("malformed JSON is logged and skipped, not thrown", () => {
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  const onEvent = vi.fn();
  connectEvents("/u", onEvent, deps);
  FakeEventSource.all[0]!.emit("tm", "{not json");
  expect(onEvent).not.toHaveBeenCalled();
  expect(warn).toHaveBeenCalledOnce();
  warn.mockRestore();
});

test("error closes the source and reopens once after 1000 ms", () => {
  connectEvents("/u", () => {}, deps);
  const first = FakeEventSource.all[0]!;
  first.listeners.get("error")!({ data: "" });
  first.listeners.get("error")!({ data: "" }); // a second error while one reopen is pending
  expect(first.closed).toBe(true);
  expect(timers.map((t) => t.ms)).toEqual([1000]);
  expect(FakeEventSource.all).toHaveLength(1);
  timers[0]!.fn();
  expect(FakeEventSource.all).toHaveLength(2);
  expect(FakeEventSource.all[1]!.url).toBe("/u");
  FakeEventSource.all[1]!.listeners.get("error")!({ data: "" }); // pending flag was reset
  expect(timers).toHaveLength(2);
});

test("the returned function closes the source and cancels a pending reopen", () => {
  const stop = connectEvents("/u", () => {}, deps);
  FakeEventSource.all[0]!.listeners.get("error")!({ data: "" });
  stop();
  expect(cleared).toEqual([1]);
  timers[0]!.fn(); // even if the timer fires anyway
  expect(FakeEventSource.all).toHaveLength(1);
  const stop2 = connectEvents("/u", () => {}, deps);
  stop2();
  expect(FakeEventSource.all[1]!.closed).toBe(true);
});
