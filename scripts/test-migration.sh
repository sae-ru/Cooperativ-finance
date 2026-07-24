#!/usr/bin/env bash
set -Eeuo pipefail

root_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
previous_revision="${COOP_MIGRATION_FROM:-0017_peer_reservations}"
expected_head="${COOP_MIGRATION_HEAD:-0021_logistics_contacts}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
project="${COOP_MIGRATION_PROJECT:-coop-migration-${timestamp,,}}"
report="${COOP_MIGRATION_REPORT:-$root_dir/evidence/migration-$timestamp.json}"
compose=(
  docker compose
  --project-name "$project"
  --project-directory "$root_dir"
  -f "$root_dir/compose.yaml"
  --profile test
)

if [[ ! "$previous_revision" =~ ^[a-zA-Z0-9_]+$ ]] ||
   [[ ! "$expected_head" =~ ^[a-zA-Z0-9_]+$ ]]; then
  echo "Migration revisions may contain only letters, digits, and underscore" >&2
  exit 2
fi

cleanup() {
  "${compose[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

if [ ! -s "$root_dir/secrets/postgres_migrator_password.txt" ]; then
  bash "$root_dir/scripts/bootstrap-node.sh"
fi

if [ "${COOP_MIGRATION_SKIP_BUILD:-0}" != "1" ]; then
  "${compose[@]}" build migrate-test backend-tests
fi
"${compose[@]}" up --detach postgres-test

ready=0
for _attempt in $(seq 1 60); do
  if "${compose[@]}" exec -T postgres-test     pg_isready -q -U coop_migrator -d cooperative_clearing_test; then
    ready=1
    break
  fi
  sleep 2
done
if [ "$ready" != "1" ]; then
  "${compose[@]}" ps --all >&2
  "${compose[@]}" logs --no-color postgres-test >&2
  echo "Migration test database did not become ready" >&2
  exit 1
fi

psql_value() {
  "${compose[@]}" exec -T postgres-test     psql -X -v ON_ERROR_STOP=1       -U coop_migrator       -d cooperative_clearing_test       -Atc "$1" |
    tr -d '\r'
}

run_migration() {
  "${compose[@]}" run --rm --no-deps migrate-test     alembic "$@"
}

run_application() {
  "${compose[@]}" run --rm --no-deps backend-tests "$@"
}

run_migration upgrade "$previous_revision"
actual_previous="$(psql_value "SELECT version_num FROM alembic_version")"
if [ "$actual_previous" != "$previous_revision" ]; then
  echo "Expected previous revision $previous_revision, got $actual_previous" >&2
  exit 1
fi

run_application coopctl init-node
run_application coopctl bootstrap-identity

identity_before="$(psql_value "
  SELECT concat_ws(
    ':',
    (SELECT count(*) FROM node.node_profiles),
    (SELECT count(*) FROM identity.users),
    (SELECT count(*) FROM identity.members),
    (SELECT count(*) FROM identity.memberships),
    (SELECT count(*) FROM identity.role_assignments)
  )")"
profile_before="$(psql_value "
  SELECT concat_ws(':', id, node_code, environment)
  FROM node.node_profiles
  ORDER BY node_code")"
tables_before="$(psql_value "
  SELECT count(*)
  FROM information_schema.tables
  WHERE table_schema NOT IN ('pg_catalog', 'information_schema')")"

run_migration upgrade head
actual_head="$(psql_value "SELECT version_num FROM alembic_version")"
if [ "$actual_head" != "$expected_head" ]; then
  echo "Expected head $expected_head, got $actual_head" >&2
  exit 1
fi

identity_after="$(psql_value "
  SELECT concat_ws(
    ':',
    (SELECT count(*) FROM node.node_profiles),
    (SELECT count(*) FROM identity.users),
    (SELECT count(*) FROM identity.members),
    (SELECT count(*) FROM identity.memberships),
    (SELECT count(*) FROM identity.role_assignments)
  )")"
profile_after="$(psql_value "
  SELECT concat_ws(':', id, node_code, environment)
  FROM node.node_profiles
  ORDER BY node_code")"
tables_after="$(psql_value "
  SELECT count(*)
  FROM information_schema.tables
  WHERE table_schema NOT IN ('pg_catalog', 'information_schema')")"
critical_tables="$(psql_value "
  SELECT count(*)
  FROM information_schema.tables
  WHERE table_schema = 'federation'
    AND table_name IN (
      'federated_clearing_cycles',
      'inter_node_obligations',
      'federated_commit_certificates',
      'federated_clearing_proofs'
    )")"

if [ "$identity_before" != "$identity_after" ] ||
   [ "$profile_before" != "$profile_after" ]; then
  echo "Previous-release identity data changed during upgrade" >&2
  exit 1
fi
if [ "$critical_tables" != "4" ] ||
   [ "$tables_after" -le "$tables_before" ]; then
  echo "Head migration did not install the expected clearing schema" >&2
  exit 1
fi

run_application coopctl init-node
run_application coopctl bootstrap-identity
identity_idempotent="$(psql_value "
  SELECT concat_ws(
    ':',
    (SELECT count(*) FROM node.node_profiles),
    (SELECT count(*) FROM identity.users),
    (SELECT count(*) FROM identity.members),
    (SELECT count(*) FROM identity.memberships),
    (SELECT count(*) FROM identity.role_assignments)
  )")"
if [ "$identity_after" != "$identity_idempotent" ]; then
  echo "Post-migration initialization is not idempotent" >&2
  exit 1
fi

run_migration downgrade "$previous_revision"
actual_downgrade="$(psql_value "SELECT version_num FROM alembic_version")"
profile_downgrade="$(psql_value "
  SELECT concat_ws(':', id, node_code, environment)
  FROM node.node_profiles
  ORDER BY node_code")"
if [ "$actual_downgrade" != "$previous_revision" ] ||
   [ "$profile_downgrade" != "$profile_before" ]; then
  echo "Recovery downgrade did not preserve previous-release data" >&2
  exit 1
fi

run_migration upgrade head
actual_reupgrade="$(psql_value "SELECT version_num FROM alembic_version")"
profile_reupgrade="$(psql_value "
  SELECT concat_ws(':', id, node_code, environment)
  FROM node.node_profiles
  ORDER BY node_code")"
if [ "$actual_reupgrade" != "$expected_head" ] ||
   [ "$profile_reupgrade" != "$profile_before" ]; then
  echo "Re-upgrade did not reproduce the expected state" >&2
  exit 1
fi

mkdir -p "$(dirname -- "$report")"
cat > "$report" <<EOF
{
  "format": "cooperative-clearing-migration-evidence-v1",
  "generated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "status": "passed",
  "from_revision": "$actual_previous",
  "to_revision": "$actual_head",
  "downgrade_revision": "$actual_downgrade",
  "reupgrade_revision": "$actual_reupgrade",
  "identity_counts": "$identity_after",
  "tables_before": $tables_before,
  "tables_after": $tables_after,
  "critical_head_tables": $critical_tables
}
EOF
printf '%s\n' "$report"
