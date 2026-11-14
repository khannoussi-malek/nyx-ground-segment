const DASH = "\u2014";
// new Date parses "…Z", "…+00:00", with or without fractions; an unparsable text shows a dash instead of throwing
const iso = (t: string): string | null => {
  const d = new Date(t);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
};

/** "HH:MM:SSZ": UTC clock text, the Z says it is UTC (UI-08). */
export const utcClock = (t: string): string => {
  const s = iso(t);
  return s ? `${s.slice(11, 19)}Z` : DASH;
};
/** "HH:MM:SS.mmmZ" */
export const utcClockMs = (t: string): string => {
  const s = iso(t);
  return s ? `${s.slice(11, 23)}Z` : DASH;
};
/** Full UTC ISO text with milliseconds, "YYYY-MM-DDTHH:MM:SS.mmmZ". */
export const utcIso = (t: string): string => iso(t) ?? DASH;
