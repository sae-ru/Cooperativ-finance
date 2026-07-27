#!/usr/bin/env bash
set -Eeuo pipefail

root_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
backup_root="${1:-${COOP_BACKUP_ROOT:-$root_dir/backups}}"
project="${COMPOSE_PROJECT_NAME:-cooperative-clearing}"
compose=(docker compose --project-directory "$root_dir" -f "$root_dir/compose.yaml")
python_bin="${PYTHON:-python3}"
runtime_setting() {
  "$python_bin" "$root_dir/scripts/runtime_environment.py" get \
    --root "$root_dir" --name "$1"
}

release="${COOP_RELEASE:-}"
if [ -z "$release" ] && [ -f "$root_dir/.env" ]; then
  release="$(sed -n 's/^COOP_RELEASE=//p' "$root_dir/.env" | tail -n 1)"
fi
release="${release:-0.1.0-dev}"
verified_release_bundle="${COOP_VERIFIED_RELEASE_BUNDLE:-$(runtime_setting COOP_VERIFIED_RELEASE_BUNDLE)}"
release_public_key="${COOP_RELEASE_PUBLIC_KEY:-$(runtime_setting COOP_RELEASE_PUBLIC_KEY)}"
policy_sha256="${COOP_RELEASE_LICENSE_POLICY_SHA256:-$(runtime_setting COOP_RELEASE_LICENSE_POLICY_SHA256)}"
release_material="external-required"
release_manifest_sha256="none"

mkdir -p "$backup_root"
backup_root="$(CDPATH= cd -- "$backup_root" && pwd)"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_id="node-$timestamp"
work_dir="$(mktemp -d "$backup_root/.${backup_id}.XXXXXX")"
final_dir="$backup_root/$backup_id"
services_stopped=0

cleanup() {
  status=$?
  if [ "$services_stopped" -eq 1 ]; then
    docker start "$api_container" "$worker_container" >/dev/null 2>&1 || true
  fi
  if [ "$status" -ne 0 ] && [ -n "$work_dir" ]; then
    rm -rf -- "$work_dir"
  fi
  exit "$status"
}
trap cleanup EXIT INT TERM

if [ -e "$final_dir" ]; then
  echo "Backup destination already exists: $final_dir" >&2
  exit 1
fi

"${compose[@]}" up -d postgres >/dev/null
if ! "${compose[@]}" exec -T postgres pg_isready -U coop_migrator -d cooperative_clearing >/dev/null; then
  echo "PostgreSQL is not ready." >&2
  exit 1
fi

api_container="$("${compose[@]}" ps -q api)"
worker_container="$("${compose[@]}" ps -q worker)"
if [ -z "$api_container" ] || [ -z "$worker_container" ] ||
   [ "$(docker inspect --format '{{.State.Running}}' "$api_container")" != "true" ] ||
   [ "$(docker inspect --format '{{.State.Running}}' "$worker_container")" != "true" ]; then
  echo "API and worker must be running before a coordinated backup." >&2
  exit 1
fi
"${compose[@]}" exec -T api coopctl verify-journal > "$work_dir/journal-verification.json"

if [ -n "$verified_release_bundle" ]; then
  verified_release_bundle="$(realpath "$verified_release_bundle")"
  if [ -z "$release_public_key" ]; then
    echo "COOP_RELEASE_PUBLIC_KEY is required to include a release in backup." >&2
    exit 1
  fi
  release_verification=(
    "$python_bin"
    "$root_dir/scripts/release_bundle.py"
    verify
    --bundle "$verified_release_bundle"
    --public-key "$release_public_key"
    --expected-release "$release"
  )
  if [ -n "$policy_sha256" ]; then
    release_verification+=(
      --expected-policy-sha256 "$policy_sha256"
    )
  fi
  "${release_verification[@]}" >/dev/null
  mkdir -p "$work_dir/release"
  cp -a "$verified_release_bundle/." "$work_dir/release/"
  release_material="included-verified"
  release_manifest_sha256="$(sha256sum "$work_dir/release/release-manifest.json" | cut -d' ' -f1)"
fi

# Quiesce every application writer so the DB and encrypted blob snapshot share one boundary.
docker stop "$api_container" "$worker_container" >/dev/null
services_stopped=1

"${compose[@]}" exec -T postgres sh -ec '
  export PGPASSWORD="$(cat /run/secrets/postgres_migrator_password)"
  exec pg_dump -h 127.0.0.1 -U coop_migrator -d cooperative_clearing --format=custom --compress=9 --no-owner
' > "$work_dir/database.dump"

docker run --rm -v "${project}_blob-data:/source:ro" -v "$work_dir:/backup" postgres:18-alpine sh -ec 'tar -C /source -czf /backup/blobs.tar.gz .'

"${compose[@]}" exec -T postgres psql -U coop_migrator -d cooperative_clearing   -Atc 'select version_num from alembic_version' > "$work_dir/schema.txt"

cp "$root_dir/compose.yaml" "$work_dir/compose.yaml"
if [ -f "$root_dir/.env" ]; then
  sed -E '/(PASSWORD|SECRET|TOKEN|PRIVATE|SIGNING|ENCRYPTION)/Id' "$root_dir/.env" > "$work_dir/runtime.env"
fi

recovery_material="external-required"
if [ -n "${COOP_ENCRYPTED_RECOVERY_BUNDLE:-}" ]; then
  recovery_bundle="$(realpath "${COOP_ENCRYPTED_RECOVERY_BUNDLE}")"
  if [ ! -f "$recovery_bundle" ]; then
    echo "Encrypted recovery bundle does not exist: $recovery_bundle" >&2
    exit 1
  fi
  cp "$recovery_bundle" "$work_dir/recovery.bundle.enc"
  recovery_material="included-encrypted"
fi
backup_kind="DATA_ONLY"
if [ "$recovery_material" = "included-encrypted" ] &&
   [ "$release_material" = "included-verified" ]; then
  backup_kind="FULL"
fi

cat > "$work_dir/manifest.env" <<EOF
format=cooperative-clearing-backup-v1
backup_id=$backup_id
backup_kind=$backup_kind
created_at=$timestamp
release=$release
schema=$(tr '\n' ' ' < "$work_dir/schema.txt" | sed 's/[[:space:]]\+/ /g')
database=database.dump
blobs=blobs.tar.gz
recovery_material=$recovery_material
release_material=$release_material
release_manifest_sha256=$release_manifest_sha256
EOF

(
  cd "$work_dir"
  files=(database.dump blobs.tar.gz journal-verification.json schema.txt compose.yaml manifest.env)
  if [ -f runtime.env ]; then
    files+=(runtime.env)
  fi
  if [ "$recovery_material" = "included-encrypted" ]; then
    files+=(recovery.bundle.enc)
  fi
  if [ "$release_material" = "included-verified" ]; then
    while IFS= read -r -d '' release_file; do
      files+=("${release_file#./}")
    done < <(find ./release -type f -print0 | sort -z)
  fi
  sha256sum "${files[@]}" > SHA256SUMS
  sha256sum --check --strict SHA256SUMS
  pg_restore --list database.dump >/dev/null
  tar -tzf blobs.tar.gz >/dev/null
)
printf '%s\n' "$timestamp" > "$work_dir/COMPLETE"
chmod -R go-rwx "$work_dir"
mv -- "$work_dir" "$final_dir"
work_dir=""

docker start "$api_container" "$worker_container" >/dev/null
services_stopped=0

echo "$final_dir"
