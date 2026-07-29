#!/usr/bin/env bash
set -Eeuo pipefail

if [ "$#" -lt 4 ]; then
  echo "Usage: $0 <release> <output-directory> <private-key> <qualified-platform> [frontend-audit-image] [upgrade-from-release@schema ...]" >&2
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
  --qualified-platform "$4"
)
if [ "$#" -ge 5 ]; then
  arguments+=(--frontend-audit-image "$5")
fi
if [ "$#" -gt 5 ]; then
  shift 5
  for source in "$@"; do
    arguments+=(--upgrade-from "$source")
  done
fi
"$python_bin" "${arguments[@]}"