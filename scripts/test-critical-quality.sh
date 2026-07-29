#!/usr/bin/env bash
set -Eeuo pipefail

root_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
repeats="${1:-${COOP_CRITICAL_TEST_REPEATS:-3}}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
destination="${COOP_QUALITY_EVIDENCE_ROOT:-$root_dir/evidence}/quality-$timestamp"
project="${COOP_QUALITY_PROJECT:-coop-quality-${timestamp,,}}"
compose=(
  docker compose
  --project-name "$project"
  --project-directory "$root_dir"
  -f "$root_dir/compose.yaml"
  --profile test
)

if [[ ! "$repeats" =~ ^[1-9][0-9]*$ ]] ||
   [ "$repeats" -gt 20 ]; then
  echo "Repeat count must be an integer between 1 and 20" >&2
  exit 2
fi

cleanup() {
  "${compose[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

mkdir -p "$destination"
started_epoch="$(date +%s)"
python "$root_dir/scripts/openapi_compat.py"   --baseline "$root_dir/infra/contracts/openapi-0.1.0.json"   --current "$root_dir/backend/openapi.json"   --mirror "$root_dir/frontend/openapi.json"   --report "$destination/openapi-compatibility.json"   >/dev/null

if [ ! -s "$root_dir/secrets/postgres_migrator_password" ]; then
  bash "$root_dir/scripts/bootstrap-node.sh"
fi

if [ "${COOP_QUALITY_SKIP_BUILD:-0}" != "1" ]; then
  "${compose[@]}" build migrate-test backend-tests
fi

COOP_MIGRATION_SKIP_BUILD=1 COOP_MIGRATION_REPORT="$destination/migration.json"   bash "$root_dir/scripts/test-migration.sh" >/dev/null

"${compose[@]}" up --detach postgres-test
"${compose[@]}" run --rm migrate-test alembic upgrade head

tests=(
  tests/unit/test_clearing_engine.py
  tests/unit/test_federated_clearing.py
  tests/unit/test_command_assurance_registry.py
  tests/integration/test_commodity_rights_flow.py
  tests/integration/test_clearing_concurrency.py
  tests/integration/test_crisis_flow.py
  tests/integration/test_crisis_concurrency.py
  tests/integration/test_federation_flow.py
  tests/integration/test_solidarity_concurrency.py
  tests/integration/test_risk_flow.py
  tests/integration/test_compensation_flow.py
  tests/integration/test_compensation_demo.py
  tests/integration/test_identity_and_auth.py::test_admin_commands_are_idempotent_and_privileged_roles_need_approval
  tests/integration/test_identity_api.py
  tests/integration/test_identity_security_flow.py
  tests/integration/test_participant_address_book.py
  tests/integration/test_custody_continuity_flow.py
  tests/integration/test_trust_appeal.py
  tests/integration/test_signed_journal_responsibility.py::test_database_rejects_event_without_signature_and_outbox
  tests/integration/test_signed_journal_responsibility.py::test_worker_crash_and_concurrent_restart_deliver_once
  tests/integration/test_signed_journal_responsibility.py::test_concurrent_commands_preserve_node_sequence
  tests/integration/test_signed_journal_responsibility.py::test_critical_event_requires_signed_evidence_and_exposure_snapshot
)

for round in $(seq 1 "$repeats"); do
  "${compose[@]}" run --rm --no-deps backend-tests     pytest -q "${tests[@]}"     2>&1 | tee "$destination/critical-tests-round-$round.txt"
done

"${compose[@]}" run --rm --no-deps backend-tests   coopctl verify-journal > "$destination/journal-verification.json"

duration_seconds="$(( $(date +%s) - started_epoch ))"
cat > "$destination/manifest.json" <<EOF
{
  "format": "cooperative-clearing-critical-quality-v1",
  "generated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "status": "passed",
  "repeat_count": $repeats,
  "duration_seconds": $duration_seconds,
  "previous_schema": "0038_atomic_event_outbox",
  "current_schema": "0039_participant_address_events",
  "openapi_baseline": "0.1.0"
}
EOF

(
  cd "$destination"
  find . -maxdepth 1 -type f ! -name SHA256SUMS -printf '%f\n' |
    sort |
    xargs sha256sum > SHA256SUMS
)
printf '%s\n' "$destination"
