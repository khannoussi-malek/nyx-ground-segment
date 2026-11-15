import { expect, test } from "vitest";
import { eventText } from "./eventText";
import { eventView } from "./fixtures";
import type { EventView } from "./store";

const ev = (kind: string, details: EventView["details"], over: Partial<EventView> = {}) =>
  eventView(`${kind}/NYX-TA1/x`, { kind, vehicle: "NYX-TA1", details, ...over });
const SUFFIX = "NYX-TA1/2026-11-14T14:03:00.000000Z/1234";
const los = eventView(`LOS/${SUFFIX}`, { kind: "LOS", vehicle: "NYX-TA1", t_onboard: "2026-11-14T14:03:12.000000Z" });
const aos = (lost: number | null) =>
  eventView(`AOS/${SUFFIX}`, { kind: "AOS", vehicle: "NYX-TA1", t_onboard: "2026-11-14T14:03:22.500000Z", details: { lost_frames: lost } });

test("LOS", () => expect(eventText(los, [los])).toBe("NYX-TA1 LOS since 14:03:12Z"));

test("AOS takes its start from the LOS with the same key suffix", () => {
  const other = eventView("LOS/NYX-TA1/other/9", { kind: "LOS", vehicle: "NYX-TA1", t_onboard: "2026-11-14T13:00:00Z" });
  expect(eventText(aos(100), [aos(100), other, los])).toBe("NYX-TA1 AOS – 100 frames lost 14:03:12Z–14:03:22Z");
  expect(eventText(aos(1), [los])).toBe("NYX-TA1 AOS – 1 frame lost 14:03:12Z–14:03:22Z");
});

test("AOS with an unknown count (G13), and with no LOS in the list", () => {
  expect(eventText(aos(null), [los])).toBe("NYX-TA1 AOS – unknown number of frames lost 14:03:12Z–14:03:22Z");
  expect(eventText(aos(100), [])).toBe("NYX-TA1 AOS – 100 frames lost until 14:03:22Z");
});

test("GAP names the count and the sequence number it follows", () => {
  expect(eventText(ev("GAP", { lost: 100, from_seq: 1235 }), [])).toBe("NYX-TA1 GAP – 100 frames lost after seq 1234");
  expect(eventText(ev("GAP", { lost: 5, from_seq: 0 }), [])).toBe("NYX-TA1 GAP – 5 frames lost at the start of the sequence");
});

test("SEQ_JUMP, SEQ_RESET, SEQ_ANOMALY", () => {
  expect(eventText(ev("SEQ_JUMP", { lost: 5000, lost_by_time: 40, from_seq: 11 }), [])).toBe("NYX-TA1 SEQ JUMP – sequence jumped by 5000 after seq 10, 40 expected by time");
  expect(eventText(ev("SEQ_RESET", {}), [])).toBe("NYX-TA1 SEQ RESET – gap of unknown size");
  expect(eventText(ev("SEQ_ANOMALY", {}), [])).toContain("SEQ ANOMALY");
});

test("FROZEN names when the values stopped changing", () => {
  expect(eventText(ev("FROZEN", { t_from: "2026-11-14T10:00:00.250000Z" }, { t_onboard: "2026-11-14T10:00:03Z" }), [])).toBe("NYX-TA1 FROZEN since 10:00:00Z");
  expect(eventText(ev("FROZEN", {}, { t_onboard: "2026-11-14T10:00:03Z" }), [])).toBe("NYX-TA1 FROZEN since 10:00:03Z");
});

test("mission db applied, with 8-digit hash prefixes", () => {
  const e = eventView("LIMITS_APPLIED/x", { kind: "LIMITS_APPLIED", details: { limits_sha256: "3f9a1c2e7700", commands_sha256: "5d07b8a1ffff", parameters_sha256: "91c4e620aaaa" } });
  expect(eventText(e, [])).toBe("mission db applied: limits 3f9a1c2e · commands 5d07b8a1 · parameters 91c4e620");
});

test("mission db rejected; the API's own refusal says commands are not updated until a restart (P-pf5)", () => {
  const rejected = eventView("LIMITS_REJECTED/x", { kind: "LIMITS_REJECTED", details: { file: "limits.yaml", error: "red_low above red_high" } });
  expect(eventText(rejected, [])).toBe("mission db REJECTED – limits.yaml: red_low above red_high");
  const api = { ...rejected, details: { ...rejected.details, by: "api" } };
  expect(eventText(api, [])).toBe("mission db REJECTED – limits.yaml: red_low above red_high – commands not updated until API restart");
  expect(eventText({ ...rejected, details: { ...rejected.details, by: "worker" } }, [])).not.toContain("API restart");
});

test("late verification, interface mismatch, poison, engine state reset", () => {
  expect(eventText(eventView("LATE_VERIFICATION/c-1", { kind: "LATE_VERIFICATION", vehicle: "NYX-TA1" }), [])).toBe("NYX-TA1 late verification of command c-1");
  expect(eventText(eventView("INTERFACE_MISMATCH/tcack/ab12cd34ef56", { kind: "INTERFACE_MISMATCH" }), [])).toBe("interface mismatch on tcack (ab12cd34ef56)");
  expect(eventText(ev("POISON", { stream: "tm:NYX-TA1", entry_id: "1-0", deliveries: 3 }), [])).toBe("NYX-TA1 POISON – entry 1-0 of tm:NYX-TA1 dead-lettered after 3 deliveries");
  expect(eventText(ev("ENGINE_STATE_RESET", { reason: "rebuilt from open alarms" }), [])).toBe("NYX-TA1 ENGINE STATE RESET – rebuilt from open alarms");
  expect(eventText(ev("ENGINE_STATE_RESET", {}), [])).toContain("reason unknown");
});

test("missing detail fields show a question mark, never a made-up number", () => {
  expect(eventText(ev("POISON", {}), [])).toBe("NYX-TA1 POISON – entry ? of ? dead-lettered after ? deliveries");
  expect(eventText(ev("LATE_VERIFICATION", {}, { key: "LATE_VERIFICATION" }), [])).toContain("command ?");
  expect(eventText(eventView("INTERFACE_MISMATCH", { kind: "INTERFACE_MISMATCH" }), [])).toBe("interface mismatch on ? (?)");
  expect(eventText(eventView("LIMITS_APPLIED/x", { kind: "LIMITS_APPLIED" }), [])).toContain("limits ?");
  expect(eventText(eventView("LIMITS_REJECTED/x", { kind: "LIMITS_REJECTED" }), [])).toBe("mission db REJECTED – ?: ?");
  expect(eventText(ev("SEQ_JUMP", {}), [])).toContain("jumped by ?");
  expect(eventText(ev("GAP", {}), [])).toContain("unknown number of frames lost");
  expect(eventText(los, [])).toContain("LOS since");
  expect(eventText({ ...los, t_onboard: null }, [])).toBe("NYX-TA1 LOS since ?");
});

test("an unknown kind falls back to the vehicle and the kind", () => {
  expect(eventText(ev("WHATEVER", {}), [])).toBe("NYX-TA1 WHATEVER");
  expect(eventText(eventView("k", { kind: "WHATEVER" }), [])).toBe("WHATEVER");
});

test("SEQ_JUMP without from_seq prints a question mark, not seq 0", () => {
  expect(eventText(ev("SEQ_JUMP", { lost: 5, lost_by_time: 4 }), [])).toBe("NYX-TA1 SEQ JUMP – sequence jumped by 5 after seq ?, 4 expected by time");
});

test("late verification and interface mismatch prefer their documented details, the key is the fallback", () => {
  const late = eventView("LATE_VERIFICATION/c-1", { kind: "LATE_VERIFICATION", vehicle: "NYX-TA1", details: { cmd_id: "c-1", name: "hold_exec", failure_reason: "NOT_VERIFIED" } });
  expect(eventText(late, [])).toBe("NYX-TA1 late verification of hold_exec c-1 (failed: NOT_VERIFIED)");
  const mismatch = eventView("INTERFACE_MISMATCH/tcack/ab12cd34ef56", { kind: "INTERFACE_MISMATCH", details: { stream: "tcack", consumer: "ingest-1", detail: "missing field cmd_id" } });
  expect(eventText(mismatch, [])).toBe("interface mismatch on tcack (consumer ingest-1): missing field cmd_id");
});

test("SEQ_JUMP and GAP from seq 0 say the start of the sequence, never seq -1; an unknown start stays unknown", () => {
  expect(eventText(ev("SEQ_JUMP", { lost: 5, lost_by_time: 4, from_seq: 0 }), [])).toBe("NYX-TA1 SEQ JUMP – sequence jumped by 5 at the start of the sequence, 4 expected by time");
  expect(eventText(ev("GAP", { lost: 5 }), [])).toBe("NYX-TA1 GAP – 5 frames lost after seq ?");
});

test("LATE_VERIFICATION prefers details.cmd_id over the key", () => {
  const late = eventView("LATE_VERIFICATION/from-key", { kind: "LATE_VERIFICATION", vehicle: "NYX-TA1", details: { cmd_id: "from-details", name: "hold_exec" } });
  expect(eventText(late, [])).toBe("NYX-TA1 late verification of hold_exec from-details");
});
