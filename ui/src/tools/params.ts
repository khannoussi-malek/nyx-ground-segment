import type { MissionDb } from "../server/api";
import { sortedEntries } from "./format";
import type { Limits } from "./gauge";
import type { ParamView } from "../components/ParamRow";

/** One row per value in the frame: mission-db order and metadata first, unknown ids after (sorted). */
export function paramViews(values: Readonly<Record<string, number | null>>, m: MissionDb | null): ParamView[] {
  if (!m) return sortedEntries(values).map(([id, value]) => ({ id, value }));
  // own keys only: a legal id like "constructor" must not resolve through Object.prototype
  const own = Object.hasOwn;
  const known = Object.keys(m.parameters).filter((id) => own(values, id));
  const unknown = sortedEntries(values).filter(([id]) => !own(m.parameters, id));
  return [
    ...known.map((id): ParamView => {
      const meta = m.parameters[id];
      const checks = own(m.limits, id) ? m.limits[id].checks : [];
      const limits: Limits | undefined = checks.find((c) => c.type === "limit");
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
