#!/bin/sh
set -eu

root_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$root_dir"

mode="${1:-demo}"
python_bin="${PYTHON:-python3}"
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
    if [ "$#" -ne 1 ] && [ "$#" -ne 0 ]; then
      echo "Usage: $0 demo" >&2
      exit 2
    fi
    if [ -f .env ] && grep -Eq '^COOP_ENVIRONMENT=(staging-node|pilot|production)$' .env; then
      echo "A hardened node cannot be started in demo mode in place." >&2
      exit 1
    fi
    export COOP_ENVIRONMENT=dev
    export COOP_DEMO_DATA_ENABLED=true
    export COMPOSE_PROFILES=demo
    COOP_DEMO_CREDENTIALS=true sh ./scripts/bootstrap-node.sh
    docker compose up -d --build
    ;;
  production)
    if [ "$#" -gt 5 ]; then
      echo "Usage: $0 production <bundle-directory> <public-key> <release> <policy-sha256>" >&2
      exit 2
    fi
    if ! command -v "$python_bin" >/dev/null 2>&1; then
      echo "Python 3 is required to verify a production release bundle." >&2
      exit 1
    fi
    bundle_dir="${2:-${COOP_VERIFIED_RELEASE_BUNDLE:-}}"
    public_key="${3:-${COOP_RELEASE_PUBLIC_KEY:-}}"
    release="${4:-${COOP_RELEASE:-}}"
    policy_sha256="${5:-${COOP_RELEASE_LICENSE_POLICY_SHA256:-}}"
    if [ -z "$bundle_dir" ] || [ -z "$public_key" ] || [ -z "$release" ] || [ -z "$policy_sha256" ]; then
      echo "Production requires a verified bundle, independent public key, release id and approved license-policy SHA-256." >&2
      echo "Usage: $0 production <bundle-directory> <public-key> <release> <policy-sha256>" >&2
      exit 2
    fi
    "$python_bin" ./scripts/release_bundle.py verify \
      --bundle "$bundle_dir" \
      --public-key "$public_key" \
      --expected-release "$release" \
      --expected-policy-sha256 "$policy_sha256" \
      --load-images
    export COOP_ENVIRONMENT=production
    export COOP_DEMO_DATA_ENABLED=false
    export COMPOSE_PROFILES=production
    export COOP_RELEASE="$release"
    export COOP_VERIFIED_RELEASE_BUNDLE="$bundle_dir"
    export COOP_RELEASE_PUBLIC_KEY="$public_key"
    export COOP_RELEASE_LICENSE_POLICY_SHA256="$policy_sha256"
    sh ./scripts/bootstrap-node.sh production "$release"
    docker compose up -d --no-build --pull never
    ;;
  *)
    echo "Usage: $0 [demo|production <bundle-directory> <public-key> <release> <policy-sha256>]" >&2
    exit 2
    ;;
esac

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
