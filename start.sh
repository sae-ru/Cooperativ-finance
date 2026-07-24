#!/bin/sh
set -eu

root_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$root_dir"

mode="${1:-demo}"
if ! command -v docker >/dev/null 2>&1; then
  echo "Docker Engine is not installed or is not available in PATH." >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose is not available." >&2
  exit 1
fi

case "$mode" in
  demo)
    export COOP_DEMO_DATA_ENABLED=true
    export COMPOSE_PROFILES=demo
    COOP_DEMO_CREDENTIALS=true sh ./scripts/bootstrap-node.sh
    ;;
  production)
    export COOP_DEMO_DATA_ENABLED=false
    export COMPOSE_PROFILES=production
    sh ./scripts/bootstrap-node.sh
    ;;
  *)
    echo "Usage: ./start.sh [demo|production]" >&2
    exit 2
    ;;
esac

docker compose up -d --build
sh ./scripts/verify-stack.sh

printf '\nCooperative Clearing is ready: http://127.0.0.1:8080\n'
if [ "$mode" = "demo" ]; then
  cat <<'EOF'

Demo accounts for a fresh installation:
  registrar / CoopDemo-Registrar-2026!
  security  / CoopDemo-Security-2026!
  auditor   / CoopDemo-Auditor-2026!
Passwords are requested to be changed after the first sign-in.
EOF
fi