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
environment="${COOP_ENVIRONMENT:-}"
if [ -z "$environment" ] && [ -f "$root_dir/.env" ]; then
  environment="$(sed -n 's/^COOP_ENVIRONMENT=//p' "$root_dir/.env" | tail -n 1)"
fi
environment="${environment:-dev}"

if ! [[ "$target_release" =~ ^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$ ]]; then
  echo "Invalid release identifier." >&2
  exit 1
fi

if [ "$environment" = "prod" ] && [ -z "$bundle_dir" ]; then
  echo "Production update requires a signed offline bundle." >&2
  exit 1
fi
if [ "$environment" = "prod" ] && [ "${COOP_UPDATE_BUILD:-no}" = "yes" ]; then
  echo "Production update cannot build images from source." >&2
  exit 1
fi

if [ -n "$bundle_dir" ]; then
  bundle_dir="$(realpath "$bundle_dir")"
  if [ -z "${COOP_RELEASE_PUBLIC_KEY:-}" ]; then
    echo "COOP_RELEASE_PUBLIC_KEY must name the independently provisioned public key." >&2
    exit 1
  fi
  verification=(
    python3
    "$root_dir/scripts/release_bundle.py"
    verify
    --bundle "$bundle_dir"
    --public-key "$COOP_RELEASE_PUBLIC_KEY"
    --expected-release "$target_release"
    --load-images
  )
  if [ -n "${COOP_RELEASE_LICENSE_POLICY_SHA256:-}" ]; then
    verification+=(--expected-policy-sha256 "$COOP_RELEASE_LICENSE_POLICY_SHA256")
  fi
  "${verification[@]}"
fi
current_release="${COOP_RELEASE:-}"
if [ -z "$current_release" ] && [ -f "$root_dir/.env" ]; then
  current_release="$(sed -n 's/^COOP_RELEASE=//p' "$root_dir/.env" | tail -n 1)"
fi
current_release="${current_release:-0.1.0-dev}"
failpoint="${COOP_UPDATE_FAILPOINT:-none}"
case "$failpoint" in
  none|after-release-switch|after-migration|after-startup) ;;
  *)
    echo "Unsupported COOP_UPDATE_FAILPOINT: $failpoint" >&2
    exit 1
    ;;
esac
if [ "$environment" = "prod" ] && [ "$failpoint" != "none" ]; then
  echo "Update faultpoints are forbidden in production." >&2
  exit 1
fi
if [ "$current_release" = "$target_release" ]; then
  echo "Release $target_release is already selected." >&2
  exit 1
fi

backup_dir="$(bash "$root_dir/scripts/backup-node.sh" "${COOP_BACKUP_ROOT:-$root_dir/backups}")"
backup_kind="$(sed -n 's/^backup_kind=//p' "$backup_dir/manifest.env")"
if [ "$backup_kind" != "FULL" ]; then
  if [ "$environment" = "prod" ] ||
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

update_failed=0
if ! "${compose[@]}" run --rm migrate; then
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
fi
if [ "$update_failed" -ne 0 ]; then
  echo "Update verification failed; attempting application-only rollback." >&2
  bash "$root_dir/scripts/rollback-node.sh" "$current_release"
  exit 1
fi

"${compose[@]}" run --rm --no-deps api coopctl verify-journal
echo "Updated $current_release -> $target_release; backup: $backup_dir"
