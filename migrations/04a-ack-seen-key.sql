SET ROLE gs_owner;

-- P-pf43: the alarm's last event key the console showed when the operator acked (AlarmView.last.key);
-- the owner supersedes the request when the alarm's newest alarm_events key differs. NULL = old clients.
ALTER TABLE alarm_ack_request ADD COLUMN seen_key text;
