import { expect, test } from "vitest";
import { PANES } from "./layout";
import { PANE_HELP } from "./paneHelp";

test("every pane has a short note: what it shows and what the operator can do there", () => {
  for (const id of PANES) {
    expect(PANE_HELP[id].length, id).toBeGreaterThan(80);
    expect(PANE_HELP[id].length, id).toBeLessThan(600); // a note, not a manual
  }
});
