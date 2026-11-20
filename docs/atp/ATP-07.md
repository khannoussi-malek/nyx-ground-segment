# ATP-07: investigation (OPS-6)
Covers DAT-03 to DAT-06, UI-04. Run on a stack that has been up for a while; it uses the NYX-TA9 test vehicle, which has no live frames and exists only for backfill. Each stack can backfill NYX-TA9 hours once: a second run needs later hours (or a fresh stack).

Choose the hours first. Run `date -u +%Y-%m-%dT%H:00:00Z`: that is H, the start of the current UTC hour. Backfill whole UTC hours that ended in the last 24 h, so their hot rows are still kept (`HOT_RETENTION_H` is 25 h; an older hour would have its hot rows deleted at once and step 4 could not show them intact). Below: hours H-4 h and H-3 h in step 1, H-2 h in step 4. The dates are examples; replace them.

1. **Backfill two past hours** with the archiver running:
   ```
   docker compose run --rm --no-deps simulator gs-sim backfill --vehicle NYX-TA9 \
     --from 2026-11-16T08:00:00Z --to 2026-11-16T10:00:00Z \
     --fault 'leak@600:{"duration_s":120}' --fault 'dropout@900:{"duration_s":10}'
   ```
   The leak starts 10 min into the window and lasts 2 min, the dropout starts at 15 min and lasts 10 s: both lie inside the 20 min window replayed in step 5. It prints `sent 71900 frames to tm:NYX-TA9`: 2 h at 10 Hz is 72000, minus the 100 frames the dropout never sends. Ingest then stores them (a few minutes). Wait until the hot row count of NYX-TA9 reaches 71900 and stops growing, so the archived object in step 3 is the final one (the archiver is running and may archive a partial hour first; it then replaces it with a complete version):
   ```
   PGPASSWORD=$(grep '^GS_APP_PASSWORD=' .env | cut -d= -f2) docker compose exec -T -e PGPASSWORD db \
     psql -h 127.0.0.1 -U gs_app -d gs -c "select count(*) from telemetry where vehicle='NYX-TA9'"
   ```
2. **Wait for the archiver.** Hours that ended are archived at the next loop, every `ARCHIVE_LOOP_S` (300 s). Watch `docker compose logs -f archiver`, or check the object list in step 3. Hot rows stay until the hour is more than `HOT_RETENTION_H` old. Wait until both backfilled hours are recorded:
   ```
   PGPASSWORD=$(grep '^GS_APP_PASSWORD=' .env | cut -d= -f2) docker compose exec -T -e PGPASSWORD db \
     psql -h 127.0.0.1 -U gs_app -d gs -c "select hour from archive_object where vehicle='NYX-TA9' order by hour"
   ```
   Expected rows (psql prints timestamptz like this; use your backfill date): `2026-11-16 08:00:00+00` and `2026-11-16 09:00:00+00`.
3. **Verify an object and its sidecar** with the MinIO client inside the stack (no secret leaves the container):
   ```
   docker compose run --rm --no-deps --entrypoint bash minio-init -c '
     mc alias set s3 http://minio:9000 "$S3_ACCESS_KEY" "$S3_SECRET_KEY" >/dev/null &&
     mc ls --recursive s3/archive/NYX-TA9 &&
     cd /tmp &&
     mc cp s3/archive/NYX-TA9/2026/11/16/08.parquet . &&
     mc cp s3/archive/NYX-TA9/2026/11/16/08.parquet.sha256 . &&
     sha256sum -c 08.parquet.sha256'
   ```
   Pass: `08.parquet: OK`. Use the date and hour you backfilled.
4. **Kill the archiver mid-upload.** Stop the archiver first, so it cannot archive the new hour before the crash hook is armed, backfill the next hour and wait for ingest, count, then start the archiver with the hook (it exists only with `GS_TEST_HOOKS=1`):
   ```
   docker compose stop archiver
   docker compose run --rm --no-deps simulator gs-sim backfill --vehicle NYX-TA9 \
     --from 2026-11-16T10:00:00Z --to 2026-11-16T11:00:00Z
   ```
   Wait until the hot row count of NYX-TA9 reaches 107900 (71900 plus the 36000 frames of this hour) and stops growing; this is the "before" count:
   ```
   PGPASSWORD=$(grep '^GS_APP_PASSWORD=' .env | cut -d= -f2) docker compose exec -T -e PGPASSWORD db \
     psql -h 127.0.0.1 -U gs_app -d gs -c "select count(*) from telemetry where vehicle='NYX-TA9'"
   ```
   Still before arming, list the recorded hours:
   ```
   PGPASSWORD=$(grep '^GS_APP_PASSWORD=' .env | cut -d= -f2) docker compose exec -T -e PGPASSWORD db \
     psql -h 127.0.0.1 -U gs_app -d gs -c "select hour from archive_object where vehicle='NYX-TA9' order by hour"
   ```
   It shows only `2026-11-16 08:00:00+00` and `2026-11-16 09:00:00+00`, no hour 10. Only now arm the crash and start the archiver:
   ```
   GS_TEST_HOOKS=1 ARCHIVER_CRASH_AT=archive:after_put ARCHIVE_LOOP_S=2 docker compose up -d archiver
   ```
   Within seconds the archiver dies right after uploading hour 10 and before it records the object; `docker compose logs archiver` shows the line `test hook CRASH_AT=archive:after_put` (it names no hour; the plan builds the oldest pending hour first and hours 08 and 09 were already recorded). Compose brings the archiver back by itself (`restart: unless-stopped`, same environment); the hook fires once per container, so it does not crash again, and the restarted pass rebuilds hour 10 and records it within seconds. You cannot see the state in between; check the outcome. Pass: the hook log line is present, the `archive_object` query now lists `2026-11-16 08:00:00+00`, `2026-11-16 09:00:00+00` and `2026-11-16 10:00:00+00` (hour 10 was absent before arming, so the crash did not lose it), the sidecar of hour 10 verifies as in step 3, and the hot row count is still 107900. Clean up by restarting without the hook, in a shell where the three variables are not set: `docker compose up -d --force-recreate archiver`.
5. **Replay a 20 min window at 10x.** In the console press Replay in the top bar, choose NYX-TA9, from `2026-11-16T08:00Z` to `2026-11-16T08:20Z` (UTC, type them in this form; the Z is required), speed 10x, **Start**. Expected: the purple REPLAY banner stays and cannot be dismissed, the leak raises its alarms at 10 min of replay time, the 10 s dropout at 15 min shows STALE and LOSS OF SIGNAL on replay time, and the replay ends after about 2 min. A window over an object that fails its hash check is refused with 409 "archive object failed hash check".
6. Record the result in `docs/atp-report.md` (Gate 5).

Run on a fresh stack or before e2e: the replay e2e fixture leaves an open ARCHIVE_CORRUPT on NYX-TA9 hour B, so the archiver `/healthz` reads `degraded` afterwards.
