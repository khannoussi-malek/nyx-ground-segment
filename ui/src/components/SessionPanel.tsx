import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { paramIds } from "../tools/params";
import { getReport, listSessions, reportCsvUrl, startSession, stopSession, type MissionDb, type ReportResult, type SessionReport, type SessionResult, type SessionView } from "../server/api";
import { eventText } from "../tools/eventText";
import { formatValue, sortedEntries } from "../tools/format";
import { SinceSummary } from "./SinceSummary";
import { FilterBar, keep, NO_FILTER, vehiclesOf } from "./listFilter";
import type { Store } from "../state/store";
import { utcClock, utcIso } from "../tools/time";

/** The full UTC time, allowed to wrap after its T in the narrow side pane; the text itself is unchanged (D-UI-13). */
const wrapIso = (t: string) => {
  const s = utcIso(t);
  const i = s.indexOf("T") + 1;
  return i > 0 ? <>{s.slice(0, i)}<wbr />{s.slice(i)}</> : s;
};

const MAY_NOT_HAVE_COMPLETED ="May not have completed – check the session list before trying again";
type View = { id: number; state: "loading" } | { id: number; state: "error"; message: string } | { id: number; state: "done"; result: ReportResult };

/** Mission-db order like the live table (the report's jsonb reorders keys), unknown ids after, sorted. */
function inMissionOrder(p: SessionReport["parameters"], m: MissionDb | null) {
  const known = m ? paramIds(m).filter((id) => Object.hasOwn(p, id)) : [];
  return [...known.map((id) => [id, p[id]] as const), ...sortedEntries(p).filter(([id]) => !known.includes(id))];
}

function Report({ id, result, mission, onRecheck }: { id: number; result: ReportResult; mission: MissionDb | null; onRecheck: () => void }) {
  if (result.kind === "running") return <p role="status">Session still running – stop it to get the report</p>;
  if (result.kind === "pending") {
    const n = result.frames !== null && result.expected !== null ? `: ${result.frames} of ${result.expected} frames` : "";
    return (
      <p role="status">
        Report pending{n} <button type="button" onClick={onRecheck}>Check again</button>
      </p>
    );
  }
  const r = result.report;
  return (
    <div className="report">
      <h3>
        {r.session.name} · {r.session.vehicle}
      </h3>
      <p>
        {utcIso(r.session.started_at)} – {r.session.ended_at ? utcIso(r.session.ended_at) : "running"} · {Math.round(r.duration_s)} s ·{" "}
        {r.complete ? `complete, ${r.frames} frames` : <strong>incomplete: {r.frames} of {r.expected} frames</strong>}
      </p>
      {/* the CSV exists only once the report is frozen: before that the server answers 409 JSON, not a file */}
      <p>
        <a href={reportCsvUrl(id)} download>Download CSV</a>
      </p>
      {r.note && <p>{r.note}</p>}
      <table>
        <thead>
          <tr>{["Parameter", "Unit", "n", "min", "avg", "max"].map((h) => <th key={h} scope="col">{h}</th>)}</tr>
        </thead>
        <tbody>
          {inMissionOrder(r.parameters, mission).map(([p, s]) => (
            <tr key={p}>
              <th scope="row">{p}</th>
              <td>{s.unit}</td>
              <td>{s.n}</td>
              <td>{formatValue(s.min)}</td>
              <td>{formatValue(s.avg)}</td>
              <td>{formatValue(s.max)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p>
        {r.alarms.length} alarm transitions · {r.commands.length} commands · {r.link_events.length} link events
      </p>
      <ul className="report-lines">
        {r.alarms.map((a) => (
          <li key={a.transition.key}>
            {utcClock(a.transition.t)} {a.param} {a.transition.kind} {a.transition.level ?? ""}
          </li>
        ))}
        {r.commands.map((c) => (
          <li key={c.cmd_id}>
            {utcClock(c.queued_at)} {c.vehicle} {c.name} {c.state}
          </li>
        ))}
        {r.link_events.map((e) => (
          <li key={e.key}>{eventText(e, r.link_events)}</li>
        ))}
      </ul>
    </div>
  );
}

/** Start and stop test sessions, read the frozen report, download its CSV (D-UI-17). */
export function SessionPanel({ store, mission }: { store: Store; mission: MissionDb | null }) {
  const vehicles = useSyncExternalStore(store.subscribe, store.vehicles, store.vehicles);
  const [sessions, setSessions] = useState<SessionView[] | null>(null);
  const [vehicle, setVehicle] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [view, setView] = useState<View | null>(null);
  const [filter, setFilter] = useState(NO_FILTER);
  const [since, setSince] = useState(false);
  const inFlight = useRef(false);
  const target = vehicles.includes(vehicle) ? vehicle : "";

  const refresh = useCallback(async () => {
    try {
      setSessions(await listSessions());
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not load the sessions");
    }
  }, []);
  useEffect(() => void refresh(), [refresh]);

  const latest = useRef(0);
  const openReport = useCallback(async (id: number) => {
    const mine = ++latest.current; // an answer that is no longer the latest request is ignored
    setView({ id, state: "loading" });
    let next: View;
    try {
      next = { id, state: "done", result: await getReport(id) };
    } catch (e) {
      next = { id, state: "error", message: e instanceof Error ? e.message : "could not load the report" };
    }
    if (mine === latest.current) setView(next);
  }, []);

  async function act(call: () => Promise<SessionResult>, then?: (s: SessionView) => void) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    const r = await call();
    inFlight.current = false;
    setBusy(false);
    if (r.ok) then?.(r.session);
    else setError(r.ambiguous ? MAY_NOT_HAVE_COMPLETED : r.message);
    void refresh(); // also after a refusal: another operator may have started or stopped it (SESSION_ACTIVE, 404, ...)
  }

  const active = (sessions ?? []).filter((s) => s.ended_at === null);
  const busyVehicle = active.some((s) => s.vehicle === target);
  const canStart = target !== "" && name.trim() !== "" && !busy && !busyVehicle;
  const visible = (sessions ?? []).filter((s) =>
    keep(filter, s.vehicle, `${s.name} ${s.vehicle} ${s.conductor} ${s.ended_at === null ? "active" : "ended"}`),
  );
  return (
    <section className="sessions" aria-label="Sessions">
      <h2>Sessions</h2>
      <form onSubmit={(e) => { e.preventDefault(); if (canStart) void act(() => startSession(target, name.trim()), () => setName("")); }}>
        <label>
          Vehicle
          <select value={target} onChange={(e) => setVehicle(e.target.value)}>
            <option value="">{"—"}</option>
            {vehicles.map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
        </label>
        <label>
          Session name
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <button type="submit" disabled={!canStart}>Start session</button>
      </form>
      <button type="button" aria-expanded={since} onClick={() => setSince(!since)}>Since…</button>
      {since && <SinceSummary store={store} />}
      {busyVehicle && <p className="desc">{target} already has an active session</p>}
      {error && <p role="alert">{error}</p>}
      {sessions === null ? (
        <p className="empty">Loading sessions…</p>
      ) : sessions.length === 0 ? (
        <p className="empty">No sessions yet</p>
      ) : (
        <>
          <FilterBar what="sessions" value={filter} onChange={setFilter} vehicles={vehiclesOf(sessions)} />
          {visible.length === 0 && <p className="empty" role="status">No sessions match the filter</p>}
          <div className="scroll">
            <table>
              <thead>
                <tr>{["Session", "Report", "Vehicle", "Started (UTC)", "Ended (UTC)", "Conductor"].map((h) => <th key={h} scope="col">{h}</th>)}</tr>
              </thead>
              <tbody>
                {visible.map((s) => (
                  <tr key={s.id} data-session-id={s.id} data-active={s.ended_at === null || undefined}>
                    <th scope="row">{s.name}</th>
                    <td>
                      {s.ended_at === null ? (
                        <button type="button" disabled={busy} onClick={() => void act(() => stopSession(s.id), (x) => void openReport(x.id))}>
                          Stop {s.name}
                        </button>
                      ) : (
                        <>
                          <button type="button" onClick={() => void openReport(s.id)}>Report</button>
                        </>
                      )}
                    </td>
                    <td>{s.vehicle}</td>
                    <td><time dateTime={s.started_at}>{wrapIso(s.started_at)}</time></td>
                    <td>{s.ended_at ? <time dateTime={s.ended_at}>{wrapIso(s.ended_at)}</time> : "active"}</td>
                    <td>{s.conductor}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {view?.state === "loading" && <p role="status">Loading the report…</p>}
      {view?.state === "error" && <p role="alert">{view.message}</p>}
      {view?.state === "done" && <Report id={view.id} result={view.result} mission={mission} onRecheck={() => void openReport(view.id)} />}
    </section>
  );
}
