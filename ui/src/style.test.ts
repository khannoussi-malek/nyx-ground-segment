import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

// D-UI-07: palette verified WCAG AA. Reads the tokens straight from style.css so a
// colour change that breaks contrast fails here, not in an operator's eyes.
const css = readFileSync(new URL("./style.css", import.meta.url), "utf8");
const tokens = Object.fromEntries(
  [...css.matchAll(/--([\w-]+):\s*(#[0-9a-fA-F]{6})\s*;/g)].map((m) => [m[1], m[2]]),
);

const channel = (c: number) => {
  const s = c / 255;
  return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
};
const luminance = (hex: string) => {
  const [r, g, b] = [1, 3, 5].map((i) => channel(parseInt(hex.slice(i, i + 2), 16)));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const contrast = (a: string, b: string) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};

// [foreground, background, minimum]: text 4.5:1, borders and focus rings 3:1 (WCAG 1.4.11)
const PAIRS: [string, string, number][] = [
  ["ink", "bg", 4.5],
  ["ink", "surface", 4.5],
  ["ink-muted", "bg", 4.5],
  ["ink-muted", "surface", 4.5],
  ["ink-muted", "surface-sunken", 4.5],
  ["on-action", "action", 4.5],
  ["focus", "bg", 3],
  ["focus", "surface", 3],
  ["field-border", "surface", 3],
  ["nominal-ink", "surface", 4.5],
  ["stale-ink", "surface", 4.5],
  ["red", "surface", 4.5],
  ["on-red", "red", 4.5],
  ["yellow-ink", "yellow", 4.5],
  ["yellow-ink", "surface", 3],
  ["on-replay", "replay", 4.5],
  ["on-bar", "bar", 4.5],
  ["on-bar-muted", "bar", 4.5],
  ["on-action", "action-hover", 4.5],
  ["action", "bar", 3],
  // limit gauge at rest, grey only (D-UI-07, G20); patterns tell the zones apart (WCAG 1.4.11)
  ["gauge-edge", "surface", 3], // the track outline: a white edge zone still shows
  ["zone-yellow-stripe", "zone-normal", 3],
  ["zone-red-stripe", "zone-yellow-stripe", 3],
  ["gauge-pointer", "surface", 3],
  ["gauge-pointer", "zone-normal", 3],
  ["gauge-pointer", "zone-base", 3],
  ["gauge-pointer", "zone-yellow-stripe", 3],
  ["gauge-halo", "zone-red-stripe", 3], // the needle's light outline carries it over dark stripes
  // the engine's live limit state fills its zone (G26)
  ["yellow-ink", "yellow", 3], // stripes in the filled yellow zone
  ["gauge-pointer", "yellow", 3],
  ["gauge-halo", "red", 3], // the needle on the solid red zone
  ["ink", "surface", 4.5], // printed limits
  ["ink", "yellow", 4.5],
  ["ink", "red-tint", 4.5],
  ["ink-muted", "red-tint", 4.5],
  ["ink", "yellow-tint", 4.5],
  ["ink-muted", "yellow-tint", 4.5],
];

describe("style.css palette (D-UI-07)", () => {
  it.each(PAIRS)("--%s on --%s meets %s:1", (fg, bg, min) => {
    expect(tokens[fg], `--${fg} missing`).toBeDefined();
    expect(tokens[bg], `--${bg} missing`).toBeDefined();
    expect(contrast(tokens[fg], tokens[bg])).toBeGreaterThanOrEqual(min);
  });

  it("matches the ratios quoted in D-UI-07", () => {
    expect(contrast("#FFFFFF", "#B3141B")).toBeCloseTo(6.92, 1);
  });
});
