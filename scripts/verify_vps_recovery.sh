#!/usr/bin/env bash
# VPS recovery verifier — run the moment the prod VPS is reported powered back on.
#
# Context: HU-1501 (prod VPS 208.84.102.243 powered down) blocks HU-1500 (CouchDB
# admin credential rotation) and the whole real-user launch chain. When the board
# accepts confirmation card `a4e92acc` ("I powered the VPS on"), this script is the
# FIRST thing to run: it proves Kestra + CouchDB + Tailscale are actually back before
# any flow is re-enabled or the HU-1500 rotation begins.
#
# Runs from the agent host (208.84.102.245) or any machine on the tailnet.
# Complements verify_prod_external.sh (which checks the public HTTPS surface).
#
# Usage:
#   bash scripts/verify_vps_recovery.sh
#       # optional overrides (otherwise uses the known prod targets below):
#   VPS_PUBLIC=208.84.102.243 VPS_TS_IP=100.109.142.4 KESTRA_TS_IP=100.75.34.75 \
#     bash scripts/verify_vps_recovery.sh
#
# Exit code: 0 only if every check passes. A single FAIL means the VPS is NOT
# ready — do not proceed to the HU-1500 rotation; re-engage the operator.
set -u

VPS_PUBLIC="${VPS_PUBLIC:-208.84.102.243}"
VPS_TS_IP="${VPS_TS_IP:-100.109.142.4}"        # tailscale: ip-208-84-102-243
KESTRA_TS_IP="${KESTRA_TS_IP:-100.75.34.75}"   # tailscale: kestra-on-vps
TS_NODE_VPS="${TS_NODE_VPS:-ip-208-84-102-243}"
TS_NODE_KESTRA="${TS_NODE_KESTRA:-kestra-on-vps}"

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

echo "=== VPS recovery verification (HU-1501 closure gate) ==="
echo "target: $VPS_PUBLIC  time: $(date -u +%FT%TZ)  from: $(hostname)"
echo

# ─── ICMP reachability ────────────────────────────────────────────────────────
echo "## ICMP"
if ping -c 2 -W 3 "$VPS_PUBLIC" >/dev/null 2>&1; then
  ok "ICMP replies from $VPS_PUBLIC."
else
  fail "ICMP unreachable" "$VPS_PUBLIC does not reply — host still down or firewall dropping echo."
fi
echo

# ─── TCP ports (SSH + Kestra + CouchDB) on public + tailnet IPs ───────────────
echo "## TCP ports"
# SSH :22 must accept connections (operator + agent SSH path).
if port_open "$VPS_PUBLIC" 22 ssh; then ok "SSH :22 open on $VPS_PUBLIC."; else fail "SSH :22 closed" "$VPS_PUBLIC:22 unreachable."; fi
# Kestra :8080 on both public and tailnet IPs.
if port_open "$VPS_PUBLIC" 8080 kestra; then ok "Kestra :8080 open on $VPS_PUBLIC."; else fail "Kestra :8080 closed" "$VPS_PUBLIC:8080 unreachable."; fi
if port_open "$KESTRA_TS_IP" 8080 kestra-ts; then ok "Kestra :8080 open on tailnet $KESTRA_TS_IP."; else note "Kestra :8080 not reachable on tailnet $KESTRA_TS_IP (acceptable if only public-bound)."; fi
# CouchDB :5984 — typically localhost-bound on the VPS. Reachable from the agent
# host only if intentionally exposed; a fail here is NOT a hard failure (the
# rotation runs on-box), but a pass means the agent host can drive it remotely.
if port_open "$VPS_TS_IP" 5984 couchdb-ts; then
  ok "CouchDB :5984 open on tailnet $VPS_TS_IP (remote rotation possible)."
else
  note "CouchDB :5984 not reachable on $VPS_TS_IP — expected if localhost-bound; rotation must run on-box (SSH/console)."
fi
echo

# ─── Kestra HTTP responds ─────────────────────────────────────────────────────
echo "## Kestra HTTP"
kestra_code="$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 8 "http://$VPS_PUBLIC:8080/" 2>/dev/null || true)"
case "$kestra_code" in
  ""|000) fail "Kestra HTTP not responding" "$VPS_PUBLIC:8080 returned no response." ;;
  *) ok "Kestra HTTP responds (code $kestra_code on :8080/)."
     note "authenticated API checks happen in the rotation runbook (HU-1500)." ;;
esac
echo

# ─── CouchDB responds (unauthenticated welcome banner) ───────────────────────
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
  note "tailscale CLI not available on this host — confirm both nodes are online (no 'offline' in their row) in the admin console."
fi
echo

echo "=== Summary: $PASS passed, $FAIL failed ==="
if [ "$FAIL" -eq 0 ]; then
  echo "RESULT: VPS_RECOVERED"
  echo "next: proceed with HU-1500 rotation runbook BEFORE resuming flows/webhooks."
  exit 0
else
  echo "RESULT: VPS_NOT_READY — do not proceed; re-engage the operator."
  exit 1
fi
