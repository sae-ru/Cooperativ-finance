#!/bin/sh
set -eu

base_url="${1:-http://127.0.0.1:8080}"
attempts="${COOP_VERIFY_ATTEMPTS:-30}"
delay="${COOP_VERIFY_DELAY_SECONDS:-2}"
verified=0
attempt=1

while [ "$attempt" -le "$attempts" ]; do
  if curl --fail --silent "$base_url/health/live" >/dev/null 2>&1 &&
     curl --fail --silent "$base_url/health/ready" >/dev/null 2>&1 &&
     curl --fail --silent "$base_url/api/v1/system/status" 2>/dev/null \
       | python3 -c 'import json,sys; data=json.load(sys.stdin)["data"]; assert data["status"] == "OPERATIONAL"; assert data["worker"]["status"] == "RUNNING"' >/dev/null 2>&1; then
    verified=1
    break
  fi
  if [ "$attempt" -lt "$attempts" ]; then
    sleep "$delay"
  fi
  attempt=$((attempt + 1))
done

if [ "$verified" -ne 1 ]; then
  echo "Stack did not become ready: $base_url" >&2
  exit 1
fi

if [ "${COOP_VERIFY_BOOTSTRAP_LOGIN:-false}" = "true" ]; then
  root_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
  password="$(cat "$root_dir/secrets/bootstrap_registrar_password")"
  login_body="$(python3 -c 'import json,sys; print(json.dumps({"login":"registrar","password":sys.argv[1]}))' "$password")"
  login_response="$(curl --fail --silent --header 'Content-Type: application/json' --data "$login_body" "$base_url/api/v1/auth/login")"
  access_token="$(printf '%s' "$login_response" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["access_token"])')"
  curl --fail --silent --header "Authorization: Bearer $access_token" "$base_url/api/v1/auth/me" \
    | python3 -c 'import json,sys; data=json.load(sys.stdin)["data"]; assert data["must_change_password"] is True; assert {item["role"] for item in data["roles"]} >= {"MEMBER_REGISTRAR", "COOPERATIVE_ADMIN"}'
fi

echo "Stack verification passed: $base_url"