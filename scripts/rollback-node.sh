#!/usr/bin/env bash
set -Eeuo pipefail

root_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
state_file="$root_dir/.operations/previous-release.env"
compose=(docker compose --project-directory "$root_dir" -f "$root_dir/compose.yaml")

requested_release="${1:-}"
if [ -z "$requested_release" ]; then
  if [ ! -f "$state_file" ]; then
    echo "No previous release state is available." >&2
    exit 1
  fi
  requested_release="$(sed -n 's/^previous_release=//p' "$state_file")"
fi
if ! [[ "$requested_release" =~ ^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$ ]]; then
  echo "Invalid rollback release identifier." >&2
  exit 1
fi

for image in backend frontend gateway; do
  docker image inspect "cooperative-clearing/$image:$requested_release" >/dev/null || {
    echo "Rollback image is unavailable: cooperative-clearing/$image:$requested_release" >&2
    exit 1
  }
done

env_file="$root_dir/.env"
temp_file="$(mktemp "$root_dir/.env.rollback.XXXXXX")"
if [ -f "$env_file" ]; then
  awk -v release="$requested_release" '
    BEGIN { found=0 }
    /^COOP_RELEASE=/ { print "COOP_RELEASE=" release; found=1; next }
    { print }
    END { if (!found) print "COOP_RELEASE=" release }
  ' "$env_file" > "$temp_file"
else
  printf 'COOP_RELEASE=%s\n' "$requested_release" > "$temp_file"
fi
chmod go-rwx "$temp_file"
mv -- "$temp_file" "$env_file"
export COOP_RELEASE="$requested_release"

# Schema downgrade is deliberately forbidden here. Use restore-node.sh with the
# recorded pre-update backup when the old application is not expand-compatible.
"${compose[@]}" up -d api worker frontend gateway
if ! bash "$root_dir/scripts/verify-stack.sh" "http://127.0.0.1:${COOP_HTTP_PORT:-8080}"; then
  echo "Application rollback failed verification." >&2
  if [ -f "$state_file" ]; then
    backup_dir="$(sed -n 's/^preupdate_backup=//p' "$state_file")"
    echo "Restore the coordinated backup with scripts/restore-node.sh: $backup_dir" >&2
  fi
  exit 1
fi
"${compose[@]}" run --rm --no-deps api coopctl verify-journal

mkdir -p "$root_dir/.operations"
printf 'rolled_back_to=%s\nrolled_back_at=%s\n' "$requested_release" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$root_dir/.operations/last-rollback.env"
chmod go-rwx "$root_dir/.operations/last-rollback.env"
echo "Application rollback completed: $requested_release"
