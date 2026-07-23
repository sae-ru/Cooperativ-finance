#!/usr/bin/env bash
set -Eeuo pipefail

if [ "$#" -lt 3 ] || [ "$#" -gt 4 ]; then
  echo "Usage: $0 <release> <output-directory> <private-key> [frontend-audit-image]" >&2
  exit 2
fi

root_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${PYTHON:-python3}"
arguments=(
  "$root_dir/scripts/release_bundle.py"
  create
  --release "$1"
  --output "$2"
  --private-key "$3"
)
if [ "$#" -eq 4 ]; then
  arguments+=(--frontend-audit-image "$4")
fi
"$python_bin" "${arguments[@]}"