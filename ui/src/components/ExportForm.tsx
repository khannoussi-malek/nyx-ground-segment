import { useState, type FormEvent } from "react";
import { getExport, type SessionView } from "../server/api";
import { exportQuery, type ExportForm as Form } from "../tools/compare";

const EMPTY: Form = { vehicle: "", from: "", to: "", session: "", params: [], format: "csv" };

/** Save a blob under a name through a temporary link. */
function save(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = Object.assign(document.createElement("a"), { href: url, download: filename });
  a.click();
  URL.revokeObjectURL(url);
}

/** "Export data" (D-EXT-13): a session, or a vehicle and From/To in UTC; no parameters picked = every parameter. */
export function ExportPanel({ vehicles, sessions, params }: { vehicles: string[]; sessions: SessionView[]; params: string[] }) {
  const [f, setF] = useState<Form>(EMPTY);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (p: Partial<Form>) => setF((cur) => ({ ...cur, ...p }));

  async function run(e: FormEvent) {
    e.preventDefault();
    const q = exportQuery(f);
    if (!q.ok) return setError(q.error);
    setBusy(true);
    setError("");
    const r = await getExport(q.query);
    setBusy(false);
    if (r.ok) save(r.blob, r.filename);
    else setError(r.message);
  }
  const bySession = f.session !== "";
  return (
    <section className="export" aria-label="Export data">
      <h3>Export data</h3>
      <form onSubmit={(e) => void run(e)}>
        <label>
          Session
          <select value={f.session} onChange={(e) => set({ session: e.target.value })}>
            <option value="">a time window</option>
            {sessions.map((s) => <option key={s.id} value={s.id}>{s.name} · {s.vehicle}</option>)}
          </select>
        </label>
        <label>
          Vehicle
          <select value={f.vehicle} disabled={bySession} onChange={(e) => set({ vehicle: e.target.value })}>
            <option value="">{"—"}</option>
            {vehicles.map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
        </label>
        <label>
          From (UTC)
          <input value={f.from} disabled={bySession} placeholder="2026-11-20T08:05Z" onChange={(e) => set({ from: e.target.value })} />
        </label>
        <label>
          To (UTC)
          <input value={f.to} disabled={bySession} placeholder="2026-11-20T09:05Z" onChange={(e) => set({ to: e.target.value })} />
        </label>
        <label>
          Parameters (none = all)
          <select multiple value={f.params} onChange={(e) => set({ params: [...e.target.selectedOptions].map((o) => o.value) })}>
            {params.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </label>
        <label>
          Format
          <select value={f.format} onChange={(e) => set({ format: e.target.value as Form["format"] })}>
            <option value="csv">CSV</option>
            <option value="parquet">Parquet</option>
          </select>
        </label>
        <button type="submit" disabled={busy}>Export</button>
      </form>
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
