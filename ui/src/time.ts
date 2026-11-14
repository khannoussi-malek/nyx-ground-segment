/** "2026-11-14T10:00:00.123456Z" -> "10:00:00.123Z": UTC clock text, the Z says it is UTC (UI-08). */
export const utcClock = (iso: string): string => `${iso.slice(11, 23)}Z`;
