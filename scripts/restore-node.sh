#!/usr/bin/env bash
set -Eeuo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: COOP_RESTORE_CONFIRM=<backup-id> COOP_RECOVERY_CONFIRMED=yes $0 <backup-directory>" >&2
  exit 2
fi

root_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
backup_dir="$(realpath "$1")"
project="${COMPOSE_PROJECT_NAME:-cooperative-clearing}"
compose=(docker compose --project-directory "$root_dir" -f "$root_dir/compose.yaml")

for required in COMPLETE SHA256SUMS manifest.env database.dump blobs.tar.gz; do
  if [ ! -f "$backup_dir/$required" ]; then
    echo "Incomplete backup: missing $required" >&2
    exit 1
  fi
done

backup_id="$(sed -n 's/^backup_id=//p' "$backup_dir/manifest.env")"
backup_kind="$(sed -n 's/^backup_kind=//p' "$backup_dir/manifest.env")"
release="$(sed -n 's/^release=//p' "$backup_dir/manifest.env")"
release_material="$(sed -n 's/^release_material=//p' "$backup_dir/manifest.env")"
if [ -z "$backup_id" ] || [ "${COOP_RESTORE_CONFIRM:-}" != "$backup_id" ]; then
  echo "Set COOP_RESTORE_CONFIRM=$backup_id to authorize destructive restore." >&2
  exit 1
fi
if [ "${COOP_RECOVERY_CONFIRMED:-}" != "yes" ]; then
  echo "Install the matching protected secrets, then set COOP_RECOVERY_CONFIRMED=yes." >&2
  exit 1
fi
if [ "$backup_kind" = "FULL" ] && [ ! -f "$backup_dir/recovery.bundle.enc" ]; then
  echo "FULL backup is missing its encrypted recovery bundle." >&2
  exit 1
fi
if [ "$backup_kind" = "FULL" ] && [ "$release_material" != "included-verified" ]; then
  echo "FULL backup is missing its verified release bundle." >&2
  exit 1
fi
if [ "$release_material" = "included-verified" ]; then
  if [ -z "${COOP_RELEASE_PUBLIC_KEY:-}" ]; then
    echo "COOP_RELEASE_PUBLIC_KEY is required to restore the signed release." >&2
    exit 1
  fi
  release_verification=(
    python3
    "$root_dir/scripts/release_bundle.py"
    verify
    --bundle "$backup_dir/release"
    --public-key "$COOP_RELEASE_PUBLIC_KEY"
    --expected-release "$release"
    --load-images
  )
  if [ -n "${COOP_RELEASE_LICENSE_POLICY_SHA256:-}" ]; then
    release_verification+=(
      --expected-policy-sha256 "$COOP_RELEASE_LICENSE_POLICY_SHA256"
    )
  fi
  "${release_verification[@]}"
  if ! cmp -s "$root_dir/compose.yaml" "$backup_dir/release/node/compose.yaml"; then
    echo "Installed node payload does not match the recovery release." >&2
    exit 1
  fi
fi

(
  cd "$backup_dir"
  sha256sum --check --strict SHA256SUMS
  pg_restore --list database.dump >/dev/null
  tar -tzf blobs.tar.gz >/dev/null
)

env_file="$root_dir/.env"
temp_env="$(mktemp "$root_dir/.env.restore.XXXXXX")"
if [ -f "$env_file" ]; then
  awk -v release="$release" '
    BEGIN { found=0 }
    /^COOP_RELEASE=/ { print "COOP_RELEASE=" release; found=1; next }
    { print }
    END { if (!found) print "COOP_RELEASE=" release }
  ' "$env_file" > "$temp_env"
else
  printf 'COOP_RELEASE=%s\n' "$release" > "$temp_env"
fi
chmod go-rwx "$temp_env"
mv -- "$temp_env" "$env_file"
export COOP_RELEASE="$release"

mkdir -p "$root_dir/.operations"
report="$root_dir/.operations/restore-${backup_id}-$(date -u +%Y%m%dT%H%M%SZ).log"
exec > >(tee -a "$report") 2>&1

echo "restore_started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "backup_id=$backup_id"

"${compose[@]}" stop gateway api worker frontend >/dev/null || true
"${compose[@]}" up -d postgres

"${compose[@]}" exec -T postgres sh -ec '
  export PGPASSWORD="$(cat /run/secrets/postgres_migrator_password)"
  psql -h 127.0.0.1 -U coop_migrator -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS cooperative_clearing WITH (FORCE)" -c "CREATE DATABASE cooperative_clearing OWNER coop_migrator"
'

cat "$backup_dir/database.dump" | "${compose[@]}" exec -T postgres sh -ec '
  export PGPASSWORD="$(cat /run/secrets/postgres_migrator_password)"
  exec pg_restore -h 127.0.0.1 -U coop_migrator -d cooperative_clearing --exit-on-error --no-owner
'

docker run --rm -v "${project}_blob-data:/target" -v "$backup_dir:/backup:ro" postgres:18-alpine sh -ec 'find /target -mindepth 1 -delete; tar -C /target -xzf /backup/blobs.tar.gz'

"${compose[@]}" run --rm migrate
"${compose[@]}" run --rm init-node
"${compose[@]}" run --rm bootstrap-identity
"${compose[@]}" run --rm --no-deps api coopctl verify-restore-consistency
"${compose[@]}" run --rm --no-deps api coopctl verify-journal
"${compose[@]}" up -d api worker frontend gateway
bash "$root_dir/scripts/verify-stack.sh" "http://127.0.0.1:${COOP_HTTP_PORT:-8080}"

echo "restore_completed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "restore_report=$report"
