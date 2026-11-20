import { useState, useSyncExternalStore, type FormEvent } from "react";
import { getSummary, type Summary } from "../server/api";
import type { Store } from "../state/store";
import { utcIso } from "../tools/time";

const SOURCE: Record<Summary["since_source"], string> = {
  query: "chosen time",
  last_logout: "your last logout",
  session_start: "the session start",
};
// ponytail: own small ISO-Z check until the replay-form parser (ui-utc-slow-source) lands on main; then unify.
const ISO_Z = /^\d{4}-\d\d-\d\dT\d\d:\d\d(:\d\d(\.\d+)?)?Z$/;
export const validSince = (s: string) => s === "" || (ISO_Z.test(s) && !Number.isNaN(Date.parse(s)));

/** "Since..." (D-EXT-21): what changed on a vehicle since the last logout, the session start or a chosen time. */
export function SinceSummary({ store }: { store: Store }) {
  const vehicles = useSyncExternalStore(store.subscribe, store.vehicles, store.vehicles);
  const [vehicle, setVehicle] = useState("");
  const [since, setSince] = useState("");
  const [error, setError] = useState("");
  const [result, setResult] = useState<Summary | null>(null);
  const [busy, setBusy] = useState(false);
  const target = vehicles.includes(vehicle) ? vehicle : "";

  async function show(e: FormEvent) {
    e.preventDefault();
    const text = since.trim();
    if (target === "" || busy) return;
    if (!validSince(text)) return setError("Since must be a UTC time ending in Z, like 2026-11-14T10:00:00Z");
    setBusy(true);
    setError("");
    const r = await getSummary(target, text);
    setBusy(false);
    if (r.ok) setResult(r.summary);
    else {
      setResult(null);
      setError(r.message);
    }
  }

  return (
    <section className="since" aria-label="Since">
      <h3>Since…</h3>
      <form onSubmit={(e) => void show(e)}>
        <label>
          Vehicle
          <select value={target} onChange={(e) => setVehicle(e.target.value)}>
            <option value="">{"—"}</option>
            {vehicles.map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
        </label>
        <label>
          Since (UTC, optional)
          <input value={since} placeholder="2026-11-14T10:00:00Z" onChange={(e) => setSince(e.target.value)} />
        </label>
        <button type="submit" disabled={target === "" || busy}>Show changes</button>
      </form>
      {error && <p role="alert">{error}</p>}
      {result && (
        <>
          <p role="status">
            Since {utcIso(result.since)} ({SOURCE[result.since_source]}) · {result.vehicle}
          </p>
          {result.items.length === 0 ? (
            <p className="empty">Nothing since then</p>
          ) : (
            <ol className="report-lines">
              {result.items.map((i, n) => (
                <li key={n} data-kind={i.kind}>
                  <time dateTime={i.t}>{utcIso(i.t)}</time> <b className="badge">{i.kind}</b> {i.text}
                </li>
              ))}
            </ol>
          )}
        </>
      )}
    </section>
  );
}
