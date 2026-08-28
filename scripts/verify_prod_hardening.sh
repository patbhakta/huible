#!/usr/bin/env bash
# Production hardening verifier for docs/09 §8 (HU-1464, Stage-0.9).
#
# Runs ON THE PRODUCTION HOST. Checks every §8 "live env" item that cannot be
# proven from the repo alone. Designed to be SECRET-SAFE: it never prints the
# POSTGRES_PASSWORD value — only whether it is NOT a known default and whether
# it meets a length floor. The full transcript is safe to paste into the issue
# as the §8 sign-off evidence.
#
# Usage:
#   cd <huible deploy dir>   # the dir holding docker-compose.yml + .env
#   bash scripts/verify_prod_hardening.sh
#
# Exit code: 0 only if every check passes. A single FAIL is a hardening gap
# that must be fixed before Stage A → 1 advance.
set -u

PGUSER="${POSTGRES_USER:-huible}"
PGDB="${POSTGRES_DB:-huible}"

PASS=0; FAIL=0
ok()   { echo "  [PASS] $1"; PASS=$((PASS+1)); }
fail() { echo "  [FAIL] $1 — $2"; FAIL=$((FAIL+1)); }
note() { echo "       $1"; }

# True if a docker compose service is up. $1 = service name.
svc_up() { docker compose ps --services --filter "status=running" 2>/dev/null | grep -qx "$1"; }

# Layout detection: a DEDICATED prod host runs Caddy as a compose service
# (huible-caddy container); the STANDBY/shared layout (.245 failover, runbook
# §3.1b) excludes compose-caddy via a profile and fronts ingress with the
# host's systemd Caddy. Several checks below are layout-aware so the failover
# suite (execute_failover.sh §4) reports a truthful verdict on .245 instead of
# false-failing on containers/listeners that are absent-by-design there.
# Truth sources, in order: (1) the compose project the RUNNING huible-app
# container was started from (its config_files label), (2) the staged
# `.env -> .env.failover` symlink (same signal as execute_failover.sh G3).
# Unknown -> DEDICATED (strict mode) so ambiguity can never soften the checks.
DEDICATED_LAYOUT=1
_cfg_files="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}' huible-app 2>/dev/null || true)"
_layout_src=""
if [ -n "$_cfg_files" ]; then
  if echo "$_cfg_files" | grep -q "docker-compose.failover.yml"; then
    DEDICATED_LAYOUT=0; _layout_src="running stack config_files label"
  fi
elif [ "$(readlink -f .env 2>/dev/null)" = "$(pwd)/.env.failover" ]; then
  DEDICATED_LAYOUT=0; _layout_src=".env -> .env.failover symlink (stack not running yet)"
fi
if [ "$DEDICATED_LAYOUT" = "1" ]; then
  note "layout: DEDICATED — compose caddy expected (src: ${_layout_src:-unknown -> strict default})"
else
  note "layout: STANDBY/SHARED — system Caddy fronts ingress (src: $_layout_src)"
fi

echo "=== docs/09 §8 production hardening — live verification ==="
echo "host: $(hostname)  time: $(date -u +%FT%TZ)"
echo

# ─── Network ────────────────────────────────────────────────────────────────
echo "## Network"

# Postgres port must not be bound to a public interface. Acceptable: loopback
# bind (127.0.0.1:5432) or no host port published at all.
pg_port="$(docker compose port postgres 5432 2>/dev/null || true)"
if [ -z "$pg_port" ]; then
  ok "Postgres 5432 has no host port published (not internet-reachable)."
elif echo "$pg_port" | grep -q '^127.0.0.1:'; then
  ok "Postgres 5432 bound to loopback only ($pg_port)."
else
  fail "Postgres 5432 exposed publicly" "published as '$pg_port' — bind to 127.0.0.1:5432 or drop the mapping (docs/09 §8)."
fi

# Defense-in-depth: the app port should not be publicly reachable either
# (Caddy fronts ingress over the docker network).
app_port="$(docker compose port app 8000 2>/dev/null || true)"
if [ -z "$app_port" ]; then
  ok "App 8000 has no host port published (Caddy-only)."
elif echo "$app_port" | grep -q '^127.0.0.1:'; then
  ok "App 8000 bound to loopback only ($app_port)."
else
  fail "App 8000 exposed publicly" "published as '$app_port' — would let users bypass Caddy/TLS; bind to 127.0.0.1:8000."
fi

# Only 80/443/22 should be listening on external interfaces.
# Loopback = 127.0.0.0/8 (incl. systemd-resolved 127.0.0.53/.54) and ::1.
if command -v ss >/dev/null 2>&1; then
  pub_listeners="$(ss -tlnH 2>/dev/null | awk '{print $4}' | grep -vE '^(127\.|\[?::1\]?:|\[::1\])' || true)"
  # Huible stack ports must NEVER be publicly bound (hard fail in any layout).
  huible_leak="$(echo "$pub_listeners" | grep -E ':(8000|5432|5433)$' || true)"
  if [ -n "$huible_leak" ]; then
    fail "Huible stack port publicly bound" "found: $(echo "$huible_leak" | tr '\n' ' ') — app/PG must bind loopback only (docs/09 §8 Network)."
  else
    ok "Huible stack ports (8000/5432/5433) not publicly bound."
  fi
  leak="$(echo "$pub_listeners" | grep -vE ':(80|443|22)$' || true)"
  if [ -z "$leak" ]; then
    ok "No unexpected public listeners (only 80/443/22 on external interfaces)."
  elif [ "$DEDICATED_LAYOUT" = "1" ]; then
    fail "Unexpected public listener(s)" "found: $(echo "$leak" | tr '\n' ' ') — dedicated prod host must expose only 80/443/22."
  else
    # Shared standby host: pre-existing non-Huible services (Kestra :8080,
    # CouchDB tailnet :5984, other tooling) are documented on .245 — record
    # them, plus firewall posture, as §8 follow-ups instead of failing the
    # failover suite on facts the cutover cannot change.
    note "Shared-host non-stack listeners (pre-existing on .245, §8 follow-up): $(echo "$leak" | tr '\n' ' ')"
    if ufw status 2>/dev/null | grep -q "Status: active"; then
      ok "Host firewall (ufw) active — non-stack listeners gated by firewall policy."
    else
      note "Host firewall (ufw) INACTIVE — non-stack listeners are network-reachable; schedule §8 firewall hardening post-cutover."
    fi
  fi
else
  note "'ss' unavailable — verify host firewall manually (ufw allow 80,443,22 only)."
fi

echo

# ─── Secrets ────────────────────────────────────────────────────────────────
echo "## Secrets"

# Read the effective password from the live container WITHOUT echoing it.
# Fall back to the .env on the host if the container isn't running yet.
read_pw() {
  docker compose exec -T postgres sh -c 'echo "$POSTGRES_PASSWORD"' 2>/dev/null | tr -d '\r\n'
}
PW="$(read_pw)"
if [ -z "$PW" ]; then
  PW="$(grep -E "^POSTGRES_PASSWORD=" .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")"
fi

if [ -z "$PW" ]; then
  fail "POSTGRES_PASSWORD" "could not read it from the container or .env."
else
  if [ "$PW" = "changeme" ] || [ "$PW" = "huible_dev" ] || [ "$PW" = "postgres" ]; then
    fail "POSTGRES_PASSWORD is a known default" "value matches a .env.example/dev placeholder — set a strong unique secret."
  else
    ok "POSTGRES_PASSWORD is not a known default."
  fi
  # Length floor without revealing the value.
  len="${#PW}"
  if [ "$len" -ge 16 ]; then
    ok "POSTGRES_PASSWORD length ≥ 16 (${len} chars)."
  else
    fail "POSTGRES_PASSWORD too short" "only ${len} chars — use ≥16 of high entropy."
  fi
fi

# .env must be gitignored (repo-level check, but re-affirm on the host).
if [ -f .gitignore ] && grep -qx '.env' .gitignore; then
  ok ".env is gitignored."
else
  fail ".env not gitignored" "add '.env' to .gitignore (docs/09 §8 Secrets)."
fi

if git rev-parse --is-inside-work-tree >/dev/null 2>&1 && git ls-files --error-unmatch .env >/dev/null 2>&1; then
  fail ".env is tracked by git" "remove from version control and rotate any leaked secret."
else
  ok ".env is not tracked by git."
fi

echo

# ─── Database ───────────────────────────────────────────────────────────────
echo "## Database"

if svc_up postgres; then
  ok "postgres container is up."
else
  fail "postgres container not up" "start it before verifying DB items."
fi

# pgvector extension present.
if svc_up postgres; then
  v="$(docker compose exec -T postgres psql -U "$PGUSER" -d "$PGDB" -tAc \
       "SELECT 1 FROM pg_extension WHERE extname='vector'" 2>/dev/null | tr -d '\r\n' || true)"
  if [ "$v" = "1" ]; then
    ok "pgvector extension is enabled."
  else
    fail "pgvector not enabled" "run docker/init-db/01-enable-pgvector.sql against $PGDB."
  fi
fi

# Persistent volume present and mounted.
vol="$(docker compose config --volumes 2>/dev/null | grep -qx pgdata && echo yes || echo no)"
if [ "$vol" = "yes" ]; then
  ok "pgdata volume declared (persistent disk)."
  note "confirm on the host that the backing disk is the persistent one."
else
  fail "pgdata volume missing" "pgdata must be a named/docker-managed persistent volume."
fi

echo

# ─── Backup (configured + tested) ───────────────────────────────────────────
echo "## Backup"

# A live round-trip: pg_dump to /tmp, confirm non-empty, clean up. This proves
# the documented backup path actually works end-to-end in this environment.
if svc_up postgres; then
  dump_tmp="/tmp/huible_hardening_probe.dump"
  if docker compose exec -T postgres pg_dump -U "$PGUSER" --format=custom --file="$dump_tmp" "$PGDB" >/dev/null 2>&1; then
    sz="$(docker compose exec -T postgres stat -c '%s' "$dump_tmp" 2>/dev/null | tr -d '\r\n' || echo 0)"
    if [ "${sz:-0}" -gt 0 ]; then
      ok "Backup path works (test pg_dump produced ${sz} bytes, then cleaned up)."
    else
      fail "Backup path produced empty dump" "investigate pg_dump / permissions."
    fi
    docker compose exec -T postgres rm -f "$dump_tmp" >/dev/null 2>&1 || true
  else
    fail "Backup path failed" "pg_dump could not complete — verify role/permissions."
  fi
fi

# Backup automation present? (cron or systemd timer.) Informational, not fatal.
if crontab -l 2>/dev/null | grep -qi 'pg_dump'; then
  ok "Backup automation detected (cron mentions pg_dump)."
elif systemctl list-timers --all 2>/dev/null | grep -qi 'huible\|pg_dump'; then
  ok "Backup automation detected (systemd timer)."
else
  note "No backup cron/timer auto-detected — confirm a daily pg_dump is scheduled (docs/09 §9.2)."
fi

echo

# ─── Application ─────────────────────────────────────────────────────────────
echo "## Application"

# Restart policy per container must be unless-stopped. On the STANDBY/shared
# layout there is no huible-caddy container by design (systemd Caddy fronts
# ingress, runbook §3.1b) — check the systemd unit's active+enabled state,
# which is the equivalent resilience guarantee.
for c in app postgres; do
  rp="$(docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' "huible-$c" 2>/dev/null || true)"
  if [ "$rp" = "unless-stopped" ]; then
    ok "huible-$c restart policy = unless-stopped."
  else
    fail "huible-$c restart policy" "is '$rp' — set restart: unless-stopped (docs/09 §8 Application)."
  fi
done
if [ "$DEDICATED_LAYOUT" = "1" ]; then
  rp="$(docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' "huible-caddy" 2>/dev/null || true)"
  if [ "$rp" = "unless-stopped" ]; then
    ok "huible-caddy restart policy = unless-stopped."
  else
    fail "huible-caddy restart policy" "is '$rp' — set restart: unless-stopped (docs/09 §8 Application)."
  fi
else
  if systemctl is-active --quiet caddy && systemctl is-enabled --quiet caddy; then
    ok "system caddy active+enabled (standby ingress resilience)."
  else
    fail "system caddy not active+enabled" "standby layout depends on the host Caddy unit for ingress (runbook §3.1b)."
  fi
fi

# Health endpoint via the app (loopback).
h="$(curl -fsS --max-time 5 http://127.0.0.1:8000/api/v1/health 2>/dev/null || true)"
if echo "$h" | grep -q '"status":"ok"'; then
  ok "App /health returns status=ok."
else
  fail "App /health not ok" "got: ${h:-<no response>}"
fi

echo

# ─── Monitoring (disk alert, HU-1742) ───────────────────────────────────────
echo "## Monitoring (disk)"

# Prometheus + node_exporter from the compose `monitoring` profile must be up
# with unless-stopped (docs/09 §8 disk item: "< 10GiB free triggers alert").
for c in prometheus node-exporter; do
  rp="$(docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' "huible-$c" 2>/dev/null || true)"
  if [ "$rp" = "unless-stopped" ]; then
    ok "huible-$c restart policy = unless-stopped."
  else
    fail "huible-$c not running with unless-stopped" "start the monitoring profile: docker compose --profile monitoring up -d (docs/09 §8 disk item)."
  fi
done

# Monitoring ports must stay loopback-only (no new public listeners).
for ce in "huible-prometheus 9090" "huible-node-exporter 9100"; do
  cn="${ce% *}"; cp="${ce#* }"
  bind="$(docker port "$cn" "$cp/tcp" 2>/dev/null | head -1 || true)"
  if echo "$bind" | grep -q '^127.0.0.1:'; then
    ok "$cn binds loopback only ($bind)."
  else
    fail "$cn port $cp not loopback-bound" "got '${bind:-not published}' — monitoring must not add public listeners (docs/09 §8 Network)."
  fi
done

# Prometheus API: disk rule loaded + healthy, all scrape targets up.
prom() { curl -fsS --max-time 5 "http://127.0.0.1:9090/api/v1/$1" 2>/dev/null || true; }
if command -v jq >/dev/null 2>&1; then
  rh="$(prom rules | jq -r '.data.groups[].rules[]? | select(.name=="HuibleDiskFreeLow") | .health' 2>/dev/null | head -1)"
  if [ "$rh" = "ok" ]; then
    ok "HuibleDiskFreeLow rule loaded and healthy in live Prometheus."
  else
    fail "HuibleDiskFreeLow rule not healthy" "health='${rh:-not found}' — check the rule_files mount + examples/prometheus-alerts.yml."
  fi
  tj="$(prom targets | jq -r '[.data.activeTargets[]?.health] | if length > 0 and all(. == "up") then "up" else "down" end' 2>/dev/null)"
  if [ "$tj" = "up" ]; then
    ok "All Prometheus scrape targets up (huible + node)."
  else
    fail "Prometheus scrape target(s) down" "inspect: curl -s 127.0.0.1:9090/api/v1/targets"
  fi
  # Current disk posture — informational: a firing alert means ops action is
  # needed (free space), not that the monitoring control is missing. Matches
  # the HU-2131 recalibration: absolute free bytes, not ratio.
  fr="$(curl -fsSG --max-time 5 'http://127.0.0.1:9090/api/v1/query' --data-urlencode 'query=node_filesystem_avail_bytes{fstype!~"tmpfs|overlay|squashfs|devtmpfs|iso9660"}' 2>/dev/null | jq -r '.data.result[]? | "\(.value[1] | tonumber / 1073741824 * 10 | round / 10)GiB free on \(.metric.mountpoint) (\(.metric.fstype))"' 2>/dev/null || true)"
  if [ -n "$fr" ]; then
    ok "Disk free bytes queryable via node_exporter ($fr)."
  else
    fail "Disk free bytes not queryable" "node_exporter scrape broken — check the node job target."
  fi
  astate="$(prom alerts | jq -r '.data.alerts[]? | select(.labels.alertname=="HuibleDiskFreeLow") | .state' 2>/dev/null | head -1)"
  if [ -n "$astate" ]; then
    ok "HuibleDiskFreeLow live in the evaluator (state: $astate)."
    if [ "$astate" = "firing" ]; then
      note "ALERT FIRING — host disk is below the 10GiB absolute floor: free space / extend the volume now."
    fi
  else
    note "HuibleDiskFreeLow not pending/firing (all watched filesystems above the 10GiB free floor)."
  fi
else
  note "'jq' unavailable — verify rule/targets manually via 127.0.0.1:9090/api/v1/{rules,targets}."
fi

echo

# ─── TLS (Caddy, host-side signal) ──────────────────────────────────────────
echo "## TLS"
echo "  NOTE: definitive TLS proof is an EXTERNAL check:"
echo "        curl -v https://\${HUIBLE_DOMAIN}/api/v1/health   (valid cert, 200)"
echo "  Host-side Caddy signal below."

if docker compose logs caddy 2>/dev/null | grep -qi 'certificate obtained\|obtained certificate\|serving initial configuration'; then
  ok "Caddy reports a TLS certificate obtained/provisioned."
else
  note "No 'certificate obtained' line found in recent Caddy logs — check after first boot / DNS A record."
fi

echo
echo "=== Summary: $PASS passed, $FAIL failed ==="
if [ "$FAIL" -eq 0 ]; then
  echo "RESULT: ALL_LIVE_CHECKS_PASS"
  exit 0
else
  echo "RESULT: HARDENING_GAPS_REMAIN"
  exit 1
fi
