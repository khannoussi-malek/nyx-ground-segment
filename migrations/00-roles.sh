#!/bin/bash
set -euo pipefail
psql -v ON_ERROR_STOP=1 -v owner_pw="$GS_OWNER_PASSWORD" -v app_pw="$GS_APP_PASSWORD" -v db="$POSTGRES_DB" \
  -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<'SQL'
CREATE ROLE gs_owner LOGIN PASSWORD :'owner_pw';
CREATE ROLE gs_app LOGIN PASSWORD :'app_pw';
ALTER DATABASE :"db" OWNER TO gs_owner;
SQL
