#!/usr/bin/env bash
# Application Postgres backup — implements docs/09 §9.2f (HU-1672 AC #1).
#
# What: daily custom-format pg_dump of the compose app Postgres (pgvector/pg17)
# on the standby .245, written to /backups/pg/ with a sha256 sidecar, 30-day
# retention. pg_dump runs INSIDE the container so the client always matches the
# server major version (host-installed pg_dump may be older than pg17).
#
# Port map note (docker-compose.failover.yml): on this host the compose PG is
# published at 127.0.0.1:5433 (system PostgreSQL owns 5432). The dump path
# never touches the host port — it uses the container network directly.
#
# Schedule: installed by scripts/setup_pg_backup.sh as a systemd timer
# (deploy/systemd/huible-pg-backup.{service,timer}); also safe to run ad hoc.
#
# Restore (docs/09 §9.3, verified by scripts/verify_backup_restore.sh):
#   dump=/backups/pg/huible-<stamp>.dump
#   sha256sum -c "$dump.sha256"                          # must pass first
#   docker compose exec -T postgres dropdb -U huible huible   # DESTRUCTIVE
#   cat "$dump" | docker compose exec -T postgres pg_restore -U huible -d huible
#
# Secret hygiene: dumps contain user data (personas, chat history) — never
# commit them or upload outside the board-designated backup destination.
set -euo pipefail

cd "$(dirname "$0")/.."

DEST="${HUIBLE_PG_BACKUP_DIR:-/backups/pg}"
RETENTION_DAYS="${HUIBLE_PG_BACKUP_RETENTION:-30}"
PG_USER="${POSTGRES_USER:-huible}"
PG_DB="${POSTGRES_DB:-huible}"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
dump="$DEST/huible-$stamp.dump"

# Never leave a partial/empty dump behind on failure (it would poison the
# restore verifier's newest-dump selection).
cleanup() { [ -s "$dump" ] || rm -f "$dump" "$dump.sha256"; }
trap cleanup EXIT

mkdir -p "$DEST"; chmod 700 "$DEST"

# Read POSTGRES_USER/DB from .env without sourcing it: the dotenv carries
# non-shell values (e.g. GENERATOR_EXTRA_JSON JSON added by the zai flip),
# and `source` broke the nightly timer with exit 127 on 2026-08-20.
# Password not needed: pg_dump runs inside the container over its local socket.
if [ -f .env ]; then
  dotenv_value() {
    grep -E "^$1=" .env | tail -n1 | cut -d= -f2- \
      | sed -e 's/[[:space:]]#.*$//' -e 's/[[:space:]]*$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'/\1/" || :
  }
  v="$(dotenv_value POSTGRES_USER)"; PG_USER="${v:-$PG_USER}"
  v="$(dotenv_value POSTGRES_DB)";   PG_DB="${v:-$PG_DB}"
fi

docker compose exec -T postgres pg_dump -U "$PG_USER" -d "$PG_DB" -Fc > "$dump"
chmod 600 "$dump"

[ -s "$dump" ] || { echo "FAIL: dump $dump is empty" >&2; exit 1; }

sha256sum "$dump" > "$dump.sha256"
chmod 600 "$dump.sha256"

echo "$(date -u +%FT%TZ) pg backup ok: $dump ($(du -h "$dump" | cut -f1))"

# Retention: drop dump+sidecar pairs older than RETENTION_DAYS.
find "$DEST" -type f -name 'huible-*.dump' -mtime +"$RETENTION_DAYS" -delete
find "$DEST" -type f -name 'huible-*.dump.sha256' -mtime +"$RETENTION_DAYS" -delete
