import { expect, test } from "vitest";
import { DEFAULT_LAYOUT, moveBefore, moveBy, parseLayout, PANES, setHeight, toggleWide } from "./layout";

test("the default shows every pane once: alarms, vehicles and chart first and full width", () => {
  expect([...DEFAULT_LAYOUT.order].sort()).toEqual([...PANES].sort());
  expect(DEFAULT_LAYOUT.order.slice(0, 3)).toEqual(["alarms", "live", "chart"]);
  expect([DEFAULT_LAYOUT.wide.alarms, DEFAULT_LAYOUT.wide.live, DEFAULT_LAYOUT.wide.chart]).toEqual([true, true, true]);
});

test("moveBy moves one step and stops at both ends; the input is not changed", () => {
  const l = DEFAULT_LAYOUT;
  const before = [...l.order];
  expect(moveBy(l, "live", -1).order.slice(0, 2)).toEqual(["live", "alarms"]);
  expect(moveBy(l, "alarms", -1)).toBe(l);
  expect(moveBy(l, l.order.at(-1)!, 1)).toBe(l);
  expect(l.order).toEqual(before);
});

test("moveBefore puts the dragged pane where the target was, from either side", () => {
  const l = { ...DEFAULT_LAYOUT, order: ["alarms", "live", "chart", "commands", "sessions", "events"] as const };
  expect(moveBefore(l, "events", "live").order).toEqual(["alarms", "events", "live", "chart", "commands", "sessions"]);
  expect(moveBefore(l, "alarms", "commands").order).toEqual(["live", "chart", "commands", "alarms", "sessions", "events"]);
  expect(moveBefore(l, "chart", "chart")).toBe(l);
});

test("toggleWide flips one pane; setHeight keeps a whole pixel height and forgets a reset one", () => {
  expect(toggleWide(DEFAULT_LAYOUT, "chart").wide.chart).toBe(!DEFAULT_LAYOUT.wide.chart);
  expect(setHeight(DEFAULT_LAYOUT, "sessions", 412.6).heights.sessions).toBe(413);
  expect(setHeight(setHeight(DEFAULT_LAYOUT, "sessions", 400), "sessions", null).heights).not.toHaveProperty("sessions");
});

test("a stored layout is taken only as far as it is valid: unknown panes dropped, missing ones appended", () => {
  expect(parseLayout(null)).toEqual(DEFAULT_LAYOUT);
  expect(parseLayout("not json")).toEqual(DEFAULT_LAYOUT);
  const l = parseLayout(JSON.stringify({ order: ["events", "nope", "alarms", "events"], wide: { events: true, nope: true }, heights: { alarms: 300, chart: -5, live: "x" } }));
  expect(l.order.slice(0, 2)).toEqual(["events", "alarms"]);
  expect([...l.order].sort()).toEqual([...PANES].sort());
  expect(l.wide.events).toBe(true);
  expect(l.wide).not.toHaveProperty("nope");
  expect(l.heights).toEqual({ alarms: 300 });
});
