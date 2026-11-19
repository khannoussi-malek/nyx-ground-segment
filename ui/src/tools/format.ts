export const formatValue = (v: number | null): string => {
  if (v === null) return "—";
  const s = v.toFixed(2);
  if (s === "-0.00") return "0.00"; // a reading that rounds to zero is not negative
  return s.replace("-", "\u2212"); // true minus: as wide as a tabular digit, lines up in columns
};

export const sortedEntries = <T,>(values: Readonly<Record<string, T>>): [string, T][] =>
  Object.entries(values).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));

/** Age now = server age at send (api_now − t_onboard) + time since receipt, one browser clock (D-UI-05). */
export const ageAt = (ageMs: number | null | undefined, receivedAt: number | undefined, now: number) =>
  ageMs == null || receivedAt === undefined ? null : ageMs + Math.max(0, now - receivedAt);

export function formatAge(ms: number | null): string {
  if (ms === null) return "—";
  const t = Math.max(0, ms);
  if (t < 10_000) return `${(t / 1000).toFixed(1)} s`;
  if (t < 120_000) return `${Math.round(t / 1000)} s`;
  return `${Math.floor(t / 60_000)} min`;
}
