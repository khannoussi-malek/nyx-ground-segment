SET ROLE gs_owner;

CREATE TABLE seq_state (vehicle text PRIMARY KEY, epoch timestamptz NOT NULL, last_seq bigint NOT NULL,
  last_t timestamptz NOT NULL, period_s double precision NOT NULL);                  -- R5, same txn as frames

CREATE TABLE limits_versions (                           -- one row per mission-db set (D-EXT-02)
  set_sha256 text PRIMARY KEY,                           -- sha256 of '<limits>\n<commands>\n<parameters>\n' file hashes
  limits_sha256 text NOT NULL, commands_sha256 text NOT NULL, parameters_sha256 text NOT NULL,
  limits_yaml text NOT NULL, commands_yaml text NOT NULL, parameters_yaml text NOT NULL,   -- the file bytes
  first_seen_at timestamptz NOT NULL DEFAULT clock_timestamp());

CREATE TABLE engine_state (                              -- D-ALM-11: upserted in every frame transaction
  vehicle text PRIMARY KEY, last_seq bigint NOT NULL, epoch timestamptz NOT NULL,
  set_sha256 text NOT NULL REFERENCES limits_versions,  -- mission-db set the state was built with (D-EXT-02)
  schema_version int NOT NULL,                           -- mismatch → approximate rebuild + ENGINE_STATE_RESET
  state jsonb NOT NULL,                                  -- per-check frozen State, keyed by check kind (D-ALM-10)
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp());

CREATE TABLE events (            -- GAP SEQ_JUMP SEQ_RESET SEQ_ANOMALY POISON LOS AOS FROZEN LATE_VERIFICATION
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,   --  LIMITS_APPLIED LIMITS_REJECTED ENGINE_STATE_RESET
  key text NOT NULL UNIQUE,                             -- deterministic, e.g. 'GAP/NYX-TA1/<epoch>/<from_seq>' (R5)
  vehicle text, kind text NOT NULL,                     -- no severity/source columns (D-DB-08, SR-09)
  t_onboard timestamptz, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  details jsonb NOT NULL DEFAULT '{}');                 -- epoch, from_seq, lost, lost_by_time, first_seq, set_sha256 + the 3 file hashes …

CREATE TABLE test_session (                              -- R11; one active session per vehicle (D-DB-07)
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, vehicle text NOT NULL,
  name text NOT NULL CHECK (length(name) BETWEEN 1 AND 200), conductor text NOT NULL,
  started_at timestamptz NOT NULL DEFAULT now(), ended_at timestamptz, ended_by text,
  CHECK (ended_at IS NULL OR ended_at > started_at), CHECK ((ended_at IS NULL) = (ended_by IS NULL)));
CREATE UNIQUE INDEX test_session_one_active ON test_session (vehicle) WHERE ended_at IS NULL;

CREATE TABLE alarms (                                    -- current state; one open alarm per (vehicle, param)
  alarm_id uuid PRIMARY KEY, vehicle text NOT NULL, param text NOT NULL,
  state text NOT NULL CHECK (state IN ('UNACK','ACKED','RTN_UNACK','CLEARED')),
  severity text NOT NULL CHECK (severity IN ('YELLOW','RED')), acked_sev text,
  trigger_check text NOT NULL, limits_sha256 text NOT NULL,    -- sha256 of mission/limits.yaml at the latest transition (ALM-08)
  set_sha256 text NOT NULL REFERENCES limits_versions,  -- the whole mission-db set at the latest transition (EX-10, Q6)
  check_set text NOT NULL DEFAULT 'default',             -- 'default' or the mode name (D-EXT-06; column from Gate 2, EX-21)
  opened_at timestamptz NOT NULL, closed_at timestamptz);
CREATE UNIQUE INDEX alarms_one_open ON alarms (vehicle, param) WHERE state <> 'CLEARED';

CREATE TABLE alarm_events (                              -- history; never pruned (D-DB-09)
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  key text NOT NULL UNIQUE,                              -- vehicle/param/epoch/trigger_seq/transition, or ACK request_id
  alarm_id uuid REFERENCES alarms, vehicle text NOT NULL, param text NOT NULL,
  kind text NOT NULL CHECK (kind IN ('RAISE','ESCALATE','DEESCALATE','RTN','ACK','CLEAR')),   -- no LEVEL (D-ALM-11)
  check_kind text, level text, severity text, value double precision,  -- value: the triggering reading (sample, or slope per second for a ROC check)
  check_set text NOT NULL DEFAULT 'default',  -- D-EXT-06
  trigger_seq bigint, trigger_t_onboard timestamptz,     -- ALM-05 start stamp (R10)
  after_epoch timestamptz, after_seq bigint, after_t_onboard timestamptz,  -- on ACK: the vehicle's last frame before the batch it was applied in (D-ALM-14, R2-10)
  processed_at timestamptz NOT NULL DEFAULT clock_timestamp(),   -- ALM-05 end stamp
  by_user text NOT NULL DEFAULT 'system', note text, alert boolean NOT NULL DEFAULT false,
  limits_sha256 text NOT NULL,                           -- = sha256sum mission/limits.yaml (ALM-08); bytes in limits_versions.limits_yaml
  set_sha256 text NOT NULL REFERENCES limits_versions);  -- the whole set that produced this row (EX-10, Q6)

CREATE INDEX alarm_events_by_alarm ON alarm_events (alarm_id, id);   -- views: newest row per alarm (P-pf21)

CREATE TABLE alarm_ack_request (                         -- D-ALM-14: API inserts, owner worker applies
  request_id uuid PRIMARY KEY, alarm_id uuid NOT NULL REFERENCES alarms, vehicle text NOT NULL,
  by_user text NOT NULL, note text NOT NULL CHECK (length(trim(note)) > 0),
  requested_at timestamptz NOT NULL DEFAULT clock_timestamp(), applied_at timestamptz,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','applied','superseded','rejected')),
  status_reason text);                                   -- superseded: alarm already ACKED; rejected: SAVEPOINT error (R2-01, R2-10)
CREATE INDEX alarm_ack_request_by_alarm ON alarm_ack_request (alarm_id, requested_at);   -- views: newest request (P-pf21)

CREATE TABLE commands (                                  -- current state; full history lives in audit_log
  cmd_id uuid PRIMARY KEY, vehicle text NOT NULL, name text NOT NULL, args jsonb NOT NULL,
  args_hash text NOT NULL, hazardous boolean NOT NULL, reason text, issued_by text NOT NULL,
  state text NOT NULL CHECK (state IN ('QUEUED','SENT','RECEIVED','VERIFIED','FAILED')),
  failure_reason text CHECK (failure_reason IN ('SEND_ERROR','REJECTED','NOT_RECEIVED','NOT_VERIFIED','ACK_INVALID')),
  queued_at timestamptz NOT NULL, sent_at timestamptz,   -- outcome is computed from failure_reason + late evidence (D-CMD-02)
  received_at timestamptz, verified_at timestamptz, failed_at timestamptz, late_verified_at timestamptz,
  late_ack_at timestamptz, late_ack_accepted boolean);   -- a tcack that arrived after NOT_RECEIVED (D-CMD-04, R2-04)

CREATE TABLE session_report (session_id bigint PRIMARY KEY REFERENCES test_session,
  report jsonb NOT NULL, csv text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp());

CREATE TABLE archive_object (                            -- D-ARC-04: inserted only after the re-read matches
  vehicle text NOT NULL, hour timestamptz NOT NULL, sha256 text NOT NULL, rows bigint NOT NULL,
  verified_at timestamptz NOT NULL, hot_deleted_at timestamptz,
  PRIMARY KEY (vehicle, hour));
