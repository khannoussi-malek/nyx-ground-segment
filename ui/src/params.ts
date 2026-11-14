import type { MissionDb } from "./api";
import { sortedEntries } from "./format";
import type { Limits } from "./gauge";
import type { ParamView } from "./ParamRow";

/** One row per value in the frame: mission-db order and metadata first, unknown ids after (sorted). */
export function paramViews(values: Readonly<Record<string, number | null>>, m: MissionDb | null): ParamView[] {
  if (!m) return sortedEntries(values).map(([id, value]) => ({ id, value }));
  const known = Object.keys(m.parameters).filter((id) => id in values);
  const unknown = sortedEntries(values).filter(([id]) => !(id in m.parameters));
  return [
    ...known.map((id): ParamView => {
      const meta = m.parameters[id];
      const limits: Limits | undefined = m.limits[id]?.checks.find((c) => c.type === "limit");
      return {
        id,
        value: values[id],
        unit: meta.unit,
        ...(meta.description && { label: meta.description }),
        ...(limits && { limits }),
      };
    }),
    ...unknown.map(([id, value]) => ({ id, value })),
  ];
}
