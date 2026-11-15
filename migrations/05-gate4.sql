SET ROLE gs_owner;

-- Gate 4 (T4-a): the archive row names the hour's range, its last scrub and the latest verified version.
-- The table is empty at initdb; the result equals design §3.2 plus `version`.
ALTER TABLE archive_object
  ADD COLUMN t_min timestamptz NOT NULL,
  ADD COLUMN t_max timestamptz NOT NULL,
  ADD COLUMN last_checked_at timestamptz,
  ADD COLUMN version int NOT NULL DEFAULT 0,
  ADD CHECK (t_min <= t_max),
  ADD CHECK (version >= 0);
