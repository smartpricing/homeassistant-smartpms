#!/usr/bin/env bash
# Read-only smoke checks for the SmartPMS Home Assistant integration.
#
# Every request is a GET (or HEAD); nothing is created, changed or deleted, so
# both modes are safe to run against production.
#
#   scripts/smoke/smoke.sh api [API_BASE]
#       Anonymous checks of the SmartPMS public v2 API the integration depends
#       on (default https://pms-api.smartness.com/api/public/v2): TLS chain,
#       health, the auth gate rejects anonymous / bogus credentials, /login is
#       POST-only. No credentials are used.
#
#   HA_TOKEN=<long-lived token> scripts/smoke/smoke.sh ha [HA_URL]
#       Checks a Home Assistant instance running the integration (default
#       http://127.0.0.1:8123): entries loaded, unit sensors present,
#       diagnostics redacted, anonymous access rejected, and (optional) that
#       none of the operator's own secrets appear in diagnostics or in the
#       Home Assistant log. Put those secrets (SmartPMS password, API key; one
#       per line) in a file and pass SMARTPMS_SECRETS_FILE=<file>; values are
#       only compared, never printed.
#
# Exit code: 0 = no FAIL (WARNs allowed), 1 = at least one FAIL, 2 = usage.
set -uo pipefail

MODE="${1:-}"
PASS=0; FAIL=0; WARN=0
pass() { PASS=$((PASS + 1)); printf '[PASS] %s\n' "$*"; }
fail() { FAIL=$((FAIL + 1)); printf '[FAIL] %s\n' "$*"; }
warn() { WARN=$((WARN + 1)); printf '[WARN] %s\n' "$*"; }
CURL=(curl -sS --proto '=https,http' --max-time 20 -o /dev/null)

status() { # status URL [curl args...]
  local url="$1"; shift
  "${CURL[@]}" -w '%{http_code}' "$@" "$url" 2>/dev/null || echo 000
}

expect_status() { # expect_status "label" URL "allowed codes" [curl args...]
  local label="$1" url="$2" allowed="$3"; shift 3
  local code; code="$(status "$url" "$@")"
  if [[ " $allowed " == *" $code "* ]]; then pass "$label -> $code"
  else fail "$label -> $code (expected $allowed)"; fi
}

summary() {
  printf '\n%d passed, %d failed, %d warnings\n' "$PASS" "$FAIL" "$WARN"
  [[ $FAIL -eq 0 ]]
}

api_mode() {
  local base="${1:-https://pms-api.smartness.com/api/public/v2}"
  base="${base%/}"
  local root="${base%/v2}"
  echo "SmartPMS API smoke (anonymous, GET only): $base"

  # TLS: curl verifies chain + hostname by default; 000 = handshake failure.
  expect_status "GET ${root}/up (health, TLS verified)" "${root}/up" "200"
  expect_status "GET /automations/units anonymous is rejected" \
    "${base}/automations/units" "401"
  expect_status "GET /automations/properties anonymous is rejected" \
    "${base}/automations/properties" "401"
  expect_status "GET /automations/units with bogus key + token is rejected" \
    "${base}/automations/units" "401 403" \
    -H 'X-API-KEY: smoke-invalid-key' -H 'Authorization: Bearer smoke-invalid'
  expect_status "GET /login is not served (login is POST-only)" \
    "${base}/login" "404 405"

  local headers
  headers="$(curl -sS --max-time 20 -D - -o /dev/null "${base}/automations/units" 2>/dev/null)"
  grep -qi '^strict-transport-security:' <<<"$headers" \
    && pass "HSTS header present" \
    || warn "no Strict-Transport-Security header on the API (cb-backend/ingress)"
  grep -qi '^x-powered-by:' <<<"$headers" \
    && warn "X-Powered-By discloses the stack: $(grep -i '^x-powered-by:' <<<"$headers" | tr -d '\r')" \
    || pass "no X-Powered-By header"
  summary
}

ha_mode() {
  local url="${1:-http://127.0.0.1:8123}"; url="${url%/}"
  : "${HA_TOKEN:?set HA_TOKEN to a long-lived access token of an admin user}"
  local auth=(-H "Authorization: Bearer ${HA_TOKEN}")
  local tmp; tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  echo "Home Assistant smoke (GET only): $url"

  expect_status "GET /api/ anonymous is rejected" "${url}/api/" "401"
  expect_status "GET /api/ with token" "${url}/api/" "200" "${auth[@]}"

  curl -sS --max-time 20 "${auth[@]}" \
    "${url}/api/config/config_entries/entry?domain=smartpms" -o "$tmp/entries.json"
  local ids
  ids="$(python3 - "$tmp/entries.json" <<'PY'
import json, sys
entries = json.load(open(sys.argv[1]))
for e in entries:
    print(e["entry_id"], e["state"], (e.get("reason") or "").replace(" ", "_"))
PY
)" || { fail "cannot read config entries"; summary; return; }
  if [[ -z "$ids" ]]; then fail "no smartpms config entry"; summary; return; fi
  while read -r id state reason; do
    [[ "$state" == "loaded" ]] && pass "entry ${id:0:8}… loaded" \
      || fail "entry ${id:0:8}… state=$state reason=$reason"
  done <<<"$ids"

  curl -sS --max-time 30 "${auth[@]}" "${url}/api/states" -o "$tmp/states.json"
  python3 - "$tmp/states.json" <<'PY' && pass "unit sensors present" || fail "no SmartPMS unit sensors"
import json, sys
states = [s for s in json.load(open(sys.argv[1]))
          if s["entity_id"].startswith("sensor.")
          and {"unit_id", "property_id"} <= set(s["attributes"])]
bad = [s for s in states if s["state"] not in ("free", "occupied", "blocked")]
print(f"       {len(states)} unit sensors, {len(bad)} not free/occupied/blocked")
sys.exit(0 if states else 1)
PY

  local secrets_file="${SMARTPMS_SECRETS_FILE:-}"
  while read -r id _state _reason; do
    local diag="$tmp/diag-$id.json"
    expect_status "GET diagnostics ${id:0:8}… anonymous is rejected" \
      "${url}/api/diagnostics/config_entry/${id}" "401"
    curl -sS --max-time 30 "${auth[@]}" \
      "${url}/api/diagnostics/config_entry/${id}" -o "$diag"
    python3 - "$diag" <<'PY' && pass "diagnostics ${id:0:8}… redact email/password/api_key" \
      || fail "diagnostics ${id:0:8}… not redacted"
import json, sys
cfg = json.load(open(sys.argv[1]))["data"]["config_entry"]
sys.exit(0 if all(cfg.get(k) == "**REDACTED**" for k in ("email", "password", "api_key")) else 1)
PY
    if [[ -n "$secrets_file" ]]; then
      if grep -qF -f "$secrets_file" "$diag"; then fail "a secret appears in diagnostics ${id:0:8}…"
      else pass "no operator secret in diagnostics ${id:0:8}…"; fi
    fi
  done <<<"$ids"

  local code
  code="$(status "${url}/api/error_log" "${auth[@]}")"
  if [[ "$code" == "200" ]]; then
    curl -sS --max-time 30 "${auth[@]}" "${url}/api/error_log" -o "$tmp/ha.log"
    if [[ -n "$secrets_file" ]]; then
      if grep -qF -f "$secrets_file" "$tmp/ha.log"; then fail "a secret appears in the HA log"
      else pass "no operator secret in the HA log"; fi
    fi
    local tokens
    tokens="$(grep -cE '"(token|refreshToken)"[[:space:]]*:' "$tmp/ha.log" || true)"
    [[ "$tokens" == "0" ]] && pass "no login response (token JSON) in the HA log" \
      || fail "$tokens log lines contain a token field"
    local errors
    errors="$(grep -c 'ERROR .*custom_components.smartpms' "$tmp/ha.log" || true)"
    [[ "$errors" == "0" ]] && pass "no SmartPMS errors in the HA log" \
      || warn "$errors SmartPMS ERROR lines in the HA log (check they are expected)"
  else
    warn "GET /api/error_log -> $code (log checks skipped; needs an admin token)"
  fi

  for path in /.git/config /.env /local/secrets.yaml /local/.storage/core.config_entries; do
    expect_status "GET $path is not served" "${url}${path}" "401 403 404"
  done
  summary
}

case "$MODE" in
  api) shift; api_mode "$@" ;;
  ha) shift; ha_mode "$@" ;;
  *) sed -n '2,24p' "$0"; exit 2 ;;
esac
