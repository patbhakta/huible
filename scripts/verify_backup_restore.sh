#!/usr/bin/env bash
# Restore-path proof for the pg backups — HU-1672 AC #1 ("backups configured
# + tested"), docs/09 §9.2f/§9.3.
#
# Takes the newest (or --dump PATH) backup in /backups/pg, verifies its sha256,
# restores it into a scratch database (huible_restore_test) INSIDE the compose
# postgres container, compares public-schema table + row counts against the
# live DB, then drops the scratch DB. Live data is never touched.
#
# Usage:
#   bash scripts/verify_backup_restore.sh                # newest dump
#   bash scripts/verify_backup_restore.sh --dump /backups/pg/huible-X.dump
set -euo pipefail

cd "$(dirname "$0")/.."

DEST="${HUIBLE_PG_BACKUP_DIR:-/backups/pg}"
PG_USER="${POSTGRES_USER:-huible}"
PG_DB="${POSTGRES_DB:-huible}"
SCRATCH="huible_restore_test"

dump=""
if [ "${1:-}" = "--dump" ] && [ -n "${2:-}" ]; then
  dump="$2"
else
  dump="$(ls -1t "$DEST"/huible-*.dump 2>/dev/null | head -1 || true)"
fi
[ -n "$dump" ] && [ -f "$dump" ] || { echo "FAIL: no dump found in $DEST (run scripts/backup_pg_dump.sh first)" >&2; exit 1; }

if [ -f .env ]; then
  # shellcheck disable=SC1091
  set -a; . ./.env; set +a
  PG_USER="${POSTGRES_USER:-$PG_USER}"
  PG_DB="${POSTGRES_DB:-$PG_DB}"
fi

ok()   { echo "ok:   $*"; }
bad()  { echo "FAIL: $*" >&2; exit 1; }

# 1) Integrity: sha256 must pass before the dump is trusted.
[ -f "$dump.sha256" ] || bad "missing sidecar $dump.sha256"
(cd "$(dirname "$dump")" && sha256sum -c "$(basename "$dump").sha256" >/dev/null) \
  && ok "sha256 verified: $(basename "$dump")" || bad "sha256 mismatch for $dump"

# 2) Clean slate scratch DB (drops leftover from an aborted prior run).
docker compose exec -T postgres psql -U "$PG_USER" -d postgres \
  -c "DROP DATABASE IF EXISTS $SCRATCH;" >/dev/null
docker compose exec -T postgres createdb -U "$PG_USER" "$SCRATCH"

# 3) Restore.
cat "$dump" | docker compose exec -T postgres pg_restore -U "$PG_USER" -d "$SCRATCH" --no-owner \
  || bad "pg_restore into $SCRATCH failed"

# 4) Compare live vs restored (public schema).
count_sql() { # db  metric
  docker compose exec -T postgres psql -U "$PG_USER" -d "$1" -Atc "$2"
}
t_live=$(count_sql "$PG_DB"  "SELECT count(*) FROM pg_tables WHERE schemaname='public';")
t_rest=$(count_sql "$SCRATCH" "SELECT count(*) FROM pg_tables WHERE schemaname='public';")
r_live=$(count_sql "$PG_DB"  "SELECT coalesce(sum(n_live_tup),0) FROM pg_stat_user_tables;")
r_rest=$(count_sql "$SCRATCH" "SELECT coalesce(sum(n_live_tup),0) FROM pg_stat_user_tables;")
# n_live_tup is an estimate on the restored side until ANALYZE; run it.
docker compose exec -T postgres psql -U "$PG_USER" -d "$SCRATCH" -c "ANALYZE;" >/dev/null
r_rest=$(count_sql "$SCRATCH" "SELECT coalesce(sum(n_live_tup),0) FROM pg_stat_user_tables;")

[ "$t_live" -gt 0 ] || bad "live DB has no public tables — wrong DB? (expected data)"
[ "$t_live" = "$t_rest" ] || bad "table count mismatch: live=$t_live restored=$t_rest"
[ "$r_live" = "$r_rest" ] || bad "row count mismatch: live=$r_live restored=$r_rest (estimates; re-run if close)"
ok "round-trip verified: $t_rest tables, $r_rest rows match live"

# 5) Cleanup.
docker compose exec -T postgres psql -U "$PG_USER" -d postgres \
  -c "DROP DATABASE $SCRATCH;" >/dev/null
ok "scratch DB $SCRATCH dropped — live DB untouched"

echo "RESTORE_PROOF_PASS $dump"
