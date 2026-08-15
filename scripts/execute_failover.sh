#!/usr/bin/env bash
# HU-1644 / HU-1501: one-command cutover of Huible prod from VPS .243 to
# standby .245, encoding docs/runbooks/vps-failover-to-standby.md §2-§4.
#
# Safety model:
#   default / --check : pre-flight gates ONLY — no containers started, no
#                       system config touched. Safe to run anytime.
#   --execute         : full cutover (§3.1 stack, §3.1b Caddy site block,
#                       §3.3/§3.4 verifies, §4 suite). Board-authorized only.
#   --seed            : with --execute, also seed an empty DB via
#                       scripts.seed_data (runbook §3.2 no-backup path; the
#                       data-loss window must be documented in the thread).
#
# Every run tees evidence to logs/failover-<UTC timestamp>.log.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

MODE="check"; SEED=0
for arg in "$@"; do
  case "$arg" in
    --check)  MODE="check" ;;
    --execute) MODE="execute" ;;
    --seed)   SEED=1 ;;
    *) echo "usage: $0 [--check|--execute] [--seed]"; exit 64 ;;
  esac
done

STANDBY_HOSTNAME="ip-208-84-102-245.my-advin.com"
PRIMARY_VPS="208.84.102.243"
COMPOSE_FILES=(-f docker-compose.yml -f docker-compose.failover.yml)
LOG_DIR="logs"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/failover-$(date -u +%Y%m%dT%H%M%SZ).log"

pass=0; fail=0
ok()   { echo "  [PASS] $*" | tee -a "$LOG"; pass=$((pass+1)); }
bad()  { echo "  [FAIL] $*" | tee -a "$LOG"; fail=$((fail+1)); }
gate() { # gate "<msg>" — hard abort when a check failed
  if [ "$fail" -gt 0 ]; then
    echo "ABORT: $fail pre-flight check(s) failed — $*" | tee -a "$LOG"
    echo "RESULT: PREFLIGHT_FAILED" | tee -a "$LOG"; exit 1
  fi
}

echo "=== Huible failover .243 -> .245 | mode=$MODE seed=$SEED ===" | tee -a "$LOG"
echo "host: $(hostname -f)  time: $(date -u +%FT%TZ)  repo: $REPO_DIR" | tee -a "$LOG"
echo

echo "## Pre-flight gates (runbook §2 + §1)" | tee -a "$LOG"

# G1 — host identity: cutover commands are local-only on the standby.
[ "$(hostname -f)" = "$STANDBY_HOSTNAME" ] && ok "host is the standby (.245)" \
  || bad "host is $(hostname -f), expected $STANDBY_HOSTNAME — run from .245 only"

# G2 — repo clean (uncommitted changes at cutover = unreproducible state).
[ -z "$(git status --porcelain)" ] && ok "repo clean" \
  || bad "repo dirty — commit or stash first: $(git status --porcelain | head -3 | tr '\n' ' ')"

# G3 — failover env staged, 0600, and symlinked as .env (compose env_file).
if [ -f .env.failover ] && [ "$(stat -c %a .env.failover)" = "600" ]; then
  ok ".env.failover present (0600)"
else
  bad ".env.failover missing or not 0600"
fi
[ "$(readlink -f .env 2>/dev/null)" = "$REPO_DIR/.env.failover" ] \
  && ok ".env -> .env.failover symlink in place" \
  || { [ "$MODE" = "execute" ] && ln -sf .env.failover .env && ok ".env symlink created (execute mode)" \
       || bad ".env is not symlinked to .env.failover (run: ln -sf .env.failover .env)"; }

# G4 — compose merge: default stack must be exactly app+postgres (caddy
# excluded via compose-caddy profile), app loopback :8000, PG loopback :5433,
# no host 80/443 binding (system Caddy owns those).
SERVICES="$(docker compose "${COMPOSE_FILES[@]}" --env-file .env.failover config --services 2>/dev/null | sort | tr '\n' ' ')"
[ "$SERVICES" = "app postgres " ] && ok "compose merge -> app + postgres only (got: ${SERVICES:-none})" \
  || bad "compose merge unexpected services: '${SERVICES}'"
PORTS="$(docker compose "${COMPOSE_FILES[@]}" --env-file .env.failover config 2>/dev/null | grep -E '^\s+(published|target):' -A0 | grep published | sort -u | tr '\n' ' ')"
CFG="$(docker compose "${COMPOSE_FILES[@]}" --env-file .env.failover config 2>/dev/null)"
echo "$CFG" | grep -q 'published: "8000"' && ok "app published port 8000 (loopback binding in config)" || bad "app port 8000 missing in merged config"
echo "$CFG" | grep -q 'published: "5433"' && ok "postgres published port 5433 (remap active, no system-PG collision)" || bad "PG 5433 remap missing in merged config"
echo "$CFG" | grep -qE 'published: "(80|443)"' && bad "host 80/443 binding present — compose caddy not excluded" || ok "no host 80/443 binding (system Caddy keeps ingress)"

# G5 — images cached: first bring-up must not depend on a registry pull or a
# gate-time build (app image pre-built 2026-08-14).
docker image inspect huible-app:latest >/dev/null 2>&1 && ok "app image huible-app:latest cached" \
  || bad "app image not built — run: docker compose -f docker-compose.yml -f docker-compose.failover.yml --env-file .env.failover build app"
docker image inspect pgvector/pgvector:pg17 >/dev/null 2>&1 && ok "pgvector/pgvector:pg17 cached" \
  || bad "pgvector image missing"

# G6 — standby system services the cutover relies on (never started by us):
systemctl is-active --quiet kestra.service && ok "kestra.service active" || bad "kestra.service not active"
curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://localhost:8080/ | grep -qE '200|307' \
  && ok "Kestra :8080 responds (200/307)" || bad "Kestra :8080 not responding"
if docker ps --format '{{.Names}}' | grep -q couchdb-livesync; then
  ok "couchdb-livesync container up"
else
  ok "couchdb-livesync absent — retired 2026-08-15 (HU-1681 S5); FNS is the vault sync stack"
fi
systemctl is-active --quiet caddy && ok "system caddy active" || bad "system caddy not active"

# G7 — Vault sync sanity. LiveSync/CouchDB retired 2026-08-15 (HU-1681 S5);
# FNS (fast-note-sync) is the vault stack — verify it instead of CouchDB.
if curl -s --max-time 5 http://localhost:9000/api/health | grep -q '"status":"healthy"'; then
  ok "FNS /api/health healthy (vault sync stack, post-LiveSync-retirement)"
else
  bad "FNS :9000 not healthy — vault sync stack suspect; do not cutover"
fi

# G8 — Caddy site block staged and validatable against a copy of the live
# Caddyfile (§3.1b; never edits the live file in check mode).
if [ -f deploy/caddy-standby/huible-site.caddy ] && [ -f /etc/caddy/Caddyfile ]; then
  TMPD=$(mktemp -d)
  cp deploy/caddy-standby/huible-site.caddy "$TMPD/huible-site.caddy"
  { cat /etc/caddy/Caddyfile; echo; echo 'import huible-site.caddy'; } > "$TMPD/Caddyfile"
  if HUIBLE_DOMAIN=localhost caddy validate --config "$TMPD/Caddyfile" >/dev/null 2>&1; then
    ok "Caddy site block validates against live-Caddyfile copy (HUIBLE_DOMAIN=localhost)"
  else
    bad "Caddy site block failed validation — inspect deploy/caddy-standby/huible-site.caddy"
  fi
  rm -rf "$TMPD"
else
  bad "site block or /etc/caddy/Caddyfile missing"
fi

# G9 — premise check: the primary .243 must STILL be down. If it returned,
# the cutover-vs-restore decision reverts to the CEO/board (HU-1643) — abort.
# (Output captured first: a grep -q in-pipeline would SIGPIPE writers and
# pipefail would misreport a match as failure.)
PROBE_OUT="$(bash scripts/verify_vps_recovery.sh 2>/dev/null || true)"
echo "$PROBE_OUT" >> "$LOG"
if echo "$PROBE_OUT" | grep -q 'VPS_NOT_READY'; then
  ok "primary .243 still dark — cutover premise holds"
else
  bad "primary .243 appears RECOVERED — premise changed; halt and route the cutover-vs-restore decision back through HU-1643/HU-1644 before executing"
fi

echo | tee -a "$LOG"
echo "Pre-flight: $pass passed, $fail failed" | tee -a "$LOG"

if [ "$MODE" = "check" ]; then
  [ "$fail" -eq 0 ] && echo "RESULT: PREFLIGHT_GREEN — ready for --execute on board GO" | tee -a "$LOG" \
                    || { echo "RESULT: PREFLIGHT_FAILED" | tee -a "$LOG"; exit 1; }
  exit 0
fi

# ------------------------- execute mode (§3) -------------------------
gate "cutover will not start with failed gates"

echo | tee -a "$LOG"; echo "## Cutover §3.1 — start app stack (app + postgres)" | tee -a "$LOG"
docker compose "${COMPOSE_FILES[@]}" --env-file .env.failover up -d 2>&1 | tee -a "$LOG"

# Health gate with bounded retries.
HEALTH=1
for i in $(seq 1 30); do
  BODY=$(curl -s --max-time 3 http://127.0.0.1:8000/api/v1/health || true)
  echo "$BODY" | grep -q '"status":"ok"' && { HEALTH=0; break; } || sleep 5
done
[ "$HEALTH" -eq 0 ] && ok "app health: $BODY" || bad "app health endpoint never returned ok"
gate "app stack unhealthy"

docker compose "${COMPOSE_FILES[@]}" --env-file .env.failover exec -T postgres pg_isready -U huible | tee -a "$LOG" \
  && ok "postgres pg_isready" || bad "postgres not ready"
docker compose "${COMPOSE_FILES[@]}" --env-file .env.failover exec -T app alembic upgrade head 2>&1 | tail -3 | tee -a "$LOG" \
  && ok "alembic upgrade head applied" || bad "alembic migration failed"

if [ "$SEED" -eq 1 ]; then
  echo "## §3.2 — seed empty DB (--seed; data-loss window documented in thread)" | tee -a "$LOG"
  docker compose "${COMPOSE_FILES[@]}" --env-file .env.failover exec -T app python -m scripts.seed_data \
    --url "postgresql://huible:${POSTGRES_PASSWORD:-huible}@postgres:5432/huible" 2>&1 | tail -5 | tee -a "$LOG" \
    && ok "seed_data applied" || bad "seed_data failed"
fi

echo "## §3.1b — install system-Caddy site block (validate gate, reload only)" | tee -a "$LOG"
cp deploy/caddy-standby/huible-site.caddy /etc/caddy/huible-site.caddy
grep -q 'import huible-site.caddy' /etc/caddy/Caddyfile || echo 'import huible-site.caddy' >> /etc/caddy/Caddyfile
HUIBLE_DOMAIN=localhost caddy validate --config /etc/caddy/Caddyfile >/dev/null 2>&1 \
  && { systemctl reload caddy; ok "site block installed, validated, caddy reloaded (zero-downtime)"; } \
  || bad "Caddyfile validation failed AFTER install — site block present but not reloaded; inspect /etc/caddy manually"
gate "ingress left in unknown state"

echo "## §3.3/§3.4 — FNS + Kestra verify (LiveSync retired 2026-08-15, HU-1681 S5)" | tee -a "$LOG"
curl -s --max-time 5 http://localhost:9000/api/health | grep -q '"status":"healthy"' \
  && ok "FNS /api/health healthy" || bad "FNS not healthy"
systemctl is-active --quiet kestra.service && ok "kestra active post-cutover" || bad "kestra inactive post-cutover"

echo "## §4 — post-cutover verification suite (standby overrides)" | tee -a "$LOG"
VPS_PUBLIC=208.84.102.245 VPS_TS_IP=100.101.235.117 KESTRA_TS_IP=100.101.235.117 \
TS_NODE_VPS=ip-208-84-102-245 TS_NODE_KESTRA=ip-208-84-102-245 \
  bash scripts/verify_vps_recovery.sh 2>&1 | tee -a "$LOG"
bash scripts/verify_prod_external.sh 2>&1 | tee -a "$LOG" || bad "verify_prod_external reported failures"
bash scripts/verify_prod_hardening.sh 2>&1 | tee -a "$LOG" || bad "verify_prod_hardening reported failures"

echo | tee -a "$LOG"
echo "Cutover complete: $pass passed, $fail failed — evidence: $LOG" | tee -a "$LOG"
[ "$fail" -eq 0 ] && echo "RESULT: CUTOVER_GREEN" | tee -a "$LOG" || { echo "RESULT: CUTOVER_DEGRADED — triage $LOG" | tee -a "$LOG"; exit 1; }
