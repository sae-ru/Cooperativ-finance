#!/usr/bin/env bash
set -Eeuo pipefail

if [ "$#" -lt 2 ] || [ "$#" -gt 4 ]; then
  echo "Usage: $0 <bundle-directory> <public-key> [expected-release] [expected-platform]" >&2
  exit 2
fi

root_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${PYTHON:-python3}"
arguments=(
  "$root_dir/scripts/release_bundle.py"
  verify
  --bundle "$1"
  --public-key "$2"
)
if [ "$#" -ge 3 ]; then
  arguments+=(--expected-release "$3")
fi
if [ "$#" -eq 4 ]; then
  arguments+=(--expected-platform "$4")
fi
if [ -n "${COOP_RELEASE_LICENSE_POLICY_SHA256:-}" ]; then
  arguments+=(--expected-policy-sha256 "$COOP_RELEASE_LICENSE_POLICY_SHA256")
fi
"$python_bin" "${arguments[@]}"