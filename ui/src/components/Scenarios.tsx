import { useEffect, useRef, useState } from "react";
import { sendFault, testControls, type TestControls } from "../server/api";
import { GROUPS, type Scenario } from "../config/scenarios";
import { utcClock } from "../tools/time";

/** Top-bar button + drawer to start and clear simulator faults for demos. Not rendered unless GET /api/test-controls
 *  answers 200 (D-SIM-12). The vehicle is an explicit choice, like commands. */
export function Scenarios() {
  const [controls, setControls] = useState<TestControls | null>(null);
  const [open, setOpen] = useState(false);
  const [vehicle, setVehicle] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    void testControls().then((c) => mounted.current && setControls(c));
    return () => {
      mounted.current = false;
    };
  }, []);
  if (!controls) return null;

  const run = async (s: Scenario, clear: boolean) => {
    setBusy(true);
    const r = clear
      ? await sendFault("DELETE", { vehicle, fault: s.fault })
      : await sendFault("POST", { vehicle, fault: s.fault, ...(s.params && { params: s.params }) });
    if (!mounted.current) return;
    setBusy(false);
    setMsg(r.ok ? { ok: true, text: `${s.label} ${clear ? "cleared" : "started"} on ${vehicle} at ${utcClock(new Date().toISOString())}` } : { ok: false, text: r.message });
  };
  const off = busy || !controls.vehicles.includes(vehicle);
  return (
    <>
      <button onClick={() => setOpen((o) => !o)}>Scenarios</button>
      {open && (
        <section className="scenarios" aria-label="Test scenarios">
          <h2>Test scenarios</h2>
          <p>Simulator faults for testing and demos. Not an operator function. Every use is audited.</p>
          <label>
            Vehicle{" "}
            <select value={vehicle} onChange={(e) => setVehicle(e.target.value)}>
              <option value="">Choose a vehicle</option>
              {controls.vehicles.map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
          </label>
          {msg && (msg.ok ? <p role="status">{msg.text}</p> : <p role="alert">{msg.text}</p>)}
          {GROUPS.map((g) => (
            <div key={g.title}>
              <h3>{g.title}</h3>
              <ul>
                {g.items.map((s) => (
                  <li key={s.label}>
                    <button disabled={off} onClick={() => void run(s, false)}>{s.label}</button>
                    {s.clear && <button disabled={off} aria-label={`Clear ${s.label}`} onClick={() => void run(s, true)}>Clear</button>}
                    <span>{s.hint}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </section>
      )}
    </>
  );
}
