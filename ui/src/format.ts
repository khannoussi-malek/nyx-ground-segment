export const formatValue = (v: number | null): string => (v === null ? "—" : v.toFixed(2));

export const sortedEntries = (values: Readonly<Record<string, number | null>>) =>
  Object.entries(values).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
