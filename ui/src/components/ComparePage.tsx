import { useEffect, useState, type FormEvent } from "react";
import { getCompare, listSessions, vehicles as loadVehicles, type Compare, type MissionDb, type SessionView } from "../server/api";
import { utcIso } from "../tools/time";
import { CompareCharts } from "./CompareCharts";
import { ExportPanel } from "./ExportForm";

const [MIN_SESSIONS, MAX_SESSIONS, MIN_PARAMS, MAX_PARAMS] = [2, 6, 1, 4];

function Pick<T extends string | number>({ legend, items, chosen, max, onToggle }: {
  legend: string;
  items: { key: T; text: string }[];
  chosen: T[];
  max: number;
  onToggle: (k: T) => void;
}) {
  return (
    <fieldset>
      <legend>{legend}</legend>
      {items.map((i) => (
        <label key={i.key}>
          <input type="checkbox" checked={chosen.includes(i.key)} disabled={!chosen.includes(i.key) && chosen.length >= max} onChange={() => onToggle(i.key)} />
          {i.text}
        </label>
      ))}
    </fieldset>
  );
}

const toggle = <T,>(list: T[], k: T): T[] => (list.includes(k) ? list.filter((x) => x !== k) : [...list, k]);

/** Compare page (D-EXT-12): 2-6 sessions on one time axis, 1-4 parameters, plus the export form. */
export function ComparePage({ mission }: { mission: MissionDb | null }) {
  const [sessions, setSessions] = useState<SessionView[]>([]);
  const [names, setNames] = useState<string[]>([]);
  const [ids, setIds] = useState<number[]>([]);
  const [params, setParams] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Compare | null>(null);
  useEffect(() => {
    let mounted = true;
    listSessions().then((s) => mounted && setSessions(s), () => mounted && setError("Sessions could not be loaded"));
    void loadVehicles().then((v) => mounted && setNames(v));
    return () => {
      mounted = false;
    };
  }, []);
  const known = mission ? Object.keys(mission.parameters) : [];
  const ready = ids.length >= MIN_SESSIONS && params.length >= MIN_PARAMS;

  async function run(e: FormEvent) {
    e.preventDefault();
    if (!ready || busy) return;
    setBusy(true);
    setError("");
    const r = await getCompare(ids, params);
    setBusy(false);
    if (r.ok) setResult(r.compare);
    else {
      setResult(null);
      setError(r.message);
    }
  }
  return (
    <div className="compare">
      <h2>Compare sessions</h2>
      <form onSubmit={(e) => void run(e)}>
        <Pick legend={`Sessions (${MIN_SESSIONS} to ${MAX_SESSIONS})`} chosen={ids} max={MAX_SESSIONS} onToggle={(k) => setIds((l) => toggle(l, k))}
          items={sessions.map((s) => ({ key: s.id, text: `${s.name} · ${s.vehicle} · ${utcIso(s.started_at)}` }))} />
        <Pick legend={`Parameters (${MIN_PARAMS} to ${MAX_PARAMS})`} chosen={params} max={MAX_PARAMS} onToggle={(k) => setParams((l) => toggle(l, k))}
          items={known.map((p) => ({ key: p, text: p }))} />
        <button type="submit" disabled={!ready || busy}>Compare</button>
      </form>
      {error && <p role="alert">{error}</p>}
      {result && <CompareCharts c={result} units={Object.fromEntries(known.map((p) => [p, mission?.parameters[p]?.unit]))} />}
      <ExportPanel vehicles={names} sessions={sessions} params={known} />
    </div>
  );
}
