#!/usr/bin/env bash
# Activate the OpenRouter persona-voice provider on the production standby
# (.245) — the board-approved env flip (HU-1774 decision sweep 2026-08-18,
# item 3; HU-1461). The $50/mo hard-cap code is already deployed (commit
# a836f86); this script only stages the key and flips two env vars.
#
# Usage:
#   bash scripts/activate_voice_provider.sh <path-to-key-file>     # activate
#   bash scripts/activate_voice_provider.sh --check               # pre-flight
#   bash scripts/activate_voice_provider.sh --rollback            # back to fake
#
# Key file: a single line containing the OpenRouter key (sk-or-...). The
# script never logs the key. Rollback is the one-knob approved posture:
# LLM_PROVIDER=fake (FakeLLMClient deterministic voice).
#
# Idempotent: safe to re-run; re-validates and re-asserts the target state.

set -euo pipefail

ENV_FILE=".env.failover"
COMPOSE_FILES=(-f docker-compose.yml -f docker-compose.failover.yml)
PASS=0; FAIL=0
ok()   { echo "  [PASS] $1"; PASS=$((PASS+1)); }
bad()  { echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }

key_file=""

preflight() {
  echo "== Pre-flight (voice-provider activation, HU-1461) =="
  command -v docker >/dev/null || { bad "docker CLI missing"; exit 1; }
  docker inspect huible-app --format '{{.State.Health.Status}}' 2>/dev/null | grep -q healthy \
    && ok "huible-app container healthy" || bad "huible-app container not healthy"
  docker exec huible-app python -c "import huible.llm.budget" >/dev/null 2>&1 \
    && ok "spend-cap code present in image (huible.llm.budget)" \
    || bad "spend-cap code MISSING from image — rebuild first (commit a836f86+)"
  docker exec huible-app sh -c 'test -d /var/lib/huible' >/dev/null 2>&1 \
    && ok "durable spend-state mount /var/lib/huible present" \
    || bad "spend-state mount missing — check docker-compose.yml app volumes"
  grep -q '^OPENROUTER_MONTHLY_BUDGET_USD=50' "$ENV_FILE" 2>/dev/null \
    && ok "budget knob staged at 50 USD (.env.failover)" \
    || bad "OPENROUTER_MONTHLY_BUDGET_USD=50 missing from $ENV_FILE"
  local prov
  prov="$(grep -E '^LLM_PROVIDER=' "$ENV_FILE" | tail -1 | cut -d= -f2- | cut -d' ' -f1)"
  if [ "$(echo "$prov" | tr -d ' ')" = "fake" ]; then
    ok "current provider=fake (safe pre-activation state)"
  else
    echo "  [INFO] current provider=$prov (activation will re-assert openrouter)"
  fi
  echo "Pre-flight: $PASS pass, $FAIL fail"
  [ "$FAIL" -eq 0 ]
}

validate_key() {
  local key="$1"
  local resp
  resp="$(curl -s -m 15 -o /dev/null -w '%{http_code}' \
    -H "Authorization: Bearer $key" https://openrouter.ai/api/v1/key)" || true
  # 200 = key valid; 401/403 = bad key. Anything else (network/proxy) is a
  # soft-fail: warn but continue — the fail-closed client config still guards.
  case "$resp" in
    200) ok "OpenRouter key validated (GET /api/v1/key -> 200)"; return 0 ;;
    401|403) bad "OpenRouter key REJECTED (HTTP $resp) — not activating"; return 1 ;;
    *) echo "  [WARN] key validation inconclusive (HTTP $resp); continuing (client fails closed on a bad key at first call)"; return 0 ;;
  esac
}

activate() {
  local key
  key="$(tr -d '[:space:]' < "$key_file")"
  if [ -z "$key" ]; then bad "key file empty"; exit 1; fi
  preflight
  validate_key "$key"
  cp -p "$ENV_FILE" "$ENV_FILE.bak.$(date -u +%Y%m%dT%H%M%SZ)"
  python3 - "$ENV_FILE" "$key" <<'PY'
import sys, pathlib
env, key = sys.argv[1], sys.argv[2]
p = pathlib.Path(env)
lines = p.read_text().splitlines()
out, seen_p, seen_k = [], False, False
for line in lines:
    if line.startswith("LLM_PROVIDER="):
        out.append("LLM_PROVIDER=openrouter"); seen_p = True
    elif line.startswith("OPENROUTER_API_KEY="):
        out.append(f"OPENROUTER_API_KEY={key}"); seen_k = True
    else:
        out.append(line)
if not seen_p: out.append("LLM_PROVIDER=openrouter")
if not seen_k: out.append(f"OPENROUTER_API_KEY={key}")
p.write_text("\n".join(out) + "\n")
PY
  chmod 600 "$ENV_FILE"
  ok "env staged: LLM_PROVIDER=openrouter + key (backup saved)"
  docker compose "${COMPOSE_FILES[@]}" up -d app >/dev/null
  ok "huible-app recreated with openrouter env"
  verify
}

rollback() {
  preflight || true
  sed -i 's/^LLM_PROVIDER=.*/LLM_PROVIDER=fake  # rolled back (approved one-knob posture)/' "$ENV_FILE"
  docker compose "${COMPOSE_FILES[@]}" up -d app >/dev/null
  ok "rolled back to LLM_PROVIDER=fake"
  curl -s -m 8 http://127.0.0.1:8000/health >/dev/null && ok "health reachable post-rollback"
}

verify() {
  echo "== Post-flip verification =="
  local health
  health="$(curl -s -m 10 http://127.0.0.1:8000/health)" || { bad "health endpoint unreachable"; exit 1; }
  echo "$health" | grep -q '"status":"ok"' && ok "health status ok" || bad "health not ok"
  echo "$health" | grep -q '"database":"ok"' && ok "database ok" || bad "database not ok"
  echo "$health" | grep -q 'llm_budget' \
    && ok "llm_budget surfaced in /health (openrouter client live, cap armed): $(echo "$health" | grep -o '"llm_budget":"[^"]*"')" \
    || bad "llm_budget MISSING from /health — openrouter client not constructed (check app logs)"
  echo "Verification: $PASS pass, $FAIL fail"
  [ "$FAIL" -eq 0 ]
}

case "${1:-}" in
  --check) preflight ;;
  --rollback) rollback ;;
  --help|-h|'') sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) key_file="$1"; activate ;;
esac
