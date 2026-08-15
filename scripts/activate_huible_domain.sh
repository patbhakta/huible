#!/usr/bin/env bash
# HU-1743: activate the production HUIBLE_DOMAIN on the standby (.245).
# Implements docs/runbooks/vps-failover-to-standby.md §3.6 for the system
# Caddy + compose stack installed by scripts/execute_failover.sh.
#
# Usage:
#   bash scripts/activate_huible_domain.sh --check    # preconditions only, no changes
#   bash scripts/activate_huible_domain.sh <domain>   # activate (idempotent)
#
# What activation does (runbook §3.6):
#   1. Gate: DNS A record for <domain> must already resolve to this host
#      (208.84.102.245) — ACME HTTP-01 fails otherwise. Grey-cloud (DNS only)
#      is required so the public cert is provisioned by Caddy on this host,
#      matching the kestra.bhakta.us pattern (verify_prod_external.sh checks
#      the ORIGIN cert, not a CDN edge cert).
#   2. .env.failover: HUIBLE_DOMAIN=<domain> (was localhost placeholder).
#   3. systemd drop-in for caddy.service: Environment=HUIBLE_DOMAIN=<domain>
#      (systemd caddy has no env of its own — the f25acdc localhost-default
#      fix relies on the site-block default; the real domain must be explicit).
#   4. caddy validate (with env) -> systemctl reload caddy (reload, never
#      restart — kestra/brain/investinme sites must not blip).
#   5. Wait (bounded) for ACME issuance, then run verify_prod_external.sh.
#
# Every run tees evidence to logs/activate-domain-<UTC timestamp>.log.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

STANDBY_PUBLIC_IP="208.84.102.245"
ENV_FAILOVER=".env.failover"
DROPIN_DIR="/etc/systemd/system/caddy.service.d"
DROPIN_FILE="$DROPIN_DIR/huible-domain.conf"
LOG_DIR="logs"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/activate-domain-$(date -u +%Y%m%dT%H%M%SZ).log"

pass=0; fail=0
ok()   { echo "  [PASS] $*" | tee -a "$LOG"; pass=$((pass+1)); }
bad()  { echo "  [FAIL] $*" | tee -a "$LOG"; fail=$((fail+1)); }
note() { echo "       $*" | tee -a "$LOG"; }
die()  { echo "ABORT: $*" | tee -a "$LOG"; echo "RESULT: ABORTED" | tee -a "$LOG"; exit 1; }

resolv_a() { # 1=domain -> prints first A record via public resolver (local
             # resolver may SERVFAIL on the operator's zone)
  timeout 8 dig +short +time=5 +tries=1 @1.1.1.1 "$1" A 2>/dev/null | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | head -1
}

wait_for_origin_cert() { # 1=domain 2=max_seconds — 0 once a cert is served
  local d="$1" left="$2" issuer
  while [ "$left" -gt 0 ]; do
    issuer="$(echo | timeout 8 openssl s_client -connect "$d:443" -servername "$d" 2>/dev/null \
      | openssl x509 -noout -issuer 2>/dev/null | sed 's/^issuer=//' || true)"
    if [ -n "$issuer" ]; then
      case "$issuer" in
        *Let*s\ Encrypt*|*ZeroSSL*|*Google\ Trust*|*DigiCert*|*Sectigo*) echo "$issuer"; return 0 ;;
        *) echo "$issuer"; return 0 ;; # print issuer; caller judges public-CA-ness
      esac
    fi
    sleep 5; left=$((left - 5))
  done
  return 1
}

MODE_CHECK=0; DOMAIN=""
case "${1:-}" in
  --check) MODE_CHECK=1 ;;
  "") echo "usage: $0 --check | $0 <domain>" >&2; exit 64 ;;
  *) DOMAIN="${1#https://}"; DOMAIN="${DOMAIN#http://}"; DOMAIN="${DOMAIN%%/*}" ;;
esac

echo "=== HUIBLE_DOMAIN activation | mode=$( [ $MODE_CHECK = 1 ] && echo check || echo "activate:$DOMAIN" ) ===" | tee -a "$LOG"
echo "host: $(hostname -f)  time: $(date -u +%FT%TZ)" | tee -a "$LOG"

echo "## Preconditions" | tee -a "$LOG"
command -v dig >/dev/null && ok "dig available" || bad "dig missing (dnsutils)"
command -v openssl >/dev/null && ok "openssl available" || bad "openssl missing"
systemctl is-active caddy >/dev/null 2>&1 && ok "system caddy active" || bad "system caddy not active"
[ -f "$ENV_FAILOVER" ] && ok "$ENV_FAILOVER present" || bad "$ENV_FAILOVER missing"
[ -f /etc/caddy/huible-site.caddy ] && ok "huible-site.caddy installed" || bad "/etc/caddy/huible-site.caddy missing"
grep -q "import huible-site.caddy" /etc/caddy/Caddyfile && ok "Caddyfile imports huible-site" || bad "Caddyfile does not import huible-site"
curl -fsS --max-time 8 http://127.0.0.1:8000/api/v1/health 2>/dev/null | grep -q '"status":"ok"' \
  && ok "app health ok (127.0.0.1:8000)" || bad "app health not ok (127.0.0.1:8000)"
[ "$fail" -gt 0 ] && die "preconditions failed"
[ "$MODE_CHECK" = 1 ] && { echo "RESULT: CHECK_OK ($(date -u +%FT%TZ))" | tee -a "$LOG"; exit 0; }

# ─── Activation ──────────────────────────────────────────────────────────────
echo "## DNS gate" | tee -a "$LOG"
A_IP="$(resolv_a "$DOMAIN" || true)"
[ -n "$A_IP" ] || die "no public A record for $DOMAIN (create it first, grey-cloud/DNS-only, -> $STANDBY_PUBLIC_IP)"
if [ "$A_IP" = "$STANDBY_PUBLIC_IP" ]; then
  ok "$DOMAIN -> $A_IP (this host)"
else
  die "$DOMAIN -> $A_IP, expected $STANDBY_PUBLIC_IP — fix the A record (and make it DNS-only/grey-cloud, not proxied)"
fi

echo "## .env.failover" | tee -a "$LOG"
if grep -q "^HUIBLE_DOMAIN=" "$ENV_FAILOVER"; then
  sed -i "s|^HUIBLE_DOMAIN=.*|HUIBLE_DOMAIN=$DOMAIN|" "$ENV_FAILOVER"
  ok "HUIBLE_DOMAIN=$DOMAIN written"
else
  { echo ""; echo "HUIBLE_DOMAIN=$DOMAIN"; } >> "$ENV_FAILOVER"
  ok "HUIBLE_DOMAIN=$DOMAIN appended"
fi

echo "## systemd caddy env" | tee -a "$LOG"
mkdir -p "$DROPIN_DIR"
if grep -q "^Environment=HUIBLE_DOMAIN=" "$DROPIN_FILE" 2>/dev/null; then
  sed -i "s|^Environment=HUIBLE_DOMAIN=.*|Environment=HUIBLE_DOMAIN=$DOMAIN|" "$DROPIN_FILE"
else
  printf '[Service]\nEnvironment=HUIBLE_DOMAIN=%s\n' "$DOMAIN" > "$DROPIN_FILE"
fi
ok "drop-in $DROPIN_FILE -> HUIBLE_DOMAIN=$DOMAIN"
systemctl daemon-reload && ok "daemon-reload done"

echo "## caddy validate + reload" | tee -a "$LOG"
HUIBLE_DOMAIN="$DOMAIN" caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1 \
  && ok "caddy validate (with HUIBLE_DOMAIN=$DOMAIN)" || die "caddy validate FAILED — not reloading"
systemctl reload caddy && ok "caddy reloaded (no restart)"

echo "## ACME issuance (bounded wait)" | tee -a "$LOG"
ISSUER="$(wait_for_origin_cert "$DOMAIN" 180 || true)"
if [ -n "$ISSUER" ]; then
  ok "cert served for $DOMAIN — issuer: $ISSUER"
  case "$ISSUER" in
    *Let*s\ Encrypt*|*ZeroSSL*|*Google\ Trust*|*DigiCert*) note "public CA — good for §8 evidence" ;;
    *) note "NON-public issuer (internal/self-signed?) — §8 requires public-CA; check A record is grey-cloud" ;;
  esac
else
  bad "no cert served for $DOMAIN after 180s (ACME may still be retrying — check /var/log/caddy or journalctl -u caddy)"
fi

echo "## External verification" | tee -a "$LOG"
if bash scripts/verify_prod_external.sh "$DOMAIN" 2>&1 | tee -a "$LOG"; then
  ok "verify_prod_external.sh PASSED for $DOMAIN"
else
  bad "verify_prod_external.sh FAILED for $DOMAIN"
fi

echo "=== Summary: $pass passed, $fail failed ===" | tee -a "$LOG"
if [ "$fail" -eq 0 ]; then
  echo "RESULT: DOMAIN_ACTIVATED ($DOMAIN)" | tee -a "$LOG"
else
  echo "RESULT: DOMAIN_ACTIVATED_WITH_GAPS ($DOMAIN)" | tee -a "$LOG"
  exit 1
fi
