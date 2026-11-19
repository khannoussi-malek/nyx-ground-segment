import { execFileSync } from "node:child_process";

/** Specs edit mission/limits.yaml and parameters.yaml and restore them after each test. A run killed
 * mid-test leaves the edit behind; the next run would test (and a commit would ship) the wrong limits. */
export default function globalSetup(): void {
  try {
    execFileSync("git", ["diff", "--quiet", "--", "mission"], { cwd: new URL("..", import.meta.url).pathname });
  } catch {
    throw new Error("mission/ has local edits (an interrupted e2e run?). Run `git checkout -- mission` and start again.");
  }
}
