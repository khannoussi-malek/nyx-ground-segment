// Fully typed test fixtures built on the generated API types (no hand-written shapes, no casts).
import type { AlarmView, CommandView, EventView } from "./store";

export const alarmView = (over: Partial<AlarmView> = {}): AlarmView => ({
  ack_request: null,
  alarm_id: "a1",
  closed_at: null,
  last: { alert: true, by_user: "engine", check_kind: "limit", key: "k1", kind: "RAISE", level: "RED_HIGH", note: null, t: "2026-11-14T10:00:00.000000Z", value: 107 },
  level: "RED_HIGH",
  limits_sha256: "l",
  opened_at: "2026-11-14T10:00:00Z",
  param: "cabin_pressure_kpa",
  set_sha256: "s",
  severity: "RED",
  state: "UNACK",
  trigger_check: "limit",
  value: 107,
  vehicle: "NYX-TA1",
  ...over,
});

export const commandView = (over: Partial<CommandView> = {}): CommandView => ({
  args: {},
  cmd_id: "c1",
  failed_at: null,
  failure_reason: null,
  hazardous: false,
  issued_by: "fc1",
  late_ack_accepted: null,
  late_ack_at: null,
  late_verified_at: null,
  name: "PING",
  outcome: null,
  queued_at: "2026-11-14T10:00:00Z",
  reason: null,
  received_at: null,
  sent_at: null,
  state: "QUEUED",
  vehicle: "NYX-TA1",
  verified_at: null,
  ...over,
});

export const eventView = (key: string, over: Partial<EventView> = {}): EventView => ({
  details: {}, key, kind: "k", t_onboard: null, vehicle: null, ...over,
});
