/** One filter for every long list (commands, sessions, events): a vehicle and free text. */
export type Filter = { vehicle: string; text: string };
export const NO_FILTER: Filter = { vehicle: "", text: "" };

/** The row stays when its vehicle is the chosen one and every typed word appears in its text (any case, any order). */
export function keep(f: Filter, vehicle: string | null | undefined, text: string): boolean {
  if (f.vehicle !== "" && vehicle !== f.vehicle) return false;
  const hay = text.toLowerCase();
  return f.text.toLowerCase().split(/\s+/).every((w) => hay.includes(w));
}

/** The vehicle choices come from the list itself: it may hold vehicles that are not live any more. */
export const vehiclesOf = (rows: readonly { vehicle?: string | null }[]): string[] =>
  [...new Set(rows.map((r) => r.vehicle).filter((v): v is string => !!v))].sort();

export function FilterBar({ what, value, onChange, vehicles }: {
  what: string; // "commands", "sessions", "events": names the search region for screen readers
  value: Filter;
  onChange: (f: Filter) => void;
  vehicles: readonly string[];
}) {
  return (
    <div className="list-filter" role="search" aria-label={`Filter ${what}`}>
      <label>
        Show
        <select value={value.vehicle} onChange={(e) => onChange({ ...value, vehicle: e.target.value })}>
          <option value="">All vehicles</option>
          {vehicles.map((v) => <option key={v} value={v}>{v}</option>)}
        </select>
      </label>
      <label>
        Search
        <input type="search" value={value.text} onChange={(e) => onChange({ ...value, text: e.target.value })} />
      </label>
    </div>
  );
}
