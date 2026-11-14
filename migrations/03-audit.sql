SET ROLE gs_owner;

CREATE TABLE audit_log (
  id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  at      timestamptz NOT NULL DEFAULT now(),
  actor   text NOT NULL, action text NOT NULL, reason text,
  details jsonb NOT NULL DEFAULT '{}'
);
REVOKE ALL ON audit_log FROM PUBLIC;
GRANT SELECT, INSERT ON audit_log TO gs_app;
CREATE FUNCTION audit_log_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'audit_log is append-only (% blocked)', TG_OP; END $$;
CREATE TRIGGER audit_no_update_delete BEFORE UPDATE OR DELETE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();
CREATE TRIGGER audit_no_truncate BEFORE TRUNCATE ON audit_log
  FOR EACH STATEMENT EXECUTE FUNCTION audit_log_immutable();
