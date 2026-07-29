#!/usr/bin/env bash
set -Eeuo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: bash $0 <backup-directory>" >&2
  exit 2
fi

root_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
backup_dir="$(realpath "$1")"
suffix="$(date -u +%Y%m%d%H%M%S)-$$"
container="coop-restore-drill-$suffix"
network="$container"
db_volume="$container-db"
blob_volume="$container-blobs"
password="$(openssl rand -hex 24)"
secret_dir="$(mktemp -d)"

cleanup() {
  docker rm -f "$container" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
  docker volume rm "$db_volume" "$blob_volume" >/dev/null 2>&1 || true
  rm -rf -- "$secret_dir"
}
trap cleanup EXIT INT TERM

for required in COMPLETE SHA256SUMS manifest.env database.dump blobs.tar.gz secret-storage-verification.txt backup-secret-audit.json restore-consistency.json; do
  test -f "$backup_dir/$required" || {
    echo "Incomplete backup: missing $required" >&2
    exit 1
  }
done
if ! grep -qx 'secret_storage=PASS' "$backup_dir/secret-storage-verification.txt"; then
  echo "Backup has no valid secret storage evidence." >&2
  exit 1
fi
python3 "$root_dir/scripts/supply_secret_audit.py" backup \
  --directory "$backup_dir" >/dev/null
python3 -c 'import json,sys; assert json.load(open(sys.argv[1], encoding="utf-8"))["ok"] is True' \
  "$backup_dir/restore-consistency.json"
(
  cd "$backup_dir"
  sha256sum --check --strict SHA256SUMS
  pg_restore --list database.dump >/dev/null
  tar -tzf blobs.tar.gz >/dev/null
)

format="$(sed -n 's/^format=//p' "$backup_dir/manifest.env")"
if [ "$format" != "cooperative-clearing-backup-v2" ]; then
  echo "Unsupported backup format: $format" >&2
  exit 1
fi
backup_kind="$(sed -n 's/^backup_kind=//p' "$backup_dir/manifest.env")"
release="$(sed -n 's/^release=//p' "$backup_dir/manifest.env")"
release_material="$(sed -n 's/^release_material=//p' "$backup_dir/manifest.env")"
recovery_material="$(sed -n 's/^recovery_material=//p' "$backup_dir/manifest.env")"
release_manifest_sha256="$(sed -n 's/^release_manifest_sha256=//p' "$backup_dir/manifest.env")"
if [ "$backup_kind" = "FULL" ] &&
   { [ "$release_material" != "included-verified" ] ||
     [ "$recovery_material" != "included-encrypted" ]; }; then
  echo "FULL backup must include verified release and encrypted recovery material." >&2
  exit 1
fi
if [ "$release_material" = "included-verified" ]; then
  if [ -z "${COOP_RELEASE_PUBLIC_KEY:-}" ]; then
    echo "COOP_RELEASE_PUBLIC_KEY is required to verify backup release." >&2
    exit 1
  fi
  actual_manifest_sha256="$(sha256sum "$backup_dir/release/release-manifest.json" | cut -d' ' -f1)"
  if [ "$actual_manifest_sha256" != "$release_manifest_sha256" ]; then
    echo "Backup release manifest hash does not match manifest.env." >&2
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
  "${release_verification[@]}" >/dev/null
fi

docker network create "$network" >/dev/null
docker volume create "$db_volume" >/dev/null
docker volume create "$blob_volume" >/dev/null
docker run -d --name "$container" --network "$network" -e "POSTGRES_USER=coop_migrator" -e "POSTGRES_PASSWORD=$password" -e "POSTGRES_DB=restore_drill" -v "$db_volume:/var/lib/postgresql" postgres:18-alpine >/dev/null

ready_checks=0
for _ in $(seq 1 60); do
  if docker exec "$container" pg_isready -U coop_migrator -d restore_drill >/dev/null 2>&1; then
    ready_checks=$((ready_checks + 1))
    if [ "$ready_checks" -ge 3 ]; then
      break
    fi
  else
    ready_checks=0
  fi
  sleep 1
done
if [ "$ready_checks" -lt 3 ]; then
  echo "Restore drill PostgreSQL did not become stable." >&2
  exit 1
fi
docker exec "$container" psql -U coop_migrator -d restore_drill -v ON_ERROR_STOP=1 \
  -c 'CREATE ROLE coop_app NOLOGIN'
docker exec -i "$container" pg_restore -U coop_migrator -d restore_drill --exit-on-error --no-owner < "$backup_dir/database.dump"
docker exec "$container" psql -U coop_migrator -d restore_drill -v ON_ERROR_STOP=1 \
  -c "ALTER ROLE coop_app LOGIN PASSWORD '$password'"
restored_secret_status="$(
  docker exec -i "$container" psql -X -qAt -v ON_ERROR_STOP=1 \
    -U coop_migrator -d restore_drill -f - \
    < "$root_dir/infra/postgres/verify-secret-storage.sql"
)"
if [ "$restored_secret_status" != "secret_storage=PASS" ]; then
  echo "Restored database secret storage verification failed." >&2
  exit 1
fi

schema="$(docker exec "$container" psql -U coop_migrator -d restore_drill -Atc 'select version_num from alembic_version')"
table_count="$(docker exec "$container" psql -U coop_migrator -d restore_drill -Atc "select count(*) from information_schema.tables where table_schema not in ('pg_catalog','information_schema')")"
event_count="$(docker exec "$container" psql -U coop_migrator -d restore_drill -Atc 'select count(*) from journal.signed_events')"

docker run --rm -v "$blob_volume:/target" -v "$backup_dir:/backup:ro" postgres:18-alpine sh -ec 'tar -C /target -xzf /backup/blobs.tar.gz'
archive_files="$(tar -tzf "$backup_dir/blobs.tar.gz" | awk '!/\/$/ {count++} END {print count+0}')"
restored_files="$(docker run --rm -v "$blob_volume:/target:ro" postgres:18-alpine sh -ec "find /target -type f | wc -l")"
if [ "$archive_files" -ne "$restored_files" ]; then
  echo "Blob restore file count mismatch: archive=$archive_files restored=$restored_files" >&2
  exit 1
fi

for secret in node_signing_seed blob_encryption_key mfa_encryption_key; do
  test -f "$root_dir/secrets/$secret" || {
    echo "Installed recovery secret is missing: secrets/$secret" >&2
    exit 1
  }
  cp "$root_dir/secrets/$secret" "$secret_dir/$secret"
done
printf '%s\n' "$password" > "$secret_dir/postgres_app_password"
chmod 0755 "$secret_dir"
chmod 0444 "$secret_dir"/*
node_code="$(docker exec "$container" psql -U coop_migrator -d restore_drill -Atc 'select node_code from node.node_profiles order by created_at limit 1')"
test -n "$node_code" || {
  echo "Restored node profile is missing." >&2
  exit 1
}
consistency="$(
  docker run --rm --user 10001:10001 --read-only --network "$network" \
    --cap-drop ALL --security-opt no-new-privileges:true \
    -e COOP_ENVIRONMENT=dev \
    -e COOP_NODE_CODE="$node_code" \
    -e COOP_DATABASE_HOST="$container" \
    -e COOP_DATABASE_NAME=restore_drill \
    -e COOP_DATABASE_USER=coop_app \
    -e COOP_DATABASE_PASSWORD_FILE=/run/secrets/postgres_app_password \
    -v "$secret_dir/postgres_app_password:/run/secrets/postgres_app_password:ro" \
    -v "$secret_dir/node_signing_seed:/run/secrets/node_signing_seed:ro" \
    -v "$secret_dir/blob_encryption_key:/run/secrets/blob_encryption_key:ro" \
    -v "$secret_dir/mfa_encryption_key:/run/secrets/mfa_encryption_key:ro" \
    -v "$blob_volume:/var/lib/cooperative-clearing/blobs:ro" \
    "cooperative-clearing/backend:$release" \
    coopctl verify-restore-consistency
)"

printf 'restore_drill=PASS schema=%s tables=%s events=%s blob_files=%s consistency=%s\n' "$schema" "$table_count" "$event_count" "$restored_files" "$consistency"
