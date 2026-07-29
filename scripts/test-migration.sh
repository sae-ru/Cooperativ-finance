#!/usr/bin/env bash
set -Eeuo pipefail

root_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
previous_revision="${COOP_MIGRATION_FROM:-0038_atomic_event_outbox}"
expected_head="${COOP_MIGRATION_HEAD:-0039_participant_address_events}"
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

if [ ! -s "$root_dir/secrets/postgres_migrator_password" ]; then
  bash "$root_dir/scripts/bootstrap-node.sh"
fi

if [ "${COOP_MIGRATION_SKIP_BUILD:-0}" != "1" ]; then
  "${compose[@]}" build migrate-test backend-tests
fi
"${compose[@]}" up --detach postgres-test

ready=0
for _attempt in $(seq 1 60); do
  if "${compose[@]}" exec -T postgres-test     pg_isready -q -h postgres-test -U coop_migrator -d cooperative_clearing_test; then
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
  WHERE table_schema = 'risk'
    AND table_name = 'compensation_transfers'")"
executed_amount_columns="$(psql_value "
  SELECT count(*)
  FROM information_schema.columns
  WHERE table_schema = 'risk'
    AND table_name = 'exposure_commitments'
    AND column_name = 'executed_amount'
    AND is_nullable = 'NO'")"
atomicity_trigger="$(psql_value "
  SELECT count(*)
  FROM pg_trigger
  WHERE tgname = 'trg_signed_event_delivery_atomicity'
    AND tgenabled = 'O'")"
outbox_event_constraint="$(psql_value "
  SELECT count(*)
  FROM pg_constraint
  WHERE conname = 'uq_outbox_event'
    AND conrelid = 'journal.outbox_messages'::regclass")"
node_signature_index="$(psql_value "
  SELECT count(*)
  FROM pg_indexes
  WHERE schemaname = 'journal'
    AND tablename = 'event_signatures'
    AND indexname = 'uq_event_signatures_node_event'")"
address_event_trigger="$(psql_value "
  SELECT count(*)
  FROM pg_trigger
  WHERE tgname = 'trg_participant_address_event_link'
    AND tgenabled = 'O'")"
address_event_constraint="$(psql_value "
  SELECT count(*)
  FROM pg_constraint
  WHERE conrelid = 'identity.participant_addresses'::regclass
    AND contype = 'c'
    AND pg_get_constraintdef(oid) LIKE '%event_tracking_required%'")"
address_event_index="$(psql_value "
  SELECT count(*)
  FROM pg_indexes
  WHERE schemaname = 'identity'
    AND tablename = 'participant_addresses'
    AND indexname = 'ix_participant_addresses_last_event_id'")"

if [ "$identity_before" != "$identity_after" ] ||
   [ "$profile_before" != "$profile_after" ]; then
  echo "Previous-release identity data changed during upgrade" >&2
  exit 1
fi
if [ "$critical_tables" != "1" ] ||
   [ "$executed_amount_columns" != "1" ] ||
   [ "$atomicity_trigger" != "1" ] ||
   [ "$outbox_event_constraint" != "1" ] ||
   [ "$node_signature_index" != "1" ] ||
   [ "$address_event_trigger" != "1" ] ||
   [ "$address_event_constraint" != "1" ] ||
   [ "$address_event_index" != "1" ]; then
  echo "Head migration did not install the expected event assurance schema" >&2
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

accepted_events_before="$(psql_value "SELECT count(*) FROM journal.signed_events")"
run_application coopctl seed-demo
accepted_events_after="$(psql_value "SELECT count(*) FROM journal.signed_events")"
accepted_hash_after="$(psql_value "SELECT event_hash FROM journal.signed_events ORDER BY local_sequence DESC LIMIT 1")"
if [ "$accepted_events_after" -le "$accepted_events_before" ] || [ -z "$accepted_hash_after" ]; then
  echo "Post-backup acceptance probe did not append a signed business event" >&2
  exit 1
fi

run_migration downgrade "$previous_revision"
actual_downgrade="$(psql_value "SELECT version_num FROM alembic_version")"
profile_downgrade="$(psql_value "
  SELECT concat_ws(':', id, node_code, environment)
  FROM node.node_profiles
  ORDER BY node_code")"
atomicity_downgrade="$(psql_value "
  SELECT count(*)
  FROM pg_trigger
  WHERE tgname = 'trg_signed_event_delivery_atomicity'")"
address_event_downgrade="$(psql_value "
  SELECT count(*)
  FROM pg_trigger
  WHERE tgname = 'trg_participant_address_event_link'")"
accepted_events_downgrade="$(psql_value "SELECT count(*) FROM journal.signed_events")"
accepted_hash_downgrade="$(psql_value "SELECT event_hash FROM journal.signed_events ORDER BY local_sequence DESC LIMIT 1")"
if [ "$actual_downgrade" != "$previous_revision" ] ||
   [ "$profile_downgrade" != "$profile_before" ] ||
   [ "$atomicity_downgrade" != "1" ] ||
   [ "$address_event_downgrade" != "0" ] ||
   [ "$accepted_events_downgrade" != "$accepted_events_after" ] ||
   [ "$accepted_hash_downgrade" != "$accepted_hash_after" ]; then
  echo "Recovery downgrade did not preserve previous-release data" >&2
  exit 1
fi

run_migration upgrade head
actual_reupgrade="$(psql_value "SELECT version_num FROM alembic_version")"
profile_reupgrade="$(psql_value "
  SELECT concat_ws(':', id, node_code, environment)
  FROM node.node_profiles
  ORDER BY node_code")"
atomicity_reupgrade="$(psql_value "
  SELECT count(*)
  FROM pg_trigger
  WHERE tgname = 'trg_signed_event_delivery_atomicity'
    AND tgenabled = 'O'")"
address_event_reupgrade="$(psql_value "
  SELECT count(*)
  FROM pg_trigger
  WHERE tgname = 'trg_participant_address_event_link'
    AND tgenabled = 'O'")"
if [ "$actual_reupgrade" != "$expected_head" ] ||
   [ "$profile_reupgrade" != "$profile_before" ] ||
   [ "$atomicity_reupgrade" != "1" ] ||
   [ "$address_event_reupgrade" != "1" ]; then
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
  "critical_head_tables": $critical_tables,
  "executed_amount_columns": $executed_amount_columns,
  "atomicity_trigger": $atomicity_trigger,
  "outbox_event_constraint": $outbox_event_constraint,
  "node_signature_index": $node_signature_index,
  "address_event_trigger": $address_event_trigger,
  "address_event_constraint": $address_event_constraint,
  "address_event_index": $address_event_index,
  "post_backup_events_before": $accepted_events_before,
  "post_backup_events_after": $accepted_events_after,
  "post_backup_events_downgrade": $accepted_events_downgrade,
  "post_backup_last_hash": "$accepted_hash_downgrade",
  "atomicity_downgrade": $atomicity_downgrade,
  "address_event_downgrade": $address_event_downgrade,
  "atomicity_reupgrade": $atomicity_reupgrade,
  "address_event_reupgrade": $address_event_reupgrade
}
EOF
printf '%s\n' "$report"
