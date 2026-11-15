import { expect, test } from "vitest";
import type { CommandView, MissionDb } from "./api";
import { commandView } from "./fixtures";
import { confirmEnabled, outcomeText, rejectedText } from "./commandText";

const failed = (outcome: CommandView["outcome"], over: Partial<CommandView> = {}) =>
  commandView({ state: "FAILED", failure_reason: "NOT_VERIFIED", outcome, late_verified_at: "2026-11-14T10:00:20Z", ...over });

test.each([
  ["EXECUTED_LATE", /do NOT resend/],
  ["UNKNOWN", /never blind-resend/],
  ["NOT_SENT", /safe to retry/],
  ["RECEIVED_LATE", /wait/],
  ["NOT_EXECUTED", /safe to resend after checking why/],
] as const)("%s", (o, re) => expect(outcomeText(failed(o))).toMatch(re));

test("the parenthesis names the failure; the late time is UTC", () => {
  expect(outcomeText(failed("EXECUTED_LATE"))).toBe("FAILED (no verification in 15 s) – vehicle executed it at 10:00:20Z – do NOT resend");
  expect(outcomeText(failed("NOT_EXECUTED", { failure_reason: "NOT_RECEIVED" }))).toMatch(/^FAILED \(not received in 5 s\)/);
  expect(outcomeText(failed("UNKNOWN", { failure_reason: "ACK_INVALID" }))).toMatch(/^FAILED \(vehicle answer failed its interface check\)/);
  expect(outcomeText(failed("NOT_EXECUTED", { failure_reason: "REJECTED" }))).toMatch(/^FAILED \(rejected by the vehicle\)/);
  expect(outcomeText(failed("NOT_EXECUTED", { failure_reason: null }))).toMatch(/^FAILED \(failed\)/);
  expect(outcomeText(failed("EXECUTED_LATE", { late_verified_at: null }))).toContain("at an unknown time");
});

test("NOT_SENT is not dressed as a failure of the vehicle; no outcome, no text", () => {
  expect(outcomeText(failed("NOT_SENT", { failure_reason: "SEND_ERROR" }))).toBe("not sent – safe to retry"); // no cause: an interlock expiry is NOT_SENT too
  expect(outcomeText(failed(null))).toBeNull();
});

test("per-command timeouts from the mission db replace the defaults", () => {
  const m = { commands: { PING: { receive_timeout_s: 2, verify_timeout_s: 30 } } } as unknown as MissionDb;
  expect(outcomeText(failed("UNKNOWN"), m)).toMatch(/no verification in 30 s/);
  expect(outcomeText(failed("UNKNOWN", { failure_reason: "NOT_RECEIVED" }), m)).toMatch(/not received in 2 s/);
  expect(outcomeText(failed("UNKNOWN", { name: "constructor" }), m)).toMatch(/15 s/);
});

test("confirm needs the exact vehicle id and a 10-char reason", () => {
  for (const t of ["NYX-TA", "nyx-ta2", " NYX-TA2", "NYX-TA1", "NYX-TA2 "]) expect(confirmEnabled(t, "NYX-TA2", "reason long")).toBe(false);
  expect(confirmEnabled("NYX-TA2", "NYX-TA2", "too short")).toBe(false);
  expect(confirmEnabled("NYX-TA2", "NYX-TA2", "   short    ")).toBe(false); // padding does not count
  expect(confirmEnabled("NYX-TA2", "NYX-TA2", "venting now")).toBe(true);
});

test("a duplicate names who and how long ago; other refusals keep the server's message", () => {
  expect(rejectedText({ kind: "rejected", error: "DUPLICATE_COMMAND", message: "dup", ambiguous: false, by: "fc2", secondsAgo: 1 })).toBe("fc2 sent the same command 1 s ago");
  expect(rejectedText({ kind: "rejected", error: "DUPLICATE_COMMAND", message: "dup", ambiguous: false })).toBe("dup");
  expect(rejectedText({ kind: "rejected", error: "INVALID_ARGS", message: "duration_s: 11 is outside [1.0, 10.0]", ambiguous: false })).toBe("duration_s: 11 is outside [1.0, 10.0]");
});

test("seconds_ago is rounded; an ambiguous failure never suggests a resend (P-pf26)", () => {
  expect(rejectedText({ kind: "rejected", error: "DUPLICATE_COMMAND", message: "d", ambiguous: false, by: "fc2", secondsAgo: 1.4999 })).toBe("fc2 sent the same command 1 s ago");
  const r = rejectedText({ kind: "rejected", error: "BROKER_DOWN", message: "Redis error: try again", ambiguous: true });
  expect(r).toBe("May have been sent – check the command list before sending again");
  expect(r).not.toMatch(/again\b.*(send|retry)|safe to/i);
});
