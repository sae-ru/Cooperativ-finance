#!/usr/bin/env sh
set -eu

compose_file="compose.federation-test.yaml"

cleanup() {
  status=$?
  trap - EXIT INT TERM
  if [ "$status" -ne 0 ]; then
    docker compose -f "$compose_file" ps --all || true
    docker compose -f "$compose_file" logs --no-color --tail 250 || true
  fi
  if [ "${KEEP_FEDERATION_TEST_STACK:-0}" != "1" ]; then
    docker compose -f "$compose_file" down --volumes --remove-orphans || true
  fi
  exit "$status"
}

trap cleanup EXIT INT TERM

docker compose -f "$compose_file" down --volumes --remove-orphans
docker compose -f "$compose_file" up --detach --build --wait node-a node-b node-c
docker compose -f "$compose_file" run --rm --no-deps acceptance
