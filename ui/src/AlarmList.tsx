import { useState, useSyncExternalStore } from "react";
import type { MissionDb } from "./api";
import { AckDialog } from "./AckDialog";
import { alarmText, hashText } from "./alarmText";
import { Glyph } from "./Glyph";
import type { AlarmView, Store } from "./store";
import { utcClock } from "./time";

const STATE_TEXT: Record<AlarmView["state"], string> = {
  UNACK: "UNACKNOWLEDGED",
  ACKED: "ACKNOWLEDGED",
  RTN_UNACK: "RETURNED, UNACKNOWLEDGED",
  CLEARED: "CLEARED",
};
const ALL = "ALL";
const CHANGED = "Alarm changed – review it before acknowledging";

/** Open alarms, sticky until CLEARED (a returned one stays until acknowledged), with the ACK request's own status. */
export function AlarmList({ store, mission, user }: { store: Store; mission: MissionDb | null; user: string }) {
  const alarms = useSyncExternalStore(store.subscribe, store.alarms, store.alarms);
  const link = useSyncExternalStore(store.subscribe, store.link, store.link);
  const wired = useSyncExternalStore(store.subscribe, store.hasSnapshot, store.hasSnapshot);
  const [vehicleWanted, setVehicle] = useState(ALL);
  const [severity, setSeverity] = useState(ALL);
  const [state, setState] = useState(ALL);
  const [acking, setAcking] = useState<{ alarm: AlarmView; opener: HTMLElement } | null>(null);
  const [mine, setMine] = useState<Readonly<Record<string, { by: string; requestId: string }>>>({}); // from my 202 "pending"
  const [changedIds, setChanged] = useState<ReadonlySet<string>>(new Set()); // dialogs the guard closed (P-pf42)
  // no ACK on a stale screen (P-pf42b 3): NO LIVE CONNECTION, GROUND PROCESSING DOWN (broker or owner) or no fresh hb word
  const live = (v: string) => link.connected && link.broker === "up" && link.processing[v] === true;
  const openDialog = (alarm: AlarmView, opener: HTMLElement) => {
    setChanged((s) => new Set([...s].filter((id) => id !== alarm.alarm_id)));
    setAcking({ alarm, opener });
  };
  const vehicles = [...new Set(alarms.map((a) => a.vehicle))].sort();
  const vehicle = vehicles.includes(vehicleWanted) ? vehicleWanted : ALL; // a filter whose vehicle has no alarm left resets
  const shown = [...alarms]
    .filter((a) => (vehicle === ALL || a.vehicle === vehicle) && (severity === ALL || a.severity === severity) && (state === ALL || a.state === state))
    .sort((a, b) => (a.severity === b.severity ? b.opened_at.localeCompare(a.opened_at) : a.severity === "RED" ? -1 : 1));

  function ackStatus(a: AlarmView): { text: string; pending: boolean } | null {
    const ar = a.ack_request;
    const m = mine[a.alarm_id];
    // my newer request overrides an older refused one of the server's (no double-submit window); the server's word on my
    // own request, or any pending request, wins
    const pendingBy = ar?.status === "pending" ? ar.by_user : m && ar?.request_id !== m.requestId ? m.by : undefined;
    if (pendingBy !== undefined) {
      const down = link.processing[a.vehicle] === false;
      return { text: down ? "ACK NOT APPLIED – processing down" : `ACK PENDING (by ${pendingBy})`, pending: true };
    }
    if (ar && (ar.status === "superseded" || ar.status === "rejected"))
      return { text: ar.status_reason ?? `ACK ${ar.status.toUpperCase()}`, pending: false };
    return null;
  }
  const empty = !wired ? "Waiting for the first snapshot…" : alarms.length === 0 ? "No open alarms" : "No alarms match the filters";
  const select = (label: string, value: string, set: (v: string) => void, options: readonly string[]) => (
    <label>
      {label}
      <select value={value} onChange={(e) => set(e.target.value)}>
        <option value={ALL}>All</option>
        {options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    </label>
  );
  return (
    <section className="alarms" aria-label="Alarms">
      <div className="pane-head">
        <h2>Alarms</h2>
        <div className="filters">
          {select("Vehicle", vehicle, setVehicle, vehicles)}
          {select("Severity", severity, setSeverity, ["RED", "YELLOW"])}
          {select("State", state, setState, ["UNACK", "ACKED", "RTN_UNACK"])}
        </div>
      </div>
      {shown.length === 0 ? (
        <p className="empty" role="status">{empty}</p>
      ) : (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                {["Severity", "Vehicle", "Parameter", "Alarm", "Limits and set", "Opened (UTC)", "State", "Action"].map((h) => (
                  <th key={h} scope="col">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {shown.map((a) => {
                const status = ackStatus(a);
                return (
                  <tr key={a.alarm_id} data-alarm-id={a.alarm_id} data-severity={a.severity} data-state={a.state}>
                    <th scope="row">
                      <Glyph kind={a.severity === "RED" ? "red" : "yellow"} />
                      {a.severity}
                    </th>
                    <td>{a.vehicle}</td>
                    <td>{(mission && Object.hasOwn(mission.parameters, a.param) && mission.parameters[a.param].description) || a.param}</td>
                    <td className="text">{alarmText(a, mission)}</td>
                    <td className="hash">{hashText(a)}</td>
                    <td><time dateTime={a.opened_at}>{utcClock(a.opened_at)}</time></td>
                    <td>{STATE_TEXT[a.state]}</td>
                    <td>
                      {status && <span className="ack-status">{status.text}</span>}
                      {changedIds.has(a.alarm_id) && <span className="ack-status" role="status">{CHANGED}</span>}
                      {(a.state === "UNACK" || a.state === "RTN_UNACK") && (
                        <button
                          disabled={status?.pending || !live(a.vehicle)}
                          aria-label={`Acknowledge ${a.vehicle} ${a.param}`}
                          onClick={(e) => openDialog(a, e.currentTarget)}
                        >
                          ACK
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {acking && (
        <AckDialog
          key={acking.alarm.alarm_id} // another alarm is another dialog: new request id and seen_key
          alarm={acking.alarm}
          current={alarms.find((x) => x.alarm_id === acking.alarm.alarm_id)}
          live={live(acking.alarm.vehicle)}
          onChanged={() => {
            setChanged((s) => new Set([...s, acking.alarm.alarm_id]));
            setAcking(null);
          }}
          mission={mission}
          opener={acking.opener}
          onCancel={() => setAcking(null)}
          onDone={(s, requestId) => {
            if (s === "pending") setMine((m) => ({ ...m, [acking.alarm.alarm_id]: { by: user, requestId } }));
            setAcking(null);
          }}
        />
      )}
    </section>
  );
}
