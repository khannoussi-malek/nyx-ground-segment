// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, test } from "vitest";
import { EventStrip } from "./EventStrip";
import { eventView } from "./fixtures";
import { createStore, ingest } from "./store";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root;
let host: HTMLElement;
beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

const lines = () => [...host.querySelectorAll("li")].map((li) => li.textContent);
const snapshot = (events: unknown[]) => ({ kind: "snapshot", vehicles: {}, alarms: [], commands: [], events });

test("before the first snapshot the strip does not claim there are no events", () => {
  act(() => root.render(<EventStrip store={createStore()} />));
  expect(host.textContent).toContain("Waiting for the first snapshot");
  const store = createStore();
  ingest(store, "snapshot", snapshot([]));
  act(() => root.render(<EventStrip store={store} />));
  expect(host.textContent).toContain("No events");
});

test("newest first, UTC times, operator text; a live link event goes on top", () => {
  const store = createStore();
  const los = eventView("LOS/NYX-TA1/e/7", { kind: "LOS", vehicle: "NYX-TA1", t_onboard: "2026-11-14T14:03:12.000000Z" });
  ingest(store, "snapshot", snapshot([los]));
  act(() => root.render(<EventStrip store={store} />));
  expect(lines()).toEqual(["14:03:12Z NYX-TA1 LOS since 14:03:12Z"]);
  const aos = eventView("AOS/NYX-TA1/e/7", { kind: "AOS", vehicle: "NYX-TA1", t_onboard: "2026-11-14T14:03:22Z", details: { lost_frames: 100 } });
  act(() => ingest(store, "link", { kind: "link", event: aos }));
  expect(lines()).toEqual(["14:03:22Z NYX-TA1 AOS – 100 frames lost 14:03:12Z–14:03:22Z", "14:03:12Z NYX-TA1 LOS since 14:03:12Z"]);
  expect(host.querySelector("li")!.getAttribute("data-kind")).toBe("AOS");
});

test("a repeated event is shown once; an event without a time has no clock", () => {
  const store = createStore();
  ingest(store, "snapshot", snapshot([]));
  act(() => root.render(<EventStrip store={store} />));
  const rejected = eventView("LIMITS_REJECTED/x", { kind: "LIMITS_REJECTED", details: { file: "limits.yaml", error: "bad", by: "api" } });
  act(() => ingest(store, "link", { kind: "link", event: rejected }));
  act(() => ingest(store, "link", { kind: "link", event: rejected }));
  expect(lines()).toEqual(["mission db REJECTED – limits.yaml: bad – commands not updated until API restart"]);
  expect(host.querySelector("time")).toBeNull();
});

test("a vehicle tag tells apart two events whose text names no vehicle", () => {
  const store = createStore();
  const applied = (v: string) => eventView(`LIMITS_APPLIED/${v}`, { kind: "LIMITS_APPLIED", vehicle: v, t_onboard: "2026-11-14T10:00:00Z", details: { limits_sha256: "aaaaaaaaaa" } });
  ingest(store, "snapshot", snapshot([applied("NYX-TA2"), applied("NYX-TA1")]));
  act(() => root.render(<EventStrip store={store} />));
  expect([...host.querySelectorAll(".tag")].map((t) => t.textContent)).toEqual(["NYX-TA2", "NYX-TA1"]);
  expect(host.querySelectorAll(".tag")).toHaveLength(2); // no tag where the text already starts with the vehicle
});

test("the event strip filters by vehicle and text; a filter that hides every line says so", () => {
  const store = createStore();
  const los = eventView("LOS/NYX-TA1/e/1", { kind: "LOS", vehicle: "NYX-TA1", t_onboard: "2026-11-14T14:03:12Z" });
  const aos = eventView("AOS/NYX-TA2/e/1", { kind: "AOS", vehicle: "NYX-TA2", t_onboard: "2026-11-14T14:03:22Z", details: { lost_frames: 3 } });
  ingest(store, "snapshot", snapshot([aos, los]));
  act(() => root.render(<EventStrip store={store} />));
  const field = (label: string) =>
    [...host.querySelectorAll('[role="search"] label')].find((l) => l.textContent?.startsWith(label))!.querySelector("select, input") as HTMLSelectElement & HTMLInputElement;
  const set = (el: HTMLSelectElement & HTMLInputElement, value: string) =>
    act(() => {
      const proto = el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(el, value);
      el.dispatchEvent(new Event(el instanceof HTMLSelectElement ? "change" : "input", { bubbles: true }));
    });
  const kinds = () => [...host.querySelectorAll<HTMLElement>("li")].map((li) => li.dataset.kind);
  expect(kinds()).toEqual(["AOS", "LOS"]);
  set(field("Show"), "NYX-TA1");
  expect(kinds()).toEqual(["LOS"]);
  set(field("Show"), "");
  set(field("Search"), "frames lost");
  expect(kinds()).toEqual(["AOS"]);
  set(field("Search"), "zzz");
  expect(kinds()).toEqual([]);
  expect(host.textContent).toContain("No events match the filter");
});
