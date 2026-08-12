#!/usr/bin/env bash
# External production-hardening verifier for docs/09 §8 (HU-1464, Stage-0.9).
#
# Complement to verify_prod_hardening.sh (which runs ON the host). This one runs
# from ANY machine with internet + curl + openssl — it checks every §8 item that
# is externally observable: TLS certificate validity, the /health endpoint, and
# that Postgres/app ports are NOT reachable from the public internet. Use it when
# the deployer pastes the prod URL (ask_user_questions option "url" on HU-1464).
#
# Usage:
#   bash scripts/verify_prod_external.sh <domain>
#       e.g. bash scripts/verify_prod_external.sh huible.example.com
#
# Exit code: 0 only if every external check passes. A single FAIL is a
# hardening gap that must be fixed before Stage A → 1 advance.
set -u

DOMAIN="${1:-}"
if [ -z "$DOMAIN" ]; then
  echo "Usage: $0 <domain>   (e.g. huible.example.com)" >&2
  exit 2
fi

# Strip a leading scheme if the caller pasted a full URL.
DOMAIN="${DOMAIN#https://}"
DOMAIN="${DOMAIN#http://}"
DOMAIN="${DOMAIN%%/*}"

PASS=0; FAIL=0
ok()   { echo "  [PASS] $1"; PASS=$((PASS+1)); }
fail() { echo "  [FAIL] $1 — $2"; FAIL=$((FAIL+1)); }
note() { echo "       $1"; }

echo "=== docs/09 §8 production hardening — EXTERNAL verification ==="
echo "target: $DOMAIN  time: $(date -u +%FT%TZ)  from: $(hostname)"
echo

# ─── TLS (definitive: external connect is the real proof) ────────────────────
echo "## TLS"

# 1. Certificate validity via openssl. Captures issuer, dates, and CN/SAN.
tls_out="$(echo | openssl s_client -connect "$DOMAIN:443" -servername "$DOMAIN" -showcerts 2>/dev/null || true)"
if echo "$tls_out" | grep -q 'BEGIN CERTIFICATE'; then
  issuer="$(echo "$tls_out" | openssl x509 -noout -issuer 2>/dev/null | sed 's/^issuer=//' || true)"
  subj="$(echo "$tls_out" | openssl x509 -noout -subject 2>/dev/null | sed 's/^subject=//' || true)"
  enddate="$(echo "$tls_out" | openssl x509 -noout -enddate 2>/dev/null | sed 's/^notAfter=//' || true)"

  if [ -n "$enddate" ]; then
    # Seconds until expiry (date -d is GNU date; on macOS use gdate or skip).
    if date -d "$enddate" +%s >/dev/null 2>&1; then
      exp_epoch="$(date -d "$enddate" +%s)"
      now_epoch="$(date +%s)"
      days_left=$(( (exp_epoch - now_epoch) / 86400 ))
      if [ "$days_left" -lt 7 ]; then
        fail "TLS certificate expiring soon" "only ${days_left} days left (notAfter: $enddate)."
      else
        ok "TLS certificate valid for ${days_left} more days (notAfter: $enddate)."
      fi
    else
      ok "TLS certificate retrieved (notAfter: $enddate)."
      note "could not parse expiry on this OS — confirm it is in the future."
    fi
  else
    ok "TLS certificate retrieved."
  fi
  [ -n "$issuer" ] && note "issuer: $issuer"
  [ -n "$subj" ]   && note "subject: $subj"
else
  fail "TLS handshake failed" "no certificate returned by $DOMAIN:443 — Caddy may not have obtained one yet (check DNS A record + HUIBLE_DOMAIN)."
fi
echo

# ─── Health endpoint (over HTTPS, the real-user path) ────────────────────────
echo "## Health"

h="$(curl -fsS --max-time 10 "https://$DOMAIN/api/v1/health" 2>/dev/null || true)"
if echo "$h" | grep -q '"status":"ok"'; then
  ok "HTTPS /api/v1/health returns status=ok."
else
  fail "Health endpoint not ok" "got: ${h:-<no response>}"
fi

# HTTP→HTTPS redirect: port 80 should redirect, not serve plaintext content.
http_code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "http://$DOMAIN/api/v1/health" 2>/dev/null || true)"
case "$http_code" in
  30*|301|302|307|308) ok "Port 80 redirects to HTTPS (code $http_code)." ;;
  000) note "Port 80 unreachable — acceptable if Caddy only listens on 443." ;;
  *) note "Port 80 returned code $http_code — confirm it redirects to HTTPS." ;;
esac
echo

# ─── Port exposure (Postgres + app must NOT be public) ───────────────────────
echo "## Port exposure"

# Use curl connect-timeout as a fast, portable TCP-reachability probe (the
# /dev/tcp builtin has no timeout and hangs on firewalled ports). A refused/
# filtered port returns curl exit 7 (refused) or 28 (timeout) quickly; an open
# port returns 0 (HTTP answered) or 56 (recv failure = port OPEN but not HTTP).
port_open() { # 1=host 2=port — returns 0 if the port ACCEPTS a TCP connection
  local rc
  curl -sS -o /dev/null --connect-timeout 5 --max-time 6 "http://$1:$2/" >/dev/null 2>&1
  rc=$?
  # 7=refused, 28=timeout, 6=DNS fail → port NOT reachable (good).
  [ "$rc" != "7" ] && [ "$rc" != "28" ] && [ "$rc" != "6" ]
}

# Postgres 5432 must not accept connections from the public internet.
if port_open "$DOMAIN" 5432; then
  fail "Postgres 5432 is publicly reachable" "the DB port accepts external TCP — bind to 127.0.0.1 or drop the host mapping (docs/09 §8)."
else
  ok "Postgres 5432 is NOT publicly reachable."
fi

# App 8000 must not be public either (Caddy fronts ingress).
if port_open "$DOMAIN" 8000; then
  fail "App 8000 is publicly reachable" "users could bypass Caddy/TLS — bind to 127.0.0.1 (docs/09 §8)."
else
  ok "App 8000 is NOT publicly reachable."
fi
echo

echo "=== Summary: $PASS passed, $FAIL failed ==="
if [ "$FAIL" -eq 0 ]; then
  echo "RESULT: ALL_EXTERNAL_CHECKS_PASS"
  exit 0
else
  echo "RESULT: EXTERNAL_HARDENING_GAPS_REMAIN"
  exit 1
fi
