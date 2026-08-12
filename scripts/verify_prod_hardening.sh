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
if command -v ss >/dev/null 2>&1; then
  pub_listeners="$(ss -tlnH 2>/dev/null | awk '{print $4}' | grep -vE '^(127\.0\.0\.1|\[?::1\]?):' || true)"
  leak="$(echo "$pub_listeners" | grep -vE ':(80|443|22)$' || true)"
  if [ -z "$leak" ]; then
    ok "No unexpected public listeners (only 80/443/22 on external interfaces)."
  else
    fail "Unexpected public listener(s)" "found: $(echo "$leak" | tr '\n' ' ')"
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

# Restart policy per container must be unless-stopped.
for c in app postgres caddy; do
  rp="$(docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' "huible-$c" 2>/dev/null || true)"
  if [ "$rp" = "unless-stopped" ]; then
    ok "huible-$c restart policy = unless-stopped."
  else
    fail "huible-$c restart policy" "is '$rp' — set restart: unless-stopped (docs/09 §8 Application)."
  fi
done

# Health endpoint via the app (loopback).
h="$(curl -fsS --max-time 5 http://127.0.0.1:8000/api/v1/health 2>/dev/null || true)"
if echo "$h" | grep -q '"status":"ok"'; then
  ok "App /health returns status=ok."
else
  fail "App /health not ok" "got: ${h:-<no response>}"
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
