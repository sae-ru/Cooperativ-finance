#!/usr/bin/env bash
set -Eeuo pipefail

root_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
project="${COOP_OBSERVABILITY_PROJECT:-coop-observability-${timestamp,,}}"
port="${COOP_OBSERVABILITY_PORT:-18088}"
destination="${COOP_OBSERVABILITY_EVIDENCE_ROOT:-$root_dir/evidence}/local-observability-$timestamp"
expected_schema="0039_participant_address_events"
password_file="$root_dir/secrets/bootstrap_security_password"
compose=(
  docker compose
  --project-name "$project"
  --project-directory "$root_dir"
  -f "$root_dir/compose.yaml"
  -f "$root_dir/compose.observability-test.yaml"
  --profile observability
)

if [[ ! "$port" =~ ^[0-9]+$ ]] || [ "$port" -lt 1024 ] || [ "$port" -gt 65535 ]; then
  echo "COOP_OBSERVABILITY_PORT must be between 1024 and 65535" >&2
  exit 2
fi

cleanup() {
  if [ "${KEEP_LOCAL_OBSERVABILITY_STACK:-0}" != "1" ]; then
    COOP_HTTP_PORT="$port" "${compose[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

mkdir -p "$destination"
export COOP_OBSERVABILITY_EVIDENCE_DIR="$destination"
if [ ! -s "$root_dir/secrets/postgres_migrator_password" ] || [ ! -s "$password_file" ]; then
  bash "$root_dir/scripts/bootstrap-node.sh"
fi
python "$root_dir/scripts/operational_status.py" probe --root "$root_dir" >/dev/null

COOP_HTTP_PORT="$port" "${compose[@]}" down --volumes --remove-orphans >/dev/null
if [ "${COOP_OBSERVABILITY_SKIP_BUILD:-0}" != "1" ]; then
  COOP_HTTP_PORT="$port" "${compose[@]}" build api frontend gateway
fi
COOP_HTTP_PORT="$port" "${compose[@]}" up --detach --wait gateway worker

for network in edge app web data; do
  value="$(docker network inspect "${project}_${network}" --format '{{.Internal}}')"
  if [ "$value" != "true" ]; then
    echo "Network ${project}_${network} is not internal" >&2
    exit 1
  fi
done

if COOP_HTTP_PORT="$port" "${compose[@]}" exec -T gateway \
  wget -q -T 3 -O /dev/null http://198.51.100.1; then
  echo "Gateway unexpectedly reached a non-local address" >&2
  exit 1
fi

cat > "$destination/network-isolation.json" <<EOF
{
  "format": "cooperative-clearing-network-isolation-v1",
  "networks": {
    "edge": true,
    "app": true,
    "web": true,
    "data": true
  },
  "egress_probe": "BLOCKED"
}
EOF


COOP_HTTP_PORT="$port" "${compose[@]}" logs --no-color --tail 500 \
  api worker gateway > "$destination/runtime.log"

COOP_HTTP_PORT="$port" "${compose[@]}" run --rm --no-deps observability-probe \
  python /workspace/scripts/local_observability_probe.py \
  --base-url http://gateway:8080 \
  --allow-internal-host gateway \
  --login security \
  --password-file /run/secrets/operator_password \
  --expected-schema "$expected_schema" \
  --network-evidence /evidence/network-isolation.json \
  --logs /evidence/runtime.log \
  --report - > "$destination/report.json"

(
  cd "$destination"
  find . -maxdepth 1 -type f ! -name SHA256SUMS -printf '%f\n' |
    sort |
    xargs sha256sum > SHA256SUMS
)
printf '%s\n' "$destination"