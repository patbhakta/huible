#!/usr/bin/env python3
"""HU-2774 battery pre-task: reset conversation-writeback memories.

Deletes source_type='conversation' memories for BOTH test personas so each
battery run starts from identical state: vault evidence only. Without this,
run N's conversation turns contaminate run N+1's retrieval (r9-verify
finding 2026-09-10: Monica quoted her own PREVIOUS RUN's answer to the same
probe — the durable memory lane working exactly as designed, which is
precisely why evidence runs must reset it).

r10 finding 2026-09-12: the conversation HISTORY table is durable too
(HU-1440), and the §3 dosage gate derives turn_count from it
(RISK_DOSAGE_CAP_TURNS=40 → pause_session). A killed/repeated battery round
left 34 stale conversation_turns rows under the same run id; the next round
started at turn_count 18 and the cap fired mid-conversation (r10-friends-1
t23 platform-text failure). This script therefore ALSO purges
conversation_turns + crisis_sessions for the battery namespace
('hu2774-%') — the two tables that feed enforcement signals (turn count,
crisis history). Consent rows are re-recorded per run and left alone.

The working memories inside a run are untouched: within a run, session 2's
cross-session recall of session 1 (the CEO bar) still rides the persisted
conversation memories written during THIS run.

Reads DATABASE_URL from the engine .env; talk to the same Postgres the
engine uses. Vault memories (source_type='vault_ingest') are NEVER touched.
"""
import argparse
import os
import sys

PERSONA_IDS = (
    "fdc3a44b-4c0f-565d-b671-4ed0e3bc7894",  # Chandler
    "3ef60bec-79d2-5e31-8d9e-e856bb1ebfea",  # Monica
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--env', default='/root/repos/huible/.env')
    ap.add_argument('--execute', action='store_true',
                    help='actually delete; without this flag, dry-run count only')
    args = ap.parse_args()

    dsn = ''
    env: dict[str, str] = {}
    for line in open(args.env):
        line = line.strip()
        if '=' not in line or line.startswith('#'):
            continue
        k, _, v = line.partition('=')
        # strip inline comments ("VAR=value  # note") and surrounding quotes
        v = v.split(' #', 1)[0].strip().strip('"\'')
        env[k] = v
        if k == 'DATABASE_URL':
            dsn = v
    if not dsn:
        print('DATABASE_URL not found in .env', file=sys.stderr)
        return 2
    # .env-style ${VAR} interpolation (docker compose resolves these at
    # container start; the host-side battery pre-task must resolve them too).
    for k, v in env.items():
        dsn = dsn.replace('${' + k + '}', v)
    dsn = (dsn.replace('postgresql+asyncpg://', 'postgresql://')
              .replace('postgresql+psycopg://', 'postgresql://'))
    # Host-side access: the compose-internal service name/port is not
    # resolvable outside the docker network; use the host port mapping.
    dsn = dsn.replace('@postgres:', '@127.0.0.1:')
    dsn = dsn.replace(':5432/', ':5433/')

    placeholders = ', '.join(f"'{pid}'" for pid in PERSONA_IDS)
    where = (f"source_type = 'conversation' "
             f"AND persona_id IN ({placeholders})")
    #: Enforcement-signal tables keyed by conversation id (see docstring).
    #: Battery run ids + s1/s2 conversation ids all carry the hu2774- prefix.
    SIGNAL_TABLES = ('conversation_turns', 'crisis_sessions')
    try:
        import psycopg  # psycopg 3
    except ImportError:
        print('psycopg not installed on host', file=sys.stderr)
        return 2
    conn = psycopg.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(f'SELECT count(*) FROM memories WHERE {where}')
            n = cur.fetchone()[0]
            print(f"conversation-writeback memories for the two personas: {n}")
            if args.execute and n:
                cur.execute(f'DELETE FROM memories WHERE {where}')
                print(f"deleted: {cur.rowcount}")
            elif not args.execute:
                print('dry run — pass --execute to delete')
            for table in SIGNAL_TABLES:
                cur.execute(f"SELECT count(*) FROM {table} "
                            f"WHERE conversation_id LIKE 'hu2774-%'")
                n = cur.fetchone()[0]
                print(f"{table} rows for the battery namespace: {n}")
                if args.execute and n:
                    cur.execute(f"DELETE FROM {table} "
                                f"WHERE conversation_id LIKE 'hu2774-%'")
                    print(f"deleted: {cur.rowcount}")
        conn.commit()
    finally:
        conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
