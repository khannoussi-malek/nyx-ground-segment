import { expect, test } from "vitest";
import { alignParam, DASHES, exportQuery, tPlus } from "./compare";

test("tPlus is T+hh:mm:ss, past 24 h too", () => {
  expect(tPlus(0)).toBe("T+00:00:00");
  expect(tPlus(3725.9)).toBe("T+01:02:05");
  expect(tPlus(86400)).toBe("T+24:00:00");
});

test("every session gets its own dash pattern", () => {
  expect(new Set(DASHES.map((d) => d.join(","))).size).toBe(DASHES.length);
});

const f = { vehicle: "NYX-TA1", from: "2026-11-20T08:05Z", to: "2026-11-20T09:05:30Z", session: "", params: [], format: "csv" as const };

test("the window export builds the exact query", () => {
  expect(exportQuery({ ...f, params: ["a", "b"], format: "parquet" })).toEqual({
    ok: true,
    query: "vehicle=NYX-TA1&from=2026-11-20T08%3A05%3A00.000Z&to=2026-11-20T09%3A05%3A30.000Z&params=a%2Cb&format=parquet",
  });
});

test("a session export sends the session, not the window", () => {
  expect(exportQuery({ ...f, session: "7" })).toEqual({ ok: true, query: "session=7&format=csv" });
});

test("a time without Z is rejected", () => {
  for (const bad of ["2026-11-20T08:05", "2026-11-20 08:05Z", "2026-11-20T08:05+00:00", ""]) {
    const r = exportQuery({ ...f, from: bad });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toMatch(/From must be a UTC time ending in Z/);
  }
  expect(exportQuery({ ...f, to: "2026-11-20T09:05" })).toMatchObject({ ok: false, error: expect.stringMatching(/^To must/) });
  expect(exportQuery({ ...f, vehicle: "" })).toMatchObject({ ok: false });
});

test("alignParam gives each session its own row on the union of bucket times", () => {
  const b = (t: number, v: number) => ({ t_rel_s: t, lo: v - 1, hi: v + 1, mean: v, n: 3 });
  const c = {
    bucket_s: 1,
    sessions: [{ id: 1 }, { id: 2 }],
    series: [
      { session_id: 1, param: "p", buckets: [b(0, 10), b(1, 11)] },
      { session_id: 2, param: "p", buckets: [b(1, 20), b(2, 21)] },
    ],
  } as never;
  expect(alignParam(c, "p")).toEqual({
    x: [0, 1, 2],
    lines: [[10, 11, null], [null, 20, 21]],
    los: [[9, 10, null], [null, 19, 20]],
    his: [[11, 12, null], [null, 21, 22]],
  });
});
