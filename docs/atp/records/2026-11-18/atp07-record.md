# ATP-07 acceptance record (investigation, OPS-6)

Fresh clone, branch gate5-acceptance. Run 2026-11-18 (UTC). Vehicle NYX-TA9.
H = 2026-11-18T16:00:00Z. Hours used: 12:00 and 13:00 (step 1), 14:00 (step 4); all ended within the last 24 h.
Secrets are read from .env inside the commands and never printed. Helper `q.sh "<sql>"` = the doc's psql command (see deviation 1).

## Step 1: backfill two hours (16:55:36Z - 16:56:26Z)
`docker compose run --rm --no-deps simulator gs-sim backfill --vehicle NYX-TA9 --from 2026-11-18T12:00:00Z --to 2026-11-18T14:00:00Z --fault 'leak@600:{"duration_s":120}' --fault 'dropout@900:{"duration_s":10}'`
- Output: `sent 71900 frames to tm:NYX-TA9`
- Hot count: 71900 at 16:56:31 and still 71900 at 16:57:01 (stable).
- Pass (prints 71900; count reaches 71900 and stops): PASS

## Step 2: archiver records both hours
- Polled every 30 s from 16:57:05: empty until 16:59:36; at 17:00:06 `archive_object` lists `2026-11-18 12:00:00+00` and `2026-11-18 13:00:00+00`.
- Rows: hour 12 = 35900 rows (verified/stored at 16:59:51Z), hour 13 = 36000 rows (16:59:51Z). 35900 = 36000 minus the 100 frames of the dropout.
- The archiver did not archive a partial hour first. Hot rows were kept.
- Pass (both hours recorded): PASS

## Step 3: object and sidecar (17:00:11Z)
`docker compose run --rm --no-deps --entrypoint bash minio-init -c 'mc alias set ... && mc ls --recursive s3/archive/NYX-TA9 && cd /tmp && mc cp .../2026/11/18/12.parquet . && mc cp .../12.parquet.sha256 . && sha256sum -c 12.parquet.sha256'`
- `mc ls`: 2026/11/18/12.parquet (2.9MiB), 12.parquet.sha256 (77B), 13.parquet (3.0MiB), 13.parquet.sha256 (77B)
- `12.parquet: OK`
- Pass (`12.parquet: OK`): PASS

## Step 4: kill the archiver mid-upload
Run at 17:00-17:02Z and re-verified at 17:28Z from the database, bucket and container state; not re-run.
- 17:00:15Z `docker compose stop archiver` (stopped).
- 17:00:15Z-17:00:46Z backfill `--from 2026-11-18T14:00:00Z --to 2026-11-18T15:00:00Z`: `sent 36000 frames to tm:NYX-TA9`.
- Before arming (17:00:49Z and re-checked): hot count 107900; `archive_object` hours = 12:00, 13:00 only (no hour 14).
- 17:01:12Z `GS_TEST_HOOKS=1 ARCHIVER_CRASH_AT=archive:after_put ARCHIVE_LOOP_S=2 docker compose up -d archiver`.
- Log at 17:01:15Z: `{"logger": "gs.common.hooks", "msg": "test hook CRASH_AT=archive:after_put"}` (present at 17:01:40Z, count 1).
- 17:01:40Z: MinIO has 2026/11/18/14.parquet and 14.parquet.sha256 (both stamped 17:01:16Z); `archive_object` lists 12:00, 13:00 and 14:00 (row for 14:00 written 17:01:16.85Z, 36000 rows); `14.parquet: OK`; hot count 107900; archiver healthy (restarted by compose).
- Cleanup at 17:02:08Z: `docker compose up -d --force-recreate archiver`, no hook variables in the shell. Now (17:28Z): container StartedAt 17:01:48Z, RestartCount 0, env GS_TEST_HOOKS=0, ARCHIVE_LOOP_S=300, no ARCHIVER_CRASH_AT, health ok. After the recreate the crash log line is no longer in `docker compose logs` (the old container was removed); the line was captured at 17:01:40Z before the cleanup.
- Pass (hook log line, hours 12/13/14 listed, sidecar OK, count still 107900): PASS

## Step 5: replay 20 min at 10x (console, Playwright as fc1)
Observed with two Playwright polling scripts (5 s and 200 ms polling) that are not kept in the repository.
Window 2026-11-18T12:00 to 12:20 UTC, NYX-TA9, speed 10x, Start.

Run 1 (17:02:26Z-17:04:51Z, screenshots atp07-1-form.png, atp07-4-finished.png):
- Banner REPLAY visible, background rgb(107, 47, 163) (purple), 0 buttons inside it (cannot be dismissed); visible to the end.
- Replay time advanced ~43 s per 5 s wall time (10x).
- Leak: at replay time 12:10:16 the list shows one RED, NYX-TA9 Cabin total pressure `RED LOW · 94.87 kPa (limit 95)`, summary `1 RED`; at 12:12:26 it shows `RETURNED · was RED LOW`, summary NOMINAL (leak 10:00 + 2 min).
- Dropout: not caught. The 10 s dropout is 1 s of wall time at 10x and my 5 s polling stepped over it (12:14:37 then 12:15:21). This was a sampling miss, not a failed criterion; see run 2.
- Replay finished at +145 s (about 2 min 25 s); banner still visible.

Run 2 (17:28:21Z-17:30:48Z, same window, polling every 200 ms; screenshots atp07-3-dropout.png, atp07-4-finished.png overwritten by this run):
- 17:30:13Z at replay time 12:15:03.9Z: summary `LOSS OF SIGNAL`, 8 STALE parameter rows; screenshot shows banner `LOSS OF SIGNAL (recorded)`, event `12:14:59Z NYX-TA9 LOS since 12:14:59Z`, all 8 parameters STALE.
- Banner visible and 0 buttons again; finished after 147 s.
- Note: atp07-2-leak.png (taken by run 2 at replay time 12:10:xx) shows NOMINAL because it was taken just before the alarm was raised; the leak evidence in this record is the run 1 log above; the committed evidence is `e2e/replay.spec.ts` passing on a fresh stack (it asserts that the replay's alarm list has the same parameters as the alarms recorded in the replayed window, which the backfilled leak raised) and the demo recording from 0:58 ([demo](../../../demo/README.md)).
- Not exercised: the 409 "archive object failed hash check" (needs a corrupted object; covered by e2e/replay.spec.ts, not by this procedure).
- Pass (banner stays and is not dismissible; leak alarms at 10 min; STALE and LOS at 15 min on replay time; ends after about 2 min): PASS. Duration is 2.4 min against "about 2 min"; run 2 was a second observation run of the same step, not a retry after failure.

## Step 6
Step 6 is the acceptance report, [atp-report.md](../../../atp-report.md).

## Result per requirement
- DAT-03 (24 h hot data; 1 h of one parameter < 300 ms): NOT MEASURED by ATP-07. Only shows hot rows are retained inside the 25 h window (107900 rows still hot after archiving). The latency is measured in ATP-08 (perf/run_n.py). PARTIAL.
- DAT-04 (each vehicle-hour archived as Parquet + .sha256): PASS (hours 12, 13, 14 each have object and sidecar; 12 and 14 verified `OK`; 13 sidecar present and recorded in archive_object).
- DAT-05 (hot deleted only after verification; killed upload loses nothing): PASS for the kill part (crash after put, hour 14 recorded and rebuilt, sidecar OK, hot count unchanged at 107900). Deletion after verify is not shown here (hot rows younger than retention); covered by integration tests.
- DAT-06 (past window replays at 1x or 10x with the same alarms as live and a REPLAY banner): PASS at 10x for banner, leak alarm and LOS/STALE on replay time; 1x and equality with the recorded alarms not compared here.
- UI-04 (parameter charts 2 min to 24 h; 24 h < 2 s): NOT EXERCISED by any ATP-07 step. PARTIAL / not covered here.

## Doc deviations (1)
1. The doc's psql command uses `docker compose exec -e PGPASSWORD db psql ...`; I ran it from a non-interactive shell, which needs `exec -T` (no TTY), with `-At` for plain output. Same query, same result.
(The dates and hours were replaced as the doc instructs; that is not a deviation. The doc line "Covers DAT-03 to DAT-06, UI-04" is wider than the steps, which contain nothing for DAT-03 latency or UI-04 charts.)

## Notes
- The archiver logs `urllib3 Connection pool is full, discarding connection: minio` every 5 s; it did not affect any step.
