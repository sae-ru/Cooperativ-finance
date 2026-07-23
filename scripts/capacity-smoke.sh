#!/usr/bin/env bash
set -Eeuo pipefail

release="${COOP_RELEASE:-0.1.0-dev}"

exec docker run --rm --network host \
  "cooperative-clearing/backend:$release" \
  python -m cooperative_clearing.tools.capacity \
  --base-url "${COOP_CAPACITY_BASE_URL:-http://127.0.0.1:8080}" \
  --endpoint "${COOP_CAPACITY_ENDPOINT:-/health/live}" \
  --requests "${COOP_CAPACITY_REQUESTS:-500}" \
  --concurrency "${COOP_CAPACITY_CONCURRENCY:-20}" \
  --max-error-rate "${COOP_CAPACITY_MAX_ERROR_RATE:-0}" \
  --max-p95-ms "${COOP_CAPACITY_MAX_P95_MS:-250}" \
  --min-rps "${COOP_CAPACITY_MIN_RPS:-10}" \
  "$@"
