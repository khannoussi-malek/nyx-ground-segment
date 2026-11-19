import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { createReplay, deleteReplay, vehicles as loadVehicles, type MissionDb } from "../server/api";
import { AlarmList } from "./AlarmList";
import { EventStrip } from "./EventStrip";
import { LiveTable } from "./LiveTable";
import { createReplayStore, endText, ingestReplay, RECORDED_ONLY_VEHICLES, REPLAY_KINDS, utcInput, type ReplayStore } from "../server/replay";
import type { Store } from "../state/store";

/** The live store's open unacknowledged alarms, so a live RED is not missed during a replay (R2-27). */
function LiveCounter({ live }: { live: Store }) {
  const alarms = useSyncExternalStore(live.subscribe, live.alarms, live.alarms);
  const open = alarms.filter((a) => a.state === "UNACK" || a.state === "RTN_UNACK");
  return <span role="status">LIVE: {open.length} unacknowledged ({open.filter((a) => a.severity === "RED").length} RED)</span>;
}

function Progress({ rs }: { rs: ReplayStore }) {
  const t = useSyncExternalStore(rs.subscribe, rs.t, rs.t);
  const ended = useSyncExternalStore(rs.subscribe, rs.ended, rs.ended);
  const los = useSyncExternalStore(rs.subscribe, rs.losOpen, rs.losOpen);
  return (
    <p className="replay-progress">
      {t !== null && <time>Replay time {new Date(t).toISOString()}</time>}
      {los && <strong> LOSS OF SIGNAL (recorded)</strong>}
      {ended && <strong role="status"> {endText(ended)}</strong>}
    </p>
  );
}

/** Replay of recorded data on its own store; the live SSE, alarms and beep stay with the console (D-UI-16, D-REP-03). */
export function ReplayPage({ live, mission, user }: { live: Store; mission: MissionDb | null; user: string }) {
  const [names, setNames] = useState<string[]>([]);
  const [vehicle, setVehicle] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [speed, setSpeed] = useState<1 | 10>(1);
  const [error, setError] = useState<string | null>(null);
  const [rs, setRs] = useState<ReplayStore | null>(null);
  const [busy, setBusy] = useState(false); // a create is pending or a stream is running
  const [stopped, setStopped] = useState(false);
  const run = useRef<{ id: string; close: () => void } | null>(null);
  const gen = useRef(0); // bumped by every stop and by leaving the page; a create that resolves after it is dropped
  const starting = useRef(false);
  useEffect(() => {
    void loadVehicles().then((v) => {
      setNames([...new Set([...v, ...RECORDED_ONLY_VEHICLES])]); // live first, then recorded-only
      setVehicle((cur) => cur || (v[0] ?? ""));
    });
    return () => stop(); // leaving the page ends the replay
  }, []);

  const stop = () => {
    gen.current++;
    const r = run.current;
    run.current = null;
    if (!r) return;
    r.close();
    void deleteReplay(r.id);
  };
  const start = async () => {
    const f = utcInput(from);
    const t = utcInput(to);
    if (!vehicle || f === null || t === null) return setError("Pick a vehicle and a from and to time (UTC)");
    if (starting.current || run.current) return; // double Start
    stop();
    const g = gen.current;
    starting.current = true;
    setBusy(true);
    setStopped(false);
    setError(null);
    let res;
    try {
      res = await createReplay({ vehicle, from: f, to: t, speed });
    } finally {
      starting.current = false;
    }
    if (g !== gen.current) return void (res.ok && deleteReplay(res.replayId)); // stopped or left meanwhile
    if (!res.ok) return setBusy(false), setError(res.message);
    const next = createReplayStore();
    const es = new EventSource(`/api/replays/${res.replayId}/events`);
    let opened = false;
    es.addEventListener("open", () => (opened = true));
    for (const kind of REPLAY_KINDS)
      es.addEventListener(kind, (e) => {
        try {
          ingestReplay(next, kind, JSON.parse((e as MessageEvent<string>).data));
        } catch (err) {
          console.warn(`dropping malformed ${kind} event`, err);
        }
        if (kind === "replay_end") (es.close(), (run.current = null), setBusy(false)); // ended: Start may run again
      });
    // no reconnect: the server's stream is single-use, so a reopen would only meet 404; the end is shown instead
    es.addEventListener("error", () => {
      es.close();
      run.current = null;
      setBusy(false);
      next.interrupt(opened);
    });
    run.current = { id: res.replayId, close: () => es.close() };
    setRs(next);
  };

  return (
    <div className="replay-frame">
      <div className="replay-banner" role="banner">
        <strong>REPLAY</strong>
        <LiveCounter live={live} />
      </div>
      <form className="replay-controls" onSubmit={(e) => (e.preventDefault(), void start())}>
        <label>
          Vehicle
          <select value={vehicle} onChange={(e) => setVehicle(e.target.value)}>
            {names.map((n) => <option key={n}>{n}</option>)}
          </select>
        </label>
        <label>
          From (UTC)
          <input type="datetime-local" aria-label="From (UTC)" value={from} onChange={(e) => setFrom(e.target.value)} />
        </label>
        <label>
          To (UTC)
          <input type="datetime-local" aria-label="To (UTC)" value={to} onChange={(e) => setTo(e.target.value)} />
        </label>
        <label>
          Speed
          <select value={speed} onChange={(e) => setSpeed(e.target.value === "10" ? 10 : 1)}>
            <option value="1">1×</option>
            <option value="10">10×</option>
          </select>
        </label>
        <button type="submit" disabled={busy}>Start</button>
        <button type="button" onClick={() => (stop(), setBusy(false), setStopped(true))}>Stop</button>
      </form>
      {error && <p role="alert">{error}</p>}
      {stopped && <p role="status">Replay stopped</p>}
      {rs && (
        <>
          <Progress rs={rs} />
          <AlarmList store={rs.store} mission={mission} user={user} readOnly />
          <main><LiveTable store={rs.store} mission={mission} clock={rs.clock} /></main>
          <EventStrip store={rs.store} />
        </>
      )}
    </div>
  );
}
