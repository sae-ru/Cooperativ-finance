#!/usr/bin/env bash
set -Eeuo pipefail

root_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
output_root="${1:-${COOP_EVIDENCE_ROOT:-$root_dir/evidence}}"
python_bin="${PYTHON:-python3}"
environment="$("$python_bin" "$root_dir/scripts/runtime_environment.py" resolve --root "$root_dir")"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
destination="$output_root/release-$timestamp"
compose=(docker compose --project-directory "$root_dir" -f "$root_dir/compose.yaml")
git_status="$(git -C "$root_dir" status --porcelain=v1)"

if [ "$environment" = "production" ] && [ "${COOP_ALLOW_DIRTY_EVIDENCE:-0}" = "1" ]; then
  echo "Production evidence cannot override the clean-worktree requirement" >&2
  exit 1
fi
if [ "$environment" = "production" ] && [ -n "$git_status" ]; then
  echo "Production evidence requires a clean Git worktree" >&2
  exit 1
fi

mkdir -p "$destination"
cat > "$destination/manifest.json" <<EOF
{"format":"cooperative-clearing-production-evidence-v1","generated_at":"$(date -u +%Y-%m-%dT%H:%M:%SZ)","environment":"$environment","contains_logs":false,"contains_raw_pii":false}
EOF
git -C "$root_dir" rev-parse HEAD > "$destination/git-revision.txt"
printf '%s\n' "$git_status" > "$destination/git-status.txt"
"${compose[@]}" ps > "$destination/stack.txt"
"${compose[@]}" images > "$destination/images.txt"
"${compose[@]}" exec -T api coopctl diagnostics > "$destination/diagnostics.json"
"${compose[@]}" exec -T api coopctl verify-journal > "$destination/journal-verification.json"
curl --fail --silent --show-error http://127.0.0.1:8080/health/live > "$destination/health-live.json"
curl --fail --silent --show-error http://127.0.0.1:8080/health/ready > "$destination/health-ready.json"
curl --fail --silent --show-error http://127.0.0.1:8080/api/v1/system/status > "$destination/api-v1-system-status.json"

: > "$destination/openapi-sha256.txt"
for relative in backend/openapi.json frontend/openapi.json; do
  if [ -f "$root_dir/$relative" ]; then
    hash="$(sha256sum "$root_dir/$relative" | awk '{print $1}')"
    printf '%s  %s\n' "$hash" "$relative" >> "$destination/openapi-sha256.txt"
  fi
done

python "$root_dir/scripts/openapi_compat.py" \
  --baseline "$root_dir/infra/contracts/openapi-0.1.0.json" \
  --current "$root_dir/backend/openapi.json" \
  --mirror "$root_dir/frontend/openapi.json" \
  --report "$destination/openapi-compatibility.json" \
  >/dev/null

printf 'complete_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$destination/COMPLETE"
(
  cd "$destination"
  find . -maxdepth 1 -type f ! -name SHA256SUMS -printf '%f\n' |
    sort |
    xargs sha256sum > SHA256SUMS
)

printf '%s\n' "$destination"
