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

cleanup() {
  docker rm -f "$container" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
  docker volume rm "$db_volume" "$blob_volume" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

for required in COMPLETE SHA256SUMS manifest.env database.dump blobs.tar.gz; do
  test -f "$backup_dir/$required" || {
    echo "Incomplete backup: missing $required" >&2
    exit 1
  }
done
(
  cd "$backup_dir"
  sha256sum --check --strict SHA256SUMS
  pg_restore --list database.dump >/dev/null
  tar -tzf blobs.tar.gz >/dev/null
)

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

printf 'restore_drill=PASS schema=%s tables=%s events=%s blob_files=%s\n' "$schema" "$table_count" "$event_count" "$restored_files"
