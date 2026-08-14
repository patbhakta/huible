# [09] Deployment & Operations Guide

Complete runbook for running Huible in production. Covers containerization, networking, database provisioning, environment configuration, monitoring, and disaster recovery.

---

## 1. Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| Docker | 24+ | Engine + Compose V2 (`docker compose`, not `docker-compose`) |
| Python | 3.12 | Only needed for local dev, not in containers |
| PostgreSQL client | 16+ | For manual DB operations and backups |
| Caddy | 2.8+ | Managed via Docker, no host install needed |

**Hardware minimum (Phase 1 — memory engine only):**

- 2 CPU cores, 4 GB RAM
- 20 GB disk (database + embeddings)
- No GPU required (Phase 1)

**Phase 3+ (persona generation):**

- GPU: 1x NVIDIA A10G or equivalent (24 GB VRAM for 7B–24B models)
- 8+ CPU cores, 16 GB RAM recommended

---

## 2. Quick Start

```bash
# 1. Clone the repository
git clone <repo-url> huible-engine
cd huible-engine

# 2. Copy and edit environment variables
cp .env.example .env
# Edit .env — at minimum set POSTGRES_PASSWORD and HUIBLE_DOMAIN

# 3. Start everything
docker compose up -d

# 4. Verify
curl http://localhost:8000/api/v1/health
# Expected: {"data":{"status":"ok","version":"0.1.0"}}

# 5. (Optional) Seed test data
docker compose exec app python -m scripts.seed_data \
    --url "postgresql://huible:${POSTGRES_PASSWORD}@postgres:5432/huible"
```

That's it. Docker Compose starts three services:

| Service | Container | Port | Purpose |
|---------|-----------|------|---------|
| `app` | huible-app | 8000 | Python API server |
| `postgres` | huible-postgres | 5432 | PostgreSQL 17 + pgvector |
| `caddy` | huible-caddy | 80, 443 | Reverse proxy with automatic HTTPS |

---

## 3. Configuration Reference

All configuration is via environment variables (see `.env.example`).

### 3.1 Application Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HUIBLE_HOST` | `0.0.0.0` | Bind address inside container |
| `HUIBLE_PORT` | `8000` | Bind port |
| `HUIBLE_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `HUIBLE_ENV` | `production` | `development`, `staging`, `production` |
| `HUIBLE_DOMAIN` | `localhost` | Domain for Caddy TLS certificate |

### 3.2 Database Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_HOST` | `postgres` | Hostname (service name in Compose) |
| `POSTGRES_PORT` | `5432` | PostgreSQL port |
| `POSTGRES_USER` | `huible` | Database user |
| `POSTGRES_PASSWORD` | — | **Required.** Use a strong secret. |
| `POSTGRES_DB` | `huible` | Database name |
| `DATABASE_URL` | constructed | Full async URL. Override if using external DB. |

### 3.3 Embedding Provider (Phase 2+)

| Variable | Default | Description |
|----------|---------|-------------|
| `EMBEDDING_PROVIDER` | `fake` | `openai` or `fake` (Phase 1 testing) |
| `OPENAI_API_KEY` | — | Required when `EMBEDDING_PROVIDER=openai` |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | OpenAI embedding model |

### 3.4 Advisory Models (Phase 2+)

| Variable | Default | Description |
|----------|---------|-------------|
| `CLAUDE_API_KEY` | — | Anthropic API key for gate adjudication |
| `OPENAI_ADVISORY_KEY` | — | OpenAI key for advisory layer extraction |

### 3.5 Authentication

| Variable | Default | Description |
|----------|---------|-------------|
| `API_KEYS` | — | Comma-separated persona-scoped API keys |

### 3.6 Tailscale (Optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `TAILSCALE_ENABLED` | `false` | Enable Tailscale funnel for private deployment |
| `TAILSCALE_FUNNEL_DOMAIN` | — | Tailscale funnel domain name |

### 3.7 Human-Handoff & On-Call Paging (§7.4.1, Stage 0.4)

The crisis-escalation queue routes every G1-flagged turn into a staffed-responder
queue with a monitored SLA and a fail-safe that degrades to the non-persona safe
response when no human is available (never drops, never the persona voice). The
responder count is the operational lever; paging is additive on top of an
`ENQUEUED` ticket — it never bypasses the degrade gate.

| Variable | Default | Description |
|----------|---------|-------------|
| `HANDOFF_SLA_TARGET_SECONDS` | `300` | Target for a responder to acknowledge (canary: `900` = 15 min) |
| `HANDOFF_AVAILABLE_RESPONDERS` | `0` | Simultaneous on-call responders. `0` = fail-safe degrade (pre-roster) |
| `HANDOFF_RESPONDER_POOL` | — | Comma-separated staffed responder ids (the on-call roster) |
| `HANDOFF_COVERAGE_MODE` | `always` | `always` (24/7) or `hours` (bounded window) |
| `HANDOFF_COVERAGE_TZ` | `UTC` | IANA timezone for the coverage window |
| `HANDOFF_COVERAGE_OPEN_HOUR` | `0` | Hour-of-day coverage opens, inclusive (`0`-`23`) |
| `HANDOFF_COVERAGE_CLOSE_HOUR` | `24` | Hour-of-day coverage ends, exclusive (`1`-`24`) |
| `HANDOFF_PAGER_PROVIDER` | `log` | `log` (key-free CRITICAL log line) or `webhook` |
| `HANDOFF_PAGER_WEBHOOK_URL` | — | Slack incoming webhook / PagerDuty Events API v2 URL (empty → log fallback) |

**Stage 1 canary on-call config** (founder-staffed 4×12h roster — PM / Tech Lead /
Clinical Advisor / CEO, named in the HU-1447 on-call-roster doc):

```ini
HANDOFF_AVAILABLE_RESPONDERS=4
HANDOFF_RESPONDER_POOL=huible-pm,huible-tech-lead,clinical-advisor,ceo
HANDOFF_SLA_TARGET_SECONDS=900
HANDOFF_COVERAGE_MODE=always
HANDOFF_PAGER_PROVIDER=log   # flip to webhook once the pager URL is provisioned
```

Setting `HANDOFF_AVAILABLE_RESPONDERS>0` flips the `huible_alert_oncall_configured`
gauge to `1` (the §3 Sev-1 alerts are now wired to a real on-call). Defaults stay
at the fail-safe (`0` responders) so an unconfigured deploy never falsely pages or
promises a responder.

---

## 4. Containerization

### 4.1 Dockerfile

Multi-stage build:

1. **Build stage** (`python:3.12-slim`): Installs dependencies via `pip`, compiles Python source
2. **Runtime stage** (`python:3.12-alpine`): Minimal image with only runtime dependencies, runs as non-root `huible` user

```bash
# Build manually
docker build -t huible:latest .

# With build cache
docker compose build
```

### 4.2 Docker Compose Services

```
┌──────────┐       ┌──────────┐
│  Caddy   │──────>│   App    │
│  :80/443 │       │  :8000   │
└──────────┘       └────┬─────┘
                        │
                   ┌────┴──────┐
                   │ Postgres  │
                   │ + pgvector│
                   │  :5432    │
                   └───────────┘
```

**Volumes:**

| Volume | Host Path | Purpose |
|--------|-----------|---------|
| `pgdata` | Docker-managed | PostgreSQL data persistence |
| `caddy_data` | Docker-managed | TLS certificates, Caddy state |
| `caddy_config` | Docker-managed | Caddy configuration cache |

**Health checks:**

- **postgres**: `pg_isready` every 10s, 30s start period
- **app**: `GET /api/v1/health` every 30s, 10s start period

### 4.3 Common Operations

```bash
# View logs
docker compose logs -f app
docker compose logs -f postgres

# Restart a single service
docker compose restart app

# Rebuild after code changes
docker compose up -d --build app

# Stop everything
docker compose down

# Stop and remove volumes (destroys database!)
docker compose down -v
```

---

## 5. Reverse Proxy & Networking

### 5.1 Caddy

Caddy provides automatic HTTPS via Let's Encrypt. Configuration lives in `Caddyfile`.

**Production usage:**

1. Set `HUIBLE_DOMAIN` in `.env` to your public domain (e.g., `huible.example.com`)
2. Ensure DNS A record points to your server's IP
3. Caddy automatically provisions and renews TLS certificates

**Local development:**

Caddy serves HTTP on port 80 with `HUIBLE_DOMAIN=localhost`. No HTTPS.

### 5.2 Route Definitions

| Path | Upstream | Description |
|------|----------|-------------|
| `/api/v1/health` | `app:8000` | Health / liveness endpoint |
| `/api/v1/*` | `app:8000` | REST API (memories, retrieval, quarantine) |
| `/static/*` | `app:8000` | Static assets (Phase 3+ admin UI) |

### 5.3 Tailscale Integration (Optional)

For private deployment behind a NAT or firewall:

1. Install Tailscale on the host
2. Enable Tailscale Funnel: `tailscale funnel 443`
3. Set `TAILSCALE_ENABLED=true` and `TAILSCALE_FUNNEL_DOMAIN` in `.env`
4. Caddy terminates TLS; Tailscale handles ingress

---

## 6. Database Provisioning

### 6.1 PostgreSQL + pgvector

The `postgres` service uses `pgvector/pgvector:pg17`, which bundles PostgreSQL 17 with the pgvector extension pre-installed.

**Init scripts** run on first container start in lexicographic order:

| Script | Purpose |
|--------|---------|
| `docker/init-db/01-enable-pgvector.sql` | `CREATE EXTENSION IF NOT EXISTS vector` |
| `docker/init-db/02-schema.sql` | Full schema: `personas`, `memories`, `memory_edges`, `quarantine` with HNSW indexes |

Init scripts only run when the database is empty (no `public` tables). To re-initialize, delete the `pgdata` volume:

```bash
docker compose down -v
docker compose up -d
```

### 6.2 Schema

Four tables defined in `migrations/schema.sql` (canonical source of truth):

| Table | Rows (typical) | Purpose |
|-------|----------------|---------|
| `personas` | 1–N | Person identity, voice instructions |
| `memories` | 1K–1M+ | Memory nodes with multi-vector embeddings |
| `memory_edges` | 3K–10M+ | Graph edges connecting memories |
| `quarantine` | 0–100 | Failed-gate candidates pending adjudication |

**HNSW indexes** (3 total) for vector similarity search:

- `idx_memories_content_emb` — content embeddings (1536d)
- `idx_memories_sensory_emb` — sensory embeddings (1536d)
- `idx_memories_affect_emb` — affect embeddings (512d)

### 6.3 Connection Pooling

Huible uses `asyncpg` for async database access. Pool configuration:

| Setting | Default | Notes |
|---------|---------|-------|
| `min_size` | 2 | Minimum idle connections |
| `max_size` | 10 | Maximum pool size |
| `max_queries` | 50000 | Reconnect after N queries (prevent leaks) |
| `max_inactive_connection_lifetime` | 300s | Reap idle connections |

For high-traffic deployments, increase `max_size` to 20–50.

### 6.4 Alembic Migrations

After init scripts create the baseline schema, use Alembic for incremental migrations:

```bash
# Run inside the app container
docker compose exec app alembic upgrade head

# Generate a new migration from schema.sql changes
docker compose exec app alembic revision --autogenerate -m "description"

# Rollback one migration
docker compose exec app alembic downgrade -1
```

---

## 7. Monitoring & Observability

### 7.1 Health Endpoint

`GET /api/v1/health` returns:

```json
{
  "data": {
    "status": "ok",
    "version": "0.1.0",
    "db": "connected",
    "uptime_seconds": 86400
  }
}
```

Degraded state (database unreachable):

```json
{
  "data": {
    "status": "degraded",
    "version": "0.1.0",
    "db": "disconnected",
    "uptime_seconds": 86400
  }
}
```

### 7.2 Structured Logging

Huible outputs structured JSON logs to stdout. Each log line:

```json
{
  "level": "INFO",
  "ts": "2025-12-01T18:30:00.000Z",
  "msg": "Memory ingested",
  "persona_id": "a0000000-0000-0000-0000-000000000001",
  "memory_id": "550e8400-e29b-41d4-a716-446655440000",
  "tier": "accrued",
  "gates_passed": ["safety", "dedup", "novelty", "immutability", "pertinence"],
  "duration_ms": 142
}
```

**Log levels:**

| Level | When |
|-------|------|
| `DEBUG` | Gate details, SQL queries, embedding dimensions |
| `INFO` | Request received, memory ingested, retrieval complete |
| `WARNING` | Gate ambiguity, quarantine, degraded health |
| `ERROR` | Database failure, unhandled exception, safety violation |

### 7.3 Recommended Metrics

The app exposes a Prometheus scrape endpoint at `GET /metrics` (unauthenticated
by design — contains only monotonic counters, a latency histogram, and SLO
gauges; no PHI). Stage 0.3 (HU-1446) shipped the guardrail counters; Stage 0.8
(HU-1463) added the §3 SLO *gauges* so the launch-plan §3.1/§3.2 SLO table and
§4.1 rollback triggers are observable from a scrape alone.

**Scrape config:**

```yaml
- job_name: huible
  metrics_path: /metrics
  static_configs:
    - targets: ['huible:8000']
```

**Guardrail counters (Stage 0.3, HU-1446):**

| Metric | Type | Description |
|--------|------|-------------|
| `huible_chat_turns_total` | counter | Persona-chat turns by outcome |
| `huible_chat_turn_latency_seconds` | histogram | Persona-chat turn wall-clock latency (handler entry → response) |
| `huible_chat_errors_total` | counter | Persona-chat turns that raised (4xx/5xx) by status class |
| `huible_crisis_fires_total` | counter | G1 crisis signals detected pre-generation |
| `huible_handoff_outcomes_total` | counter | §7.4.1 handoff escalations by queue outcome (enqueued/degraded/answered/abandoned) |
| `huible_consent_required_total` | counter | G6 first-use reality-framing consents required |
| `huible_ungrounded_claims_total` | counter | §7.4.2 persona claims detected as un-grounded |
| `huible_alignment_dispositions_total` | counter | §7.4.2 alignment-filter dispositions (suppressed/passed/refrained) |
| `huible_risk_enforcement_actions_total` | counter | §7.4.4 G8 binding actions by action |
| `huible_risk_flag_fires_total` | counter | §7.4.4 risk flags present on an enforced turn, by flag |
| `huible_real_user_refused_total` | counter | Stage 0.1 ramp-gate refusals |
| `huible_real_user_traffic_disabled_total` | counter | Stage 0.7 hard kill-switch refusals (503, primary rollback path) |
| `huible_paging_failures_total` | counter | §3 Sev-1 page-send failures by trigger |
| `huible_alert_oncall_configured` | gauge | `1` once the §3 Sev-1 alerts are wired to the on-call roster |

**SLO gauges (Stage 0.8, HU-1463) — launch-plan §3 SLO table + §4.1 rollback triggers:**

These gauges are mirrored from the `/api/v1/handoff/audit` telemetry and the
`/health` probe on every scrape, so the Prometheus view cannot drift from the
JSON dashboard. The alert rules in [`examples/prometheus-alerts.yml`](../examples/prometheus-alerts.yml)
page on these gauges.

| Metric | Type | Description | SLO / trigger |
|--------|------|-------------|---------------|
| `huible_handoff_degrade_rate` | gauge | §3.1 degrade rate (degraded / total) | Healthy = 0.0; > 0 halts ramp (§4.1) |
| `huible_handoff_pending_breached` | gauge | Open tickets past SLA right now | Healthy = 0; any > 0 halts ramp (§4.1) |
| `huible_handoff_pending_breach_rate` | gauge | pending_breached / pending | Healthy = 0.0 |
| `huible_handoff_answered_within_sla_rate` | gauge | 1 − answered_breach_rate | ≥ 0.9 (S1), ≥ 0.95 (S2+) |
| `huible_handoff_tickets_total` | gauge | Total tickets in audit log | context for the rates |
| `huible_handoff_pending` | gauge | Open (ENQUEUED) tickets | queue-depth signal |
| `huible_health_status` | gauge | `/health` status: 1 = ok, 0 = degraded | degraded halts ramp (§4.1) |

**Historical / forward-looking metrics** (not yet wired; track via your observability platform when implemented):

| Metric | Type | Description |
|--------|------|-------------|
| `huible_memories_ingested_total` | counter | Memories passing the five-gate firewall |
| `huible_memories_quarantined_total` | counter | Memories sent to quarantine (by gate) |
| `huible_memories_rejected_total` | counter | Memories rejected (by gate) |
| `huible_retrieval_duration_seconds` | histogram | Spreading activation retrieval time |
| `huible_active_memories_total` | gauge | Active memory count per persona |
| `huible_vector_index_size_bytes` | gauge | HNSW index size (per embedding type) |
| `huible_db_pool_active_connections` | gauge | Database connection pool usage |

### 7.3.1 Alert rules (Stage 0.8, HU-1463)

The launch-plan §4.1 rollback triggers are wired to Prometheus alerting in
[`examples/prometheus-alerts.yml`](../examples/prometheus-alerts.yml). Load it
in your Prometheus instance:

```yaml
rule_files:
  - /etc/prometheus/huible-alerts.yml
```

The file covers every §4.1 halt-the-ramp trigger:

- **`HuibleHandoffDegradeRate`** — degrade rate > 0 (fail-safe fired).
- **`HuibleHandoffPendingBreached`** — an open ticket is past SLA.
- **`HuibleHandoffAnsweredSLABurn`** — answered-within-SLA below 0.9.
- **`HuibleAlignmentLeak`** — §7.4.2 un-grounded claim reaching a user.
- **`HuibleHealthDegraded`** — `/health` reports degraded.
- **`HuibleChatLatencyBurn`** — chat p95 latency > 20s sustained 10 min.
- **`HuibleChatErrorBudgetBurn`** — 5xx error rate > 5% sustained 10 min.
- **`HuibleRealUserTrafficDisabled`** — kill-switch engaged (informational during drills).

`severity: page` alerts map to the §7.5 paging path (the on-call roster wired
in Stage 0.4); `severity: ticket` alerts are investigate-before-next-ramp-advance.
A firing `page` alert during a ramp stage means: run the §4.2 rollback
procedure (`PERSONA_CHAT_REAL_USER_TRAFFIC=off`) before advancing.


### 7.4 Log Aggregation

For production, forward container logs to your log aggregator:

```bash
# Docker native (stdout → journald)
docker compose logs -f

# Or pipe to external collector
docker compose logs -f app | fluent-bit
```

**Recommended stack:** Grafana Loki + Promtail (lightweight, pairs well with Prometheus).

### 7.5 On-Call Paging & Sev-1 Alerts (§7.4.1, Stage 0.4)

The `GET /metrics` endpoint exposes the guardrail counters shipped in Stage 0.3
(chat turns, G1 crisis fires, handoff outcomes, risk enforcement, kill-switch
refusals). Stage 0.4 wires the **alert→on-call paging link** on top of that
substrate.

**Gauge — alert wiring target:**

| Metric | Type | Description |
|--------|------|-------------|
| `huible_alert_oncall_configured` | gauge | `1` once the §3 Sev-1 alerts page the 0.4 on-call roster; `0` until `HANDOFF_AVAILABLE_RESPONDERS>0` |

**Two Sev-1 page triggers (both page the 0.4 on-call):**

1. **Crisis enqueue (primary).** Every `ENQUEUED` crisis ticket (G1
   `escalate_to_human` and risk-driven `escalate_risk_to_human`) pages
   immediately at `severity=crisis` — never throttled behind the >10%/1h
   aggregate backstop. A grieving user waiting on a crisis turn cannot be
   rate-limited.
2. **Ack-SLA breach (Sev-1 escalation).** An `ENQUEUED` ticket past its
   `HANDOFF_SLA_TARGET_SECONDS` without an acknowledgement is re-paged at
   `severity=sev-1` on every staffed-responder queue read (`GET
   /api/v1/handoff/tickets`). The canary ack SLA is **900s (15 min)**.

**Paging transports** (`HANDOFF_PAGER_PROVIDER`):

- `log` (key-free default) — emits a structured `handoff.page` **CRITICAL** log
  line. An operator wires a log scrape rule (match `level=CRITICAL` on the
  `handoff.page` message prefix) to turn this into a real alert. Distinct from
  the `handoff.enqueue` INFO audit line.
- `webhook` — POSTs a JSON payload to `HANDOFF_PAGER_WEBHOOK_URL` (Slack
  incoming webhook / PagerDuty Events API v2 style). Falls back to the log line
  when the URL is empty or the POST fails, so a misconfigured pager degrades to
  the honest log line rather than dropping the page silently.

**Paging drill (pre-real-user gate).** Before real grieving-user traffic flows,
record a successful end-to-end drill: a `handoff.page` CRITICAL line captured on
a crisis turn *and* a roster responder `POST …/handoff/tickets/{id}/resolve` on
the ticket. A `DEGRADED` ticket is never paged (no responder was paged by the
queue, so the on-call must not be told one was).

---

## 8. Production Hardening Checklist

Run through this checklist before going live.

### Network

- [ ] `POSTGRES_PASSWORD` is a strong, unique secret (not the `.env.example` default)
- [ ] PostgreSQL port 5432 is NOT exposed to the public internet (remove `ports:` mapping or bind to `127.0.0.1:5432`)
- [ ] `HUIBLE_DOMAIN` is set to a real domain with DNS A record
- [ ] Caddy TLS is active (verify with `curl https://<domain>/api/v1/health`)

### Secrets

- [ ] `.env` is not committed to version control (`.gitignore` includes `.env`)
- [ ] API keys for embedding/advisory providers are set via environment, not hardcoded
- [ ] `API_KEYS` is set for persona-scoped authentication

### Database

- [ ] `pgdata` volume is backed by a persistent disk
- [ ] Backup cron job is configured (see Section 9)
- [ ] `pgvector` extension is enabled: `SELECT * FROM pg_extension WHERE extname = 'vector'`

### Application

- [ ] `HUIBLE_ENV=production`
- [ ] `HUIBLE_LOG_LEVEL=INFO` (not `DEBUG`)
- [ ] Container restart policy is `unless-stopped`
- [ ] Health checks pass on all three services

### Infrastructure

- [ ] Host firewall allows only 80, 443 (and 22 for SSH)
- [ ] Automatic security updates are enabled on the host
- [ ] Disk monitoring alerts are set (< 20% free triggers alert)

---

## 9. Backup & Restore

### 9.1 Strategy

| Component | Method | Frequency | Retention |
|-----------|--------|-----------|-----------|
| PostgreSQL | `pg_dump` | Daily | 30 days |
| PostgreSQL | WAL archiving | Continuous | 7 days |
| Config files | Git | On change | Infinite |
| Caddy data | Backup volume | Weekly | 4 weeks |
| Kestra config (rotated env + server config) | `scripts/backup_kestra_config.sh` — versioned 0600 copies + sha256 manifest | Daily (cron 03:30 UTC) | 30 days |

### 9.2 Backup Procedure

**Full database backup:**

```bash
# Create backup directory
mkdir -p /backups/huible

# Dump the database
docker compose exec postgres pg_dump \
    -U huible \
    --format=custom \
    --file=/tmp/huible_backup.dump \
    huible

# Copy dump out of the container
docker compose cp postgres:/tmp/huible_backup.dump \
    /backups/huible/huible_$(date +%Y%m%d_%H%M%S).dump

# Verify
ls -lh /backups/huible/

# Clean up inside container
docker compose exec postgres rm /tmp/huible_backup.dump
```

**Automated daily backup (cron):**

```bash
# Add to crontab (crontab -e)
0 3 * * * cd /opt/huible && docker compose exec -T postgres pg_dump -U huible --format=custom huible > /backups/huible/huible_$(date +\%Y\%m\%d).dump
```

**Backup rotation (keep last 30 days):**

```bash
# Add to crontab
0 4 * * * find /backups/huible -name "huible_*.dump" -mtime +30 -delete
```

**Kestra config backup (secret-safe, §9.2e):**

`/opt/kestra/kestra.env` holds the **only** copy of the rotated CouchDB admin
credential (generated during the HU-1500 rotation) — losing it means losing
admin access to the live vault store. `scripts/backup_kestra_config.sh`
snapshots it together with `/root/.kestra/config.yml` into
`/backups/kestra-config/<UTC-stamp>/` (mode 0600, sha256-sealed, 30-day
retention, first snapshot 2026-08-14). Daily cron installed:

```bash
30 3 * * * /root/repos/huible/scripts/backup_kestra_config.sh >> /var/log/kestra-config-backup.log 2>&1
```

**Never** commit or upload these copies — plaintext credential material in git
is the HU-1500 leak class. Off-host redundancy stays **off** until the board
designates a secret-safe destination (pending approval `5e713a10`); the script
then takes `KESTRA_BACKUP_REMOTE=<rsync target>` to sync each snapshot out.

**Kestra config restore:**

```bash
dir=/backups/kestra-config/<stamp>
(cd "$dir" && sha256sum -c SHA256SUMS)   # must pass before trusting the copy
install -m 600 "$dir/kestra.env" /opt/kestra/kestra.env
install -m 644 "$dir/config.yml" /root/.kestra/config.yml
systemctl restart kestra
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8080/   # expect 200/307
```

### 9.3 Restore Procedure

```bash
# Stop the app to prevent writes during restore
docker compose stop app

# Restore from backup (replace FILENAME with actual backup)
docker compose cp /backups/huible/FILENAME.dump postgres:/tmp/restore.dump
docker compose exec postgres pg_restore \
    -U huible \
    --clean \
    --if-exists \
    --dbname=huible \
    /tmp/restore.dump

# Restart app
docker compose start app

# Verify
curl http://localhost:8000/api/v1/health
docker compose exec postgres psql -U huible -c "SELECT count(*) FROM memories;"
```

### 9.4 WAL Archiving (Continuous Backup)

For point-in-time recovery (PITR), enable WAL archiving in `docker-compose.yml`:

```yaml
postgres:
  environment:
    POSTGRES_USER: huible
    POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    POSTGRES_DB: huible
  command: >
    postgres
    -c wal_level=replica
    -c archive_mode=on
    -c archive_command='cp %p /backups/wal_archive/%f'
```

Then create the archive directory and mount it:

```yaml
postgres:
  volumes:
    - pgdata:/var/lib/postgresql/data
    - ./backups/wal_archive:/backups/wal_archive
    - ./docker/init-db:/docker-entrypoint-initdb.d
```

### 9.5 Recovery Objectives

| Metric | Target | Notes |
|--------|--------|-------|
| **RTO** (Recovery Time Objective) | < 15 minutes | From backup detection to app serving requests |
| **RPO** (Recovery Point Objective) | < 24 hours (daily dump) | < 5 minutes with WAL archiving enabled |

---

## 10. Troubleshooting

### 10.1 Container Won't Start

```bash
# Check logs
docker compose logs app
docker compose logs postgres

# Common causes:
# 1. Port conflict — another process using 8000, 5432, 80, or 443
#    lsof -i :8000
# 2. .env missing — copy .env.example and fill required fields
# 3. Database not ready — postgres healthcheck may take 30s on first boot
```

### 10.2 Database Connection Failures

```bash
# Test connectivity
docker compose exec app python -c "
import asyncio, asyncpg
async def test():
    conn = await asyncpg.connect('postgresql://huible:changeme@postgres:5432/huible')
    ver = await conn.fetchval('SELECT version()')
    print(ver)
    await conn.close()
asyncio.run(test())
"

# Check pgvector is enabled
docker compose exec postgres psql -U huible -c "SELECT * FROM pg_extension WHERE extname = 'vector';"

# Check tables exist
docker compose exec postgres psql -U huible -c "\dt"
```

### 10.3 Health Check Failing

```bash
# Manual health check
curl -v http://localhost:8000/api/v1/health

# If degraded, check database connectivity
docker compose exec postgres pg_isready -U huible

# If database is fine but app reports degraded:
docker compose logs app --tail 50
```

### 10.4 Caddy TLS Issues

```bash
# Check Caddy logs
docker compose logs caddy

# Common causes:
# 1. DNS not pointing to server — verify with dig <domain>
# 2. Port 443 blocked by firewall — check ufw/iptables
# 3. Rate limited by Let's Encrypt — wait 1 hour and retry
# Force certificate renewal:
docker compose restart caddy
```

### 10.5 High Memory Usage

```bash
# Check container resource usage
docker stats

# Common causes:
# 1. Large memory count — check row count
docker compose exec postgres psql -U huible -c "SELECT count(*) FROM memories;"
# 2. HNSW index size — check index bloat
docker compose exec postgres psql -U huible -c "
SELECT pg_size_pretty(pg_relation_size('idx_memories_content_emb')),
       pg_size_pretty(pg_relation_size('idx_memories_sensory_emb')),
       pg_size_pretty(pg_relation_size('idx_memories_affect_emb'));
"
# 3. Connection pool leaks — check active connections
docker compose exec postgres psql -U huible -c "SELECT count(*) FROM pg_stat_activity WHERE datname='huible';"
```

### 10.6 Reset Everything

Nuclear option — destroys all data:

```bash
docker compose down -v
docker compose up -d
```

---

## 11. Environment-Specific Overrides

### Development

```bash
# .env.development
HUIBLE_ENV=development
HUIBLE_LOG_LEVEL=DEBUG
EMBEDDING_PROVIDER=fake
POSTGRES_PASSWORD=dev_pass
```

```bash
docker compose --env-file .env.development up -d
```

### Staging

```bash
# .env.staging
HUIBLE_ENV=staging
HUIBLE_LOG_LEVEL=INFO
EMBEDDING_PROVIDER=openai
OPENAI_API_KEY=sk-staging-key
HUIBLE_DOMAIN=staging.huible.example.com
```

### Production

```bash
# .env.production
HUIBLE_ENV=production
HUIBLE_LOG_LEVEL=INFO
EMBEDDING_PROVIDER=openai
OPENAI_API_KEY=sk-prod-key
HUIBLE_DOMAIN=huible.example.com
POSTGRES_PASSWORD=<strong-random-secret>
```

---

## 12. File Reference

| File | Purpose |
|------|---------|
| `Dockerfile` | Multi-stage container build |
| `docker-compose.yml` | Service orchestration (app, postgres, caddy) |
| `Caddyfile` | Reverse proxy routes + automatic HTTPS |
| `.env.example` | Environment variable template |
| `docker/init-db/01-enable-pgvector.sql` | pgvector extension init |
| `docker/init-db/02-schema.sql` | Full schema (personas, memories, edges, quarantine) |
| `migrations/schema.sql` | Canonical DDL (source of truth) |
| `alembic.ini` | Alembic migration configuration |

---

## References

- [BHAA-1318](/BHAA/issues/BHAA-1318) — Engine Specification (architecture, tech stack)
- [BHAA-1319](/BHAA/issues/BHAA-1319) — Data Model (schema)
- [BHAA-1321](/BHAA/issues/BHAA-1321) — Build Plan (Phase 4.3: Docker & Deployment)
- [BHAA-1337](/BHAA/issues/BHAA-1337) — This issue
