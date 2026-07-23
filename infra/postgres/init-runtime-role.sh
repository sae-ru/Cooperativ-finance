#!/bin/sh
set -eu

app_password="$(cat /run/secrets/postgres_app_password)"

psql --set=ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=app_password="$app_password" <<'SQL'
SELECT format('CREATE ROLE coop_app LOGIN PASSWORD %L', :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'coop_app')
\gexec
SELECT format('ALTER ROLE coop_app WITH LOGIN PASSWORD %L', :'app_password')
\gexec
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL
