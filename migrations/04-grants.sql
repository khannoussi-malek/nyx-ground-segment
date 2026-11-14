SET ROLE gs_owner;

-- Grant matrix (RV-14). gs_owner owns every table and runs the initdb SQL and drop_chunks; services connect as gs_app.
GRANT SELECT, INSERT          ON events, limits_versions, alarm_events, session_report, audit_log TO gs_app;  -- history
GRANT SELECT, INSERT, UPDATE  ON seq_state, engine_state, alarms, commands, alarm_ack_request, archive_object TO gs_app;  -- current state
GRANT SELECT, INSERT, DELETE  ON telemetry TO gs_app;   -- DELETE only in the verified retention transaction (D-ARC-05)
GRANT SELECT, INSERT, UPDATE (ended_at, ended_by) ON test_session TO gs_app;                                  -- no DELETE
