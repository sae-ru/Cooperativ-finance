#!/usr/bin/env bash
set -Eeuo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "Usage: $0 <target-release> [verified-offline-bundle]" >&2
  exit 2
fi

root_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
target_release="$1"
bundle_dir="${2:-}"
compose=(docker compose --project-directory "$root_dir" -f "$root_dir/compose.yaml")
python_bin="${PYTHON:-python3}"
runtime_setting() {
  "$python_bin" "$root_dir/scripts/runtime_environment.py" get \
    --root "$root_dir" --name "$1"
}
environment="$("$python_bin" "$root_dir/scripts/runtime_environment.py" resolve --root "$root_dir")"
release_public_key="${COOP_RELEASE_PUBLIC_KEY:-$(runtime_setting COOP_RELEASE_PUBLIC_KEY)}"
policy_sha256="${COOP_RELEASE_LICENSE_POLICY_SHA256:-$(runtime_setting COOP_RELEASE_LICENSE_POLICY_SHA256)}"
current_verified_bundle="${COOP_VERIFIED_RELEASE_BUNDLE:-$(runtime_setting COOP_VERIFIED_RELEASE_BUNDLE)}"

if ! [[ "$target_release" =~ ^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$ ]]; then
  echo "Invalid release identifier." >&2
  exit 1
fi

current_release="${COOP_RELEASE:-}"
if [ -z "$current_release" ] && [ -f "$root_dir/.env" ]; then
  current_release="$(sed -n 's/^COOP_RELEASE=//p' "$root_dir/.env" | tail -n 1)"
fi
current_release="${current_release:-0.1.0-dev}"
if [ "$current_release" = "$target_release" ]; then
  echo "Release $target_release is already selected." >&2
  exit 1
fi

failpoint="${COOP_UPDATE_FAILPOINT:-none}"
case "$failpoint" in
  none|after-release-switch|after-migration|after-startup) ;;
  *)
    echo "Unsupported COOP_UPDATE_FAILPOINT: $failpoint" >&2
    exit 1
    ;;
esac
if [ "$environment" = "production" ] && [ "$failpoint" != "none" ]; then
  echo "Update faultpoints are forbidden in production." >&2
  exit 1
fi
if [ "$environment" = "production" ] && [ -z "$bundle_dir" ]; then
  echo "Production update requires a signed offline bundle." >&2
  exit 1
fi
if [ "$environment" = "production" ] && [ "${COOP_UPDATE_BUILD:-no}" = "yes" ]; then
  echo "Production update cannot build images from source." >&2
  exit 1
fi
if [ "$environment" = "production" ] && { [ -z "$release_public_key" ] || [ -z "$policy_sha256" ]; }; then
  echo "Production update requires the persisted release public key and license-policy SHA-256." >&2
  exit 1
fi
if [ "$environment" = "production" ] && [ -z "$current_verified_bundle" ]; then
  echo "Production update requires the verified current release bundle for rollback." >&2
  exit 1
fi

read_database_schema() {
  schema_output="$("${compose[@]}" run --rm --no-deps api alembic current)"
  schema="$(printf '%s\n' "$schema_output" | sed -n -e 's/^\([0-9A-Za-z][0-9A-Za-z_-]*\) (head)$/\1/p' -e 's/^\([0-9A-Za-z][0-9A-Za-z_-]*\)$/\1/p')"
  if ! [[ "$schema" =~ ^[0-9A-Za-z][0-9A-Za-z_-]{0,127}$ ]]; then
    echo "Cannot determine one current database schema revision." >&2
    return 1
  fi
  printf '%s\n' "$schema"
}

current_schema="$(read_database_schema)"
target_schema=""
if [ -n "$bundle_dir" ]; then
  bundle_dir="$(realpath "$bundle_dir")"
  if [ -z "$release_public_key" ]; then
    echo "COOP_RELEASE_PUBLIC_KEY must name the independently provisioned public key." >&2
    exit 1
  fi
  if [ -z "$current_verified_bundle" ]; then
    echo "Signed update requires the verified current release bundle for rollback." >&2
    exit 1
  fi
  if [ -n "$current_verified_bundle" ]; then
    current_verified_bundle="$(realpath "$current_verified_bundle")"
    previous_verification=(
      "$python_bin"
      "$root_dir/scripts/release_bundle.py"
      verify
      --bundle "$current_verified_bundle"
      --public-key "$release_public_key"
      --expected-release "$current_release"
    )
    if [ -n "$policy_sha256" ]; then
      previous_verification+=(--expected-policy-sha256 "$policy_sha256")
    fi
    "${previous_verification[@]}" >/dev/null
  fi
  verification=(
    "$python_bin"
    "$root_dir/scripts/release_bundle.py"
    verify
    --bundle "$bundle_dir"
    --public-key "$release_public_key"
    --expected-release "$target_release"
    --installed-release "$current_release"
    --installed-schema "$current_schema"
    --load-images
  )
  if [ -n "$policy_sha256" ]; then
    verification+=(--expected-policy-sha256 "$policy_sha256")
  fi
  verification_output="$("${verification[@]}")"
  target_schema="$(printf '%s' "$verification_output" | "$python_bin" -c 'import json,sys; print(json.load(sys.stdin)["database_schema_revision"])')"
  if ! [[ "$target_schema" =~ ^[0-9A-Za-z][0-9A-Za-z_-]{0,127}$ ]]; then
    echo "Verified release returned an invalid target schema revision." >&2
    exit 1
  fi
  printf '%s\n' "$verification_output"
fi
target_schema="${target_schema:-$current_schema}"
export COOP_BACKUP_VERIFIER_RELEASE="$target_release"
backup_dir="$(bash "$root_dir/scripts/backup-node.sh" "${COOP_BACKUP_ROOT:-$root_dir/backups}")"
backup_kind="$(sed -n 's/^backup_kind=//p' "$backup_dir/manifest.env")"
if [ "$backup_kind" != "FULL" ]; then
  if [ "$environment" = "production" ] ||
     [ "${COOP_ALLOW_DATA_ONLY_BACKUP:-no}" != "yes" ]; then
    echo "Update refused: pre-update backup is DATA_ONLY. Supply protected recovery material and verified release." >&2
    exit 1
  fi
fi

mkdir -p "$root_dir/.operations"
state_file="$root_dir/.operations/previous-release.env"
cat > "$state_file" <<EOF
previous_release=$current_release
target_release=$target_release
previous_schema=$current_schema
target_schema=$target_schema
previous_bundle=$current_verified_bundle
target_bundle=$bundle_dir
preupdate_backup=$backup_dir
updated_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
chmod go-rwx "$state_file"

set_release() {
  release="$1"
  env_file="$root_dir/.env"
  temp_file="$(mktemp "$root_dir/.env.update.XXXXXX")"
  if [ -f "$env_file" ]; then
    awk -v release="$release" '
      BEGIN { found=0 }
      /^COOP_RELEASE=/ { print "COOP_RELEASE=" release; found=1; next }
      { print }
      END { if (!found) print "COOP_RELEASE=" release }
    ' "$env_file" > "$temp_file"
  else
    printf 'COOP_RELEASE=%s\n' "$release" > "$temp_file"
  fi
  chmod go-rwx "$temp_file"
  mv -- "$temp_file" "$env_file"
}

set_release "$target_release"
export COOP_RELEASE="$target_release"
if [ "$failpoint" = "after-release-switch" ]; then
  echo "Injected update failure after release switch." >&2
  bash "$root_dir/scripts/rollback-node.sh" "$current_release"
  exit 1
fi

if [ "${COOP_UPDATE_BUILD:-no}" = "yes" ]; then
  "${compose[@]}" build migrate api worker frontend gateway
else
  for image in backend frontend gateway; do
    docker image inspect "cooperative-clearing/$image:$target_release" >/dev/null
  done
fi

if ! "${compose[@]}" stop api worker frontend gateway; then
  echo "Runtime writer stop failed; attempting verified rollback." >&2
  bash "$root_dir/scripts/rollback-node.sh" "$current_release"
  exit 1
fi

update_failed=0
if ! "${compose[@]}" run --rm migrate; then
  update_failed=1
elif ! migrated_schema="$(read_database_schema)"; then
  update_failed=1
elif [ "$migrated_schema" != "$target_schema" ]; then
  echo "Migration reached schema $migrated_schema, expected $target_schema." >&2
  update_failed=1
elif [ "$failpoint" = "after-migration" ]; then
  echo "Injected update failure after migration." >&2
  update_failed=1
elif ! "${compose[@]}" up -d api worker frontend gateway; then
  update_failed=1
elif [ "$failpoint" = "after-startup" ]; then
  echo "Injected update failure after startup." >&2
  update_failed=1
elif ! bash "$root_dir/scripts/verify-stack.sh" "http://127.0.0.1:${COOP_HTTP_PORT:-8080}"; then
  update_failed=1
elif ! "${compose[@]}" run --rm --no-deps api coopctl verify-journal; then
  update_failed=1
elif ! "${compose[@]}" run --rm --no-deps api coopctl verify-restore-consistency; then
  update_failed=1
elif [ "$environment" = "production" ] && ! "$python_bin" "$root_dir/scripts/runtime_environment.py" configure \
  --root "$root_dir" \
  --mode production \
  --release "$target_release" \
  --verified-release-bundle "$bundle_dir" \
  --release-public-key "$release_public_key" \
  --license-policy-sha256 "$policy_sha256" >/dev/null; then
  update_failed=1
fi
if [ "$update_failed" -ne 0 ]; then
  echo "Update verification failed; attempting verified application/schema rollback." >&2
  bash "$root_dir/scripts/rollback-node.sh" "$current_release"
  exit 1
fi

echo "Updated $current_release -> $target_release; backup: $backup_dir"
