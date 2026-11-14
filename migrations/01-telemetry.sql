-- initdb mounts ./migrations over the image's own timescaledb install script, so create it here (superuser).
CREATE EXTENSION IF NOT EXISTS timescaledb;
SET ROLE gs_owner;

-- parameter columns are added by gs-migrate from the mission database
CREATE TABLE telemetry (
  t_onboard   timestamptz NOT NULL,
  vehicle     text        NOT NULL,
  seq         bigint      NOT NULL,
  t_received  timestamptz NOT NULL DEFAULT clock_timestamp(),
  mode        text        NOT NULL CHECK (mode IN ('SAFE','STANDBY','TEST','FAULT')),
  last_cmd_id uuid,
  PRIMARY KEY (vehicle, t_onboard, seq)
);
SELECT create_hypertable('telemetry', by_range('t_onboard', INTERVAL '1 hour'),
                         create_default_indexes => false);
CREATE INDEX telemetry_vehicle_time ON telemetry (vehicle, t_onboard DESC);
