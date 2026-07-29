#!/usr/bin/env bash
set -Eeuo pipefail

root_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
state_file="$root_dir/.operations/previous-release.env"
compose=(docker compose --project-directory "$root_dir" -f "$root_dir/compose.yaml")
python_bin="${PYTHON:-python3}"

if [ ! -f "$state_file" ]; then
  echo "No verified previous release state is available." >&2
  exit 1
fi
state_value() {
  sed -n "s/^$1=//p" "$state_file" | tail -n 1
}
runtime_setting() {
  "$python_bin" "$root_dir/scripts/runtime_environment.py" get \
    --root "$root_dir" --name "$1"
}
read_database_schema() {
  schema_output="$("${compose[@]}" run --rm --no-deps api alembic current)"
  schema="$(printf '%s\n' "$schema_output" | sed -n -e 's/^\([0-9A-Za-z][0-9A-Za-z_-]*\) (head)$/\1/p' -e 's/^\([0-9A-Za-z][0-9A-Za-z_-]*\)$/\1/p')"
  if ! [[ "$schema" =~ ^[0-9A-Za-z][0-9A-Za-z_-]{0,127}$ ]]; then
    echo "Cannot determine one current database schema revision." >&2
    return 1
  fi
  printf '%s\n' "$schema"
}
journal_value() {
  printf '%s' "$1" | "$python_bin" -c 'import json,sys; print(json.load(sys.stdin)[sys.argv[1]])' "$2"
}

previous_release="$(state_value previous_release)"
target_release="$(state_value target_release)"
previous_schema="$(state_value previous_schema)"
target_schema="$(state_value target_schema)"
previous_bundle="$(state_value previous_bundle)"
target_bundle="$(state_value target_bundle)"
backup_dir="$(state_value preupdate_backup)"
requested_release="${1:-$previous_release}"

for release in "$requested_release" "$previous_release" "$target_release"; do
  if ! [[ "$release" =~ ^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$ ]]; then
    echo "Invalid rollback release state." >&2
    exit 1
  fi
done
if [ "$requested_release" != "$previous_release" ]; then
  echo "Rollback release does not match the verified previous release state." >&2
  exit 1
fi
for schema in "$previous_schema" "$target_schema"; do
  if ! [[ "$schema" =~ ^[0-9A-Za-z][0-9A-Za-z_-]{0,127}$ ]]; then
    echo "Invalid rollback schema state." >&2
    exit 1
  fi
done

environment="$("$python_bin" "$root_dir/scripts/runtime_environment.py" resolve --root "$root_dir")"
release_public_key="${COOP_RELEASE_PUBLIC_KEY:-$(runtime_setting COOP_RELEASE_PUBLIC_KEY)}"
policy_sha256="${COOP_RELEASE_LICENSE_POLICY_SHA256:-$(runtime_setting COOP_RELEASE_LICENSE_POLICY_SHA256)}"
if [ "$environment" = "production" ] && { [ -z "$release_public_key" ] || [ -z "$policy_sha256" ] || [ -z "$previous_bundle" ] || [ -z "$target_bundle" ]; }; then
  echo "Production rollback requires both signed bundles, the release key and pinned policy." >&2
  exit 1
fi

if [ -n "$target_bundle" ]; then
  target_verification=(
    "$python_bin"
    "$root_dir/scripts/release_bundle.py"
    verify
    --bundle "$target_bundle"
    --public-key "$release_public_key"
    --expected-release "$target_release"
    --installed-release "$previous_release"
    --installed-schema "$previous_schema"
  )
  previous_verification=(
    "$python_bin"
    "$root_dir/scripts/release_bundle.py"
    verify
    --bundle "$previous_bundle"
    --public-key "$release_public_key"
    --expected-release "$previous_release"
    --load-images
  )
  if [ -n "$policy_sha256" ]; then
    target_verification+=(--expected-policy-sha256 "$policy_sha256")
    previous_verification+=(--expected-policy-sha256 "$policy_sha256")
  fi
  target_report="$("${target_verification[@]}")"
  previous_report="$("${previous_verification[@]}")"
  verified_target_schema="$(printf '%s' "$target_report" | "$python_bin" -c 'import json,sys; print(json.load(sys.stdin)["database_schema_revision"])')"
  verified_previous_schema="$(printf '%s' "$previous_report" | "$python_bin" -c 'import json,sys; print(json.load(sys.stdin)["database_schema_revision"])')"
  if [ "$verified_target_schema" != "$target_schema" ] || [ "$verified_previous_schema" != "$previous_schema" ]; then
    echo "Rollback state does not match the signed release schema contracts." >&2
    exit 1
  fi
else
  for image in backend frontend gateway; do
    docker image inspect "cooperative-clearing/$image:$requested_release" >/dev/null || {
      echo "Rollback image is unavailable: cooperative-clearing/$image:$requested_release" >&2
      exit 1
    }
  done
fi

"${compose[@]}" stop api worker frontend gateway
before_report="$("${compose[@]}" run --rm --no-deps api coopctl verify-journal)"
before_sequence="$(journal_value "$before_report" last_sequence)"
before_hash="$(journal_value "$before_report" last_event_hash)"
current_schema="$(read_database_schema)"
if [ "$current_schema" = "$target_schema" ] && [ "$target_schema" != "$previous_schema" ]; then
  "${compose[@]}" run --rm --no-deps migrate alembic downgrade "$previous_schema"
elif [ "$current_schema" != "$previous_schema" ]; then
  echo "Rollback cannot proceed from unexpected schema $current_schema; restore backup: $backup_dir" >&2
  exit 1
fi
if [ "$(read_database_schema)" != "$previous_schema" ]; then
  echo "Schema rollback verification failed; restore backup: $backup_dir" >&2
  exit 1
fi
"${compose[@]}" run --rm --no-deps api coopctl verify-restore-consistency

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

if [ "$environment" = "production" ]; then
  "${compose[@]}" up -d --no-build --pull never --force-recreate api worker frontend gateway
else
  "${compose[@]}" up -d --force-recreate api worker frontend gateway
fi
if ! bash "$root_dir/scripts/verify-stack.sh" "http://127.0.0.1:${COOP_HTTP_PORT:-8080}"; then
  echo "Application rollback failed verification; restore backup: $backup_dir" >&2
  exit 1
fi
after_report="$("${compose[@]}" run --rm --no-deps api coopctl verify-journal)"
after_sequence="$(journal_value "$after_report" last_sequence)"
after_hash="$(journal_value "$after_report" last_event_hash)"
if [ "$before_sequence" != "$after_sequence" ] || [ "$before_hash" != "$after_hash" ]; then
  echo "Rollback changed accepted journal history; restore backup: $backup_dir" >&2
  exit 1
fi
if [ "$environment" = "production" ]; then
  "$python_bin" "$root_dir/scripts/runtime_environment.py" configure \
    --root "$root_dir" \
    --mode production \
    --release "$previous_release" \
    --verified-release-bundle "$previous_bundle" \
    --release-public-key "$release_public_key" \
    --license-policy-sha256 "$policy_sha256" >/dev/null
fi

mkdir -p "$root_dir/.operations"
printf 'rolled_back_to=%s\nrolled_back_schema=%s\njournal_last_sequence=%s\njournal_last_event_hash=%s\nrolled_back_at=%s\n' \
  "$requested_release" "$previous_schema" "$after_sequence" "$after_hash" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  > "$root_dir/.operations/last-rollback.env"
chmod go-rwx "$root_dir/.operations/last-rollback.env"
echo "Application/schema rollback completed: $requested_release@$previous_schema; journal sequence: $after_sequence"