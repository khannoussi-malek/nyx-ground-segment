import { expect, test } from "vitest";
import { acqMark, limitBadge, NOMINAL_OK, vehicleSummary, type ParamState } from "./status";

const s = (acq: ParamState["acq"], level: ParamState["level"], side?: ParamState["side"]): ParamState =>
  ({ acq, level, side });

test("a nominal, fresh value shows nothing: grey when nominal (D-UI-07)", () => {
  expect(acqMark(NOMINAL_OK.acq)).toBeNull();
  expect(limitBadge(NOMINAL_OK)).toBeNull();
});

test("a live limit violation names level and side in the design's words", () => {
  expect(limitBadge(s("OK", "RED", "LOW"))).toEqual({ tone: "red", text: "RED LOW" });
  expect(limitBadge(s("OK", "YELLOW", "HIGH"))).toEqual({ tone: "yellow", text: "YELLOW HIGH" });
  expect(limitBadge(s("OK", "YELLOW"))).toEqual({ tone: "yellow", text: "YELLOW" }); // e.g. a rate check
});

test("a value that is not fresh keeps its last limit as an outline 'was' badge (D-UI-06)", () => {
  expect(limitBadge(s("STALE", "RED", "LOW"))).toEqual({ tone: "was", text: "was RED LOW" });
  expect(limitBadge(s("NO_DATA", "YELLOW", "HIGH"))).toEqual({ tone: "was", text: "was YELLOW HIGH" });
  expect(limitBadge(s("STALE", "NOMINAL"))).toBeNull();
});

test("every acquisition state other than OK has text and a glyph", () => {
  expect(acqMark("STALE")).toEqual({ text: "STALE", glyph: "stale" });
  expect(acqMark("FROZEN")).toEqual({ text: "FROZEN", glyph: "frozen" });
  expect(acqMark("NO_DATA")).toEqual({ text: "NO DATA", glyph: "nodata" });
});

test("vehicle summary: NOMINAL when everything is", () => {
  expect(vehicleSummary([NOMINAL_OK, NOMINAL_OK])).toEqual({ tone: "nominal", text: "NOMINAL" });
});

test("vehicle summary counts limits first, then acquisition, worst tone wins", () => {
  const states = [s("OK", "RED", "LOW"), s("STALE", "RED", "HIGH"), s("OK", "YELLOW"), s("FROZEN", "NOMINAL")];
  expect(vehicleSummary(states)).toEqual({ tone: "red", text: "2 RED · 1 YELLOW · 1 FROZEN · 1 STALE" });
  expect(vehicleSummary([s("OK", "YELLOW", "HIGH"), s("NO_DATA", "NOMINAL")])).toEqual({
    tone: "yellow",
    text: "1 YELLOW · 1 NO DATA",
  });
  expect(vehicleSummary([s("STALE", "NOMINAL")])).toEqual({ tone: "muted", text: "1 STALE" });
});

test("a banner state replaces the counts for that vehicle (D-UI-04/15)", () => {
  expect(vehicleSummary([s("OK", "RED", "LOW")], "LOSS OF SIGNAL")).toEqual({
    tone: "down",
    text: "LOSS OF SIGNAL",
  });
});
