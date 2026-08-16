#!/usr/bin/env bash
# Prod posture verifier — ICMP + SSH + edge + Tailscale against the CURRENT prod.
#
# Canonical addresses (single source of truth — HU-1777/HU-1823 lesson):
#   Since the 2026-08-15 cutover (HU-1715), production is served from
#   208.84.102.245 (tailnet 100.101.235.117, node ip-208-84-102-245).
#   The old prod VPS 208.84.102.243 is DECOMMISSIONED/DARK — probing it for
#   prod posture has twice opened false incidents (HU-1777 against a wrong IP,
#   HU-1823 against .243 itself while prod was green the whole time).
#
# Defaults therefore target .245. The retired LiveSync stack (Kestra :8080,
# CouchDB :5984 — retired with HU-1706/HU-1681) is NOT probed in default mode;
# those sections only run when explicitly requested (see CHECK_RETIRED_STACK).
#
# Usage:
#   bash scripts/verify_vps_recovery.sh
#       # verifies current prod (.245): ICMP, SSH :22, edge :80 → 308,
#       # Tailscale node online. RESULT: VPS_RECOVERED iff all pass.
#   PROBE_LEGACY_243=1 [CHECK_RETIRED_STACK=1] bash scripts/verify_vps_recovery.sh
#       # opts into probing the decommissioned .243 (e.g. archaeology or
#       # operator power-cycle verification of the old box). Legacy mode also
#       # re-enables the Kestra/CouchDB checks by default.
#
# Exit code: 0 only if every requested check passes; 1 = NOT_READY;
# 2 = refused (legacy target without opt-in).
set -u

VPS_PUBLIC="${VPS_PUBLIC:-208.84.102.245}"
VPS_TS_IP="${VPS_TS_IP:-100.101.235.117}"        # tailscale: ip-208-84-102-245
KESTRA_TS_IP="${KESTRA_TS_IP:-100.101.235.117}"  # retired stack; legacy only
TS_NODE_VPS="${TS_NODE_VPS:-ip-208-84-102-245}"
TS_NODE_KESTRA="${TS_NODE_KESTRA:-ip-208-84-102-245}"
PROBE_LEGACY_243="${PROBE_LEGACY_243:-0}"

if [ "$VPS_PUBLIC" = "208.84.102.243" ] && [ "$PROBE_LEGACY_243" != "1" ]; then
  echo "=== REFUSED: legacy target without opt-in ===" >&2
  echo "208.84.102.243 is DECOMMISSIONED (HU-1715 cutover moved prod to .245)." >&2
  echo "Probing it for prod posture opened false incidents HU-1777 and HU-1823." >&2
  echo "If you really mean it: PROBE_LEGACY_243=1 $0" >&2
  echo "Otherwise run with defaults (current prod .245) or see" >&2
  echo "docs/runbooks/vps-failover-to-standby.md § Canonical addresses." >&2
  exit 2
fi

# Retired-stack checks (Kestra/CouchDB) default ON only in legacy mode.
CHECK_RETIRED_STACK="${CHECK_RETIRED_STACK:-$PROBE_LEGACY_243}"

PASS=0; FAIL=0
ok()   { echo "  [PASS] $1"; PASS=$((PASS+1)); }
fail() { echo "  [FAIL] $1 — $2"; FAIL=$((FAIL+1)); }
note() { echo "       $1"; }

# curl-based TCP reachability probe: 0 = port accepts a connection.
# 7=refused, 28=timeout, 6=DNS fail → not reachable.
port_open() { # 1=host 2=port 3=label
  local rc
  curl -sS -o /dev/null --connect-timeout 5 --max-time 6 "http://$1:$2/" >/dev/null 2>&1
  rc=$?
  [ "$rc" != "7" ] && [ "$rc" != "28" ] && [ "$rc" != "6" ]
}

echo "=== Prod posture verification (canonical: .245 since HU-1715) ==="
echo "target: $VPS_PUBLIC  time: $(date -u +%FT%TZ)  from: $(hostname)"
echo

# ─── ICMP reachability ────────────────────────────────────────────────────────
echo "## ICMP"
if ping -c 2 -W 3 "$VPS_PUBLIC" >/dev/null 2>&1; then
  ok "ICMP replies from $VPS_PUBLIC."
else
  fail "ICMP unreachable" "$VPS_PUBLIC does not reply — host down or firewall dropping echo."
fi
echo

# ─── TCP ports (SSH) ──────────────────────────────────────────────────────────
echo "## TCP ports"
# SSH :22 must accept connections (operator + agent SSH path).
if port_open "$VPS_PUBLIC" 22 ssh; then ok "SSH :22 open on $VPS_PUBLIC."; else fail "SSH :22 closed" "$VPS_PUBLIC:22 unreachable."; fi

if [ "$CHECK_RETIRED_STACK" = "1" ]; then
  # Kestra :8080 on both public and tailnet IPs (legacy .243 stack only).
  if port_open "$VPS_PUBLIC" 8080 kestra; then ok "Kestra :8080 open on $VPS_PUBLIC."; else fail "Kestra :8080 closed" "$VPS_PUBLIC:8080 unreachable."; fi
  if port_open "$KESTRA_TS_IP" 8080 kestra-ts; then ok "Kestra :8080 open on tailnet $KESTRA_TS_IP."; else note "Kestra :8080 not reachable on tailnet $KESTRA_TS_IP (acceptable if only public-bound)."; fi
  # CouchDB :5984 — typically localhost-bound on the VPS. Reachable from the
  # agent host only if intentionally exposed; a fail here is NOT a hard failure
  # (the rotation runs on-box), but a pass means the agent host can drive it
  # remotely.
  if port_open "$VPS_TS_IP" 5984 couchdb-ts; then
    ok "CouchDB :5984 open on tailnet $VPS_TS_IP (remote rotation possible)."
  else
    note "CouchDB :5984 not reachable on $VPS_TS_IP — expected if localhost-bound; rotation must run on-box (SSH/console)."
  fi
else
  note "Kestra :8080 / CouchDB :5984 not probed — retired with the LiveSync stack (HU-1706/HU-1681). Set CHECK_RETIRED_STACK=1 to probe them anyway."
fi
echo

if [ "$CHECK_RETIRED_STACK" = "1" ]; then
  # ─── Kestra HTTP responds (legacy stack) ────────────────────────────────────
  echo "## Kestra HTTP"
  kestra_code="$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 8 "http://$VPS_PUBLIC:8080/" 2>/dev/null || true)"
  case "$kestra_code" in
    ""|000) fail "Kestra HTTP not responding" "$VPS_PUBLIC:8080 returned no response." ;;
    *) ok "Kestra HTTP responds (code $kestra_code on :8080/)."
       note "authenticated API checks happen in the rotation runbook (HU-1500)." ;;
  esac
  echo

  # ─── CouchDB responds (unauthenticated welcome banner; legacy) ──────────────
  echo "## CouchDB banner"
  # GET / on CouchDB returns {"couchdb":"Welcome",...} without auth. Hitting the
  # tailnet IP only; if localhost-bound this silently notes the skip.
  couch="$(curl -fsS --connect-timeout 5 --max-time 8 "http://$VPS_TS_IP:5984/" 2>/dev/null || true)"
  if echo "$couch" | grep -q '"couchdb"'; then
    ver="$(echo "$couch" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("version","?"))' 2>/dev/null || echo '?')"
    ok "CouchDB responds (version $ver) on $VPS_TS_IP:5984/."
  else
    note "CouchDB welcome banner not reachable on tailnet IP — confirm on-box (curl localhost:5984/) during rotation."
  fi
  echo
fi

# ─── Edge :80 health pin (current prod; HU-1672) ──────────────────────────────
if [ "$CHECK_RETIRED_STACK" != "1" ]; then
  echo "## Edge :80"
  edge_code="$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 8 "http://$VPS_PUBLIC/" 2>/dev/null || true)"
  case "$edge_code" in
    308) ok "Edge :80 → 308 redirect to https (Caddy health pin)." ;;
    ""|000) fail "Edge :80 not responding" "$VPS_PUBLIC:80 returned no response." ;;
    *) fail "Edge :80 unexpected code" "$VPS_PUBLIC:80 returned $edge_code (expected 308)." ;;
  esac
  echo
fi

# ─── Tailscale node status ────────────────────────────────────────────────────
echo "## Tailscale nodes"
if command -v tailscale >/dev/null 2>&1; then
  ts_out="$(tailscale status 2>/dev/null || true)"
  for node in "$TS_NODE_VPS" "$TS_NODE_KESTRA"; do
    node_line="$(echo "$ts_out" | grep -E "$node" || true)"
    if [ -z "$node_line" ]; then
      fail "Tailscale node '$node' missing" "not found in 'tailscale status'."
    # A node is online iff its line has NO 'offline' token. NB: offline nodes
    # still report an 'active; relay' prefix, so matching 'active' alone is a
    # false positive — the absence of 'offline' is the real signal.
    elif echo "$node_line" | grep -qv offline; then
      ok "Tailscale node '$node' is online."
    else
      fail "Tailscale node '$node' offline" "$(echo "$node_line" | sed -E 's/.*('"$node"'.*)/\1/')"
    fi
  done
else
  note "tailscale CLI not available on this host — confirm the prod node is online (no 'offline' in its row) in the admin console."
fi
echo

echo "=== Summary: $PASS passed, $FAIL failed ==="
if [ "$FAIL" -eq 0 ]; then
  echo "RESULT: VPS_RECOVERED"
  echo "next: current-prod posture is green; for app-level checks run the on-host probes (docker compose ps, /api/v1/health)."
  exit 0
else
  echo "RESULT: VPS_NOT_READY — do not proceed; investigate before paging anyone."
  exit 1
fi
