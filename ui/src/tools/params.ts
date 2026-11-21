import type { MissionDb } from "../server/api";
import { sortedEntries } from "./format";
import type { Limits } from "./gauge";
import type { ParamView } from "../components/ParamRow";

type Meta = { unit: string; description?: string };

/** Unit/description of a raw or derived parameter (own keys only: "constructor" is a legal id). */
export function paramMeta(m: MissionDb | null | undefined, id: string): Meta | undefined {
  if (!m) return undefined;
  return Object.hasOwn(m.parameters, id) ? m.parameters[id] : Object.hasOwn(m.derived, id) ? m.derived[id] : undefined;
}

/** Every parameter id of the mission db: raw first, derived after. */
export const paramIds = (m: MissionDb): string[] => [...Object.keys(m.parameters), ...Object.keys(m.derived)];

/** One row per value in the frame: mission-db order and metadata first, unknown ids after (sorted). */
export function paramViews(values: Readonly<Record<string, number | null>>, m: MissionDb | null, mode?: string | null): ParamView[] {
  if (!m) return sortedEntries(values).map(([id, value]) => ({ id, value }));
  // own keys only: a legal id like "constructor" must not resolve through Object.prototype
  const own = Object.hasOwn;
  const known = paramIds(m).filter((id) => own(values, id));
  const unknown = sortedEntries(values).filter(([id]) => !paramMeta(m, id));
  return [
    ...known.map((id): ParamView => {
      const meta = paramMeta(m, id)!;
      const entry = own(m.limits, id) ? m.limits[id] : undefined;
      // the limits of the vehicle's current mode set, else the default ones (a mode with its own set is judged by it)
      const checks = (mode != null && entry && own(entry.checks_by_mode ?? {}, mode) ? entry.checks_by_mode[mode] : entry?.checks) ?? [];
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
