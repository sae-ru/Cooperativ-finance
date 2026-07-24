#!/bin/sh
set -eu

root_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
secrets_dir="$root_dir/secrets"

umask 077
mkdir -p "$secrets_dir"

write_secret() {
  path="$1"
  bytes="$2"
  if [ ! -s "$path" ]; then
    openssl rand -hex "$bytes" > "$path"
  fi
}

write_initial_password() {
  path="$1"
  demo_value="$2"
  if [ -s "$path" ]; then
    return
  fi
  if [ "${COOP_DEMO_CREDENTIALS:-false}" = "true" ]; then
    printf '%s\n' "$demo_value" > "$path"
  else
    openssl rand -hex 32 > "$path"
  fi
}

write_secret "$secrets_dir/postgres_migrator_password" 32
write_secret "$secrets_dir/postgres_app_password" 32
write_secret "$secrets_dir/node_signing_seed" 32
write_secret "$secrets_dir/blob_encryption_key" 32
write_initial_password "$secrets_dir/bootstrap_registrar_password" "CoopDemo-Registrar-2026!"
write_initial_password "$secrets_dir/bootstrap_security_password" "CoopDemo-Security-2026!"
write_initial_password "$secrets_dir/bootstrap_auditor_password" "CoopDemo-Auditor-2026!"

if [ ! -f "$root_dir/.env" ]; then
  cp "$root_dir/.env.example" "$root_dir/.env"
fi

echo "Node secrets and non-secret configuration are ready."
