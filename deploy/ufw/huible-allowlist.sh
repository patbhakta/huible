#!/usr/bin/env bash
# ufw allowlist for the standby .245 — HU-1672 AC #2 (docs/09 §8 defense-in-depth).
#
# ufw is currently INACTIVE on .245. This script applies the intended policy
# in one idempotent pass and verifies reachability after enable:
#
#   default deny incoming / allow outgoing
#   allow 22/tcp                       (SSH)
#   allow 80,443/tcp                   (system Caddy — public app ingress)
#   allow in on <tailscale iface>      (Kestra :8080 + CouchDB :5984 are
#                                       tailnet-only; loopback listeners such
#                                       as app :8000 and PG :5432/:5433 are
#                                       unaffected by default policy)
#
# Lockout safety: enabling a firewall over SSH can lock the operator out.
# Before running for real, read docs/runbooks/provider-console-break-glass.md
# (provider-console power/console recovery). The script REFUSES to enable
# without an explicit --confirm-break-glass flag acknowledging that path.
#
# Usage (ON .245, post-cutover):
#   bash deploy/ufw/huible-allowlist.sh --check                 # dry-run report
#   bash deploy/ufw/huible-allowlist.sh --confirm-break-glass   # apply + verify
set -euo pipefail

TS_IFACE="${TS_IFACE:-tailscale0}"
TS_IP="${TS_IP:-100.101.235.117}"   # this host's tailnet IP (runbook §1)
CONFIRM=0; CHECK_ONLY=0
for a in "$@"; do
  case "$a" in
    --check)              CHECK_ONLY=1 ;;
    --confirm-break-glass) CONFIRM=1 ;;
    *) echo "unknown arg: $a" >&2; exit 2 ;;
  esac
done

ok()   { echo "ok:   $*"; }
bad()  { echo "FAIL: $*" >&2; FAILED=1; }
note() { echo "note: $*"; }
FAILED=0

command -v ufw >/dev/null || { echo "FAIL: ufw not installed (apt install ufw)" >&2; exit 1; }

echo "== Huible ufw allowlist ($TS_IFACE, tailnet $TS_IP) =="
[ "$CHECK_ONLY" = 1 ] && note "CHECK MODE — no changes will be made"

# ── Preflight: interface + current policy facts ────────────────────────────
ip link show "$TS_IFACE" >/dev/null 2>&1 \
  && ok "tailscale interface present: $TS_IFACE" \
  || bad "interface $TS_IFACE missing — set TS_IFACE=<real iface> and re-run"
if ufw status | grep -q "Status: active"; then
  note "ufw already active — re-applying rules is idempotent, enable will be a no-op"
else
  note "ufw INACTIVE (expected pre-HU-1672 state on .245)"
fi

if [ "$FAILED" = 1 ]; then exit 1; fi
if [ "$CHECK_ONLY" = 1 ]; then
  echo "check complete — run with --confirm-break-glass to apply"
  exit 0
fi
[ "$CONFIRM" = 1 ] || { echo "REFUSED: firewall enable over SSH needs --confirm-break-glass (recovery: docs/runbooks/provider-console-break-glass.md)" >&2; exit 2; }

# ── Policy + rules (all idempotent; ufw dedupes identical rules) ───────────
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp  comment 'ssh'
ufw allow 80/tcp  comment 'system caddy http'
ufw allow 443/tcp comment 'system caddy https'
ufw allow in on "$TS_IFACE" comment 'tailnet: kestra 8080 + couchdb 5984'
ufw --force enable
ok "ufw enabled with allowlist (22, 80, 443, $TS_IFACE)"

# ── Post-enable reachability verification (AC #2) ──────────────────────────
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://$TS_IP:8080/" || true)"
echo "$code" | grep -qE '200|307' \
  && ok "Kestra :8080 reachable on tailnet ($code)" \
  || bad "Kestra :8080 NOT reachable on tailnet (got ${code:-none})"
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://$TS_IP:5984/" || true)"
echo "$code" | grep -qE '200|401' \
  && ok "CouchDB :5984 reachable on tailnet ($code)" \
  || bad "CouchDB :5984 NOT reachable on tailnet (got ${code:-none})"
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8000/ || true)"
[ "$code" != "000" ] && [ -n "$code" ] \
  && ok "app :8000 reachable on loopback ($code)" \
  || bad "app :8000 NOT reachable on loopback"
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1/ || true)"
[ "$code" != "000" ] && [ -n "$code" ] \
  && ok "system Caddy :80 on loopback ($code)" \
  || bad "system Caddy :80 NOT reachable on loopback"

echo
if [ "$FAILED" = 1 ]; then
  echo "UFW_ENABLE_PARTIAL — rules applied but a post-check failed; DO NOT close the SSH session." >&2
  echo "Recovery: docs/runbooks/provider-console-break-glass.md, or 'ufw disable' from this session." >&2
  exit 1
fi
echo "UFW_ALLOWLIST_PASS — allowlist active, all post-checks green (HU-1672 AC #2)"
