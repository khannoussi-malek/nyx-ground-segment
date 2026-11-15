import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { listCommands, sendCommand, type CommandRequest, type MissionDb } from "./api";
import { AMBIGUOUS_TEXT, rejectedText } from "./commandText";
import { ConfirmDialog } from "./ConfirmDialog";
import type { Store } from "./store";
import { Timeline } from "./Timeline";

type Spec = MissionDb["commands"][string];
type Pending = { req: CommandRequest; hazard: string; token: string; expiresInS: number; opener: HTMLElement | null };
const SHOWN = 20;

function ArgInput({ name, spec, value, onChange }: {
  name: string;
  spec: Spec["args"][string];
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <label>
      {name}
      {spec.type === "enum" ? (
        <select value={value} onChange={(e) => onChange(e.target.value)}>
          <option value="">—</option>
          {spec.values.map((v) => <option key={v} value={v}>{v}</option>)}
        </select>
      ) : (
        <>
          <input type="number" min={spec.min} max={spec.max} step={spec.type === "int" ? 1 : "any"} value={value} onChange={(e) => onChange(e.target.value)} />
          {spec.unit && <span className="unit">{spec.unit}</span>}
        </>
      )}
    </label>
  );
}

/** Send a command and watch it: typed arguments from the mission db, hazardous ones through ConfirmDialog. */
export function CommandPanel({ store, mission }: { store: Store; mission: MissionDb | null }) {
  const vehicles = useSyncExternalStore(store.subscribe, store.vehicles, store.vehicles);
  const commands = useSyncExternalStore(store.subscribe, store.commands, store.commands);
  const [vehicle, setVehicle] = useState("");
  const [name, setName] = useState("");
  const [values, setValues] = useState<Readonly<Record<string, string>>>({});
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<Pending | null>(null);
  const sendButton = useRef<HTMLButtonElement>(null);
  const inFlight = useRef(false); // two clicks in one task must not send twice
  // the target is always an explicit choice: a vehicle that appears later never becomes it, one that vanishes clears it
  const target = vehicles.includes(vehicle) ? vehicle : "";
  // a vehicle that vanishes clears the choice for good: it cannot come back as the target on its own
  useEffect(() => {
    if (vehicle && !vehicles.includes(vehicle)) setVehicle("");
  }, [vehicle, vehicles]);
  const spec: Spec | undefined = mission && name && Object.hasOwn(mission.commands, name) ? mission.commands[name] : undefined;

  const build = (): CommandRequest | null => {
    if (!spec || !target) return null;
    const args: Record<string, string | number> = {};
    for (const [k, a] of Object.entries(spec.args)) {
      const v = values[k] ?? "";
      if (v === "") {
        if (a.required) return null;
        continue;
      }
      args[k] = a.type === "enum" ? v : Number(v);
    }
    return { vehicle: target, name, args, ...(reason.trim() && { reason: reason.trim() }) };
  };
  const req = build();

  /** The new rows show at once: QUEUED / SENT would otherwise wait for the next stream event (R-h). */
  async function refresh(v: string) {
    try {
      for (const c of (await listCommands(v)).reverse()) store.applyCmd(c);
    } catch (e) {
      console.warn("could not refresh the command list", e);
    }
  }
  const accepted = (r: CommandRequest) => {
    setNote(`${r.name} queued for ${r.vehicle}`); // names its command: a late answer may belong to an earlier one
    void refresh(r.vehicle);
  };
  async function send() {
    if (!req || inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    setNote("");
    const r = await sendCommand(req);
    inFlight.current = false;
    setBusy(false);
    if (r.kind === "accepted") accepted(req);
    else if (r.kind === "confirm") setPending({ req, hazard: r.hazard, token: r.token, expiresInS: r.expiresInS, opener: sendButton.current });
    else {
      setError(rejectedText(r));
      if (r.ambiguous) void refresh(req.vehicle); // it may be in the list already
    }
  }

  if (!mission) return <section className="commands" aria-label="Commands"><h2>Commands</h2><p className="empty">Loading the mission database…</p></section>;
  return (
    <section className="commands" aria-label="Commands">
      <h2>Commands</h2>
      <form onSubmit={(e) => { e.preventDefault(); void send(); }}>
        <label>
          Vehicle
          <select value={target} onChange={(e) => setVehicle(e.target.value)}>
            <option value="">—</option>
            {vehicles.map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
        </label>
        <label>
          Command
          <select value={name} onChange={(e) => { setName(e.target.value); setValues({}); }}>
            <option value="">—</option>
            {Object.keys(mission.commands).map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>
        {spec && Object.entries(spec.args).map(([k, a]) => (
          <ArgInput key={k} name={k} spec={a} value={values[k] ?? ""} onChange={(v) => setValues((s) => ({ ...s, [k]: v }))} />
        ))}
        <label>
          Reason
          <input value={reason} onChange={(e) => setReason(e.target.value)} />
        </label>
        <button ref={sendButton} type="submit" disabled={busy || req === null}>Send</button>
      </form>
      {spec && <p className="desc">{spec.description}{spec.hazardous && <strong> – hazardous: {spec.hazard}</strong>}</p>}
      {error && <p role="alert">{error}</p>}
      {note && <p role="status">{note}</p>}
      <ul className="command-list">
        {commands.slice(0, SHOWN).map((c) => (
          <li key={c.cmd_id} data-cmd-id={c.cmd_id} data-state={c.state}>
            <strong>{c.vehicle}</strong> {c.name}
            {Object.keys(c.args).length > 0 && ` (${Object.entries(c.args).map(([k, v]) => `${k} = ${v}`).join(", ")})`}
            {" · "}{c.issued_by}{" · "}{c.state}
            <Timeline c={c} mission={mission} />
          </li>
        ))}
      </ul>
      {pending && (
        <ConfirmDialog
          key={pending.token}
          {...pending}
          initialReason={reason}
          opener={pending.opener}
          onAmbiguous={() => {
            setError(AMBIGUOUS_TEXT);
            void refresh(pending.req.vehicle);
          }}
          // only this dialog's own answer may close it: a late one from an abandoned dialog must not close the next
          onCancel={() => setPending((p) => (p?.token === pending.token ? null : p))}
          onSent={() => {
            accepted(pending.req);
            setPending((p) => (p?.token === pending.token ? null : p));
          }}
        />
      )}
    </section>
  );
}
