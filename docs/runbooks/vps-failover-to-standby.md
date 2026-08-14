# Runbook: Fail Over Production to Standby Host (`208.84.102.245`)

**Purpose:** Bring the full Huible production stack back online on the standby
host when the primary prod VPS (`208.84.102.243`) is unreachable for an extended
period and recovery is not imminent. This is the "host is gone" disaster-recovery
path that `docs/09` §9 (Backup & Restore) does not cover — §9 assumes the host is
up and you are restoring data in place.

**Scope:** Application stack + Kestra + CouchDB + Tailscale on the standby. This
runbook does **not** cover provider-console power-on (see
[`provider-console-break-glass.md`](./provider-console-break-glass.md)) or
same-host restore (see `docs/09` §9).

**Authorization gate:** Standing up production services on a new host is a
board-level decision (topology change, potential data-stranding, DNS/cutover
risk). Do **not** execute the cutover (§3 onward) until the board approves —
tracked by the incident approval (e.g. approval `4d156bd9` path (b)). Sections
§1–§2 (verification + prep) are safe to run anytime as readiness checks.

**Origin:** Post-incident prep for HU-1501 (prod VPS offline ~3 days, launch
chain frozen). Verified against the actual standby state on 2026-08-14.

---

## 0. Production topology on the primary VPS (`.243`)

These are the services that must be reproduced on the standby:

| Service | How it runs on `.243` | Port | Config source |
|---------|----------------------|------|---------------|
| Huible app (`app`) | docker-compose service | 127.0.0.1:8000 | `docker-compose.yml`, `.env` |
| PostgreSQL + pgvector | docker-compose service (`pgvector/pgvector:pg17`) | 127.0.0.1:5432 | `docker-compose.yml`, `.env` |
| Caddy (reverse proxy) | docker-compose service | 80, 443 | `Caddyfile` (`{$HUIBLE_DOMAIN}`) |
| Kestra | systemd service **or** docker-compose under `/etc/kestra/`, `/opt/kestra/`, or `/root/kestra/` | 8080 (public) | env file with `COUCH_ADMIN_PASS`, `GITHUB_TOKEN`, … |
| CouchDB | standalone (localhost-bound on `.243`) | 5984 | admin user `obsidian` |
| Tailscale node 1 | `ip-208-84-102-243` | 100.109.142.4 | tailnet |
| Tailscale node 2 | `kestra-on-vps` | 100.75.34.75 | tailnet |

Kestra flows live in `flows/*.yaml` in this repo and are namespace `huible`.

---

## 1. Standby host verification (`.245`)

Run these checks before relying on `.245` as the failover target. All were
**verified green on 2026-08-14**; re-run to confirm current state.

```bash
# Host identity — must be the standby, not the down VPS.
hostname -f   # expect: ip-208-84-102-245.my-advin.com

# Docker + Compose available.
docker --version        # Docker 24+ (verified 29.7.2)
docker compose version  # Compose V2 (verified v5.4.0)

# Tailscale online (this host's tailnet IP is 100.101.235.117).
tailscale status | grep ip-208-84-102-245   # must NOT say "offline"

# Disk headroom — app stack + images need ~5 GB free.
df -h /   # verified 28 GB available

# Repo present and on the right commit.
git -C /root/repos/huible log --oneline -1
```

### Port-conflict map on `.245` (verified 2026-08-14)

The standby already runs several unrelated services. Plan around these
collisions before starting the app stack:

| Port | In use by | Failover action |
|------|-----------|-----------------|
| `127.0.0.1:5432` | system PostgreSQL (host-installed, pid `postgres`) | **Conflict.** Either stop the system PG or remap the compose PG to `127.0.0.1:5433` and set `POSTGRES_PORT=5433` in `.env`. |
| `127.0.0.1:5984` + `100.101.235.117:5984` | `couchdb-livesync` container (`couchdb:3`, up) | **Already satisfies the CouchDB requirement** — see §3.3. |
| `127.0.0.1:2019` | system Caddy (admin API) | No conflict with compose Caddy (which uses 80/443). |
| `80` / `443` | free | Compose Caddy can bind here. |

### Docker images already cached on `.245`

`pgvector/pgvector:pg17` and `couchdb:3` are present locally, so the first
bring-up does not need a registry pull (useful if the outage is
network-related).

---

## 2. Pre-cutover prep (safe to do anytime)

These steps do not move traffic or start production services. They reduce the
post-approval execution time from hours to minutes.

1. **Confirm the repo is current** on `.245`:
   ```bash
   git -C /root/repos/huible fetch origin
   git -C /root/repos/huible status        # must be clean
   git -C /root/repos/huible log --oneline -1 origin/main
   ```
2. **Stage a failover `.env`** (do not overwrite the prod `.env` on `.243` —
   that file is unreachable. Build a fresh one):
   ```bash
   cp /root/repos/huible/.env.example /root/repos/huible/.env.failover
   # Edit .env.failover:
   #   POSTGRES_PASSWORD = <fresh strong secret>
   #   HUIBLE_DOMAIN     = <the public domain>
   #   Generate API_KEYS as needed.
   ```
3. **Resolve the PG port conflict** — either plan to stop the system PostgreSQL
   (`sudo systemctl stop postgresql`) or set `POSTGRES_PORT=5433` in
   `.env.failover` and remap the host port in a compose override.

---

## 3. Cutover procedure (board-authorized only)

Execute in order. Each step has a verification gate; do not proceed if a check
fails.

### 3.1 Start the application stack

```bash
cd /root/repos/huible
# Use the failover env (port-remapped if needed).
ln -sf .env.failover .env
docker compose up -d
```

**Verify:**
```bash
docker compose ps                         # app, postgres, caddy = Up
curl -s http://127.0.0.1:8000/api/v1/health
# expect: {"data":{"status":"ok","version":"0.1.0"}}
docker compose exec postgres pg_isready -U huible
```

Run Alembic migrations if the PG volume is fresh:
```bash
docker compose exec app alembic upgrade head
```

### 3.2 Seed / restore data

If a recent `pg_dump` backup exists (see `docs/09` §9.2), restore it:
```bash
docker compose cp /path/to/huible_backup.dump postgres:/tmp/restore.dump
docker compose exec postgres pg_restore \
    -U huible --clean --if-exists --dbname=huible /tmp/restore.dump
```

If **no backup is reachable** (it was stranded on `.243`), start empty and
re-seed:
```bash
docker compose exec app python -m scripts.seed_data \
    --url "postgresql://huible:${POSTGRES_PASSWORD}@postgres:5432/huible"
```
Document the data-loss window in the incident thread.

### 3.3 CouchDB

`.245` already runs CouchDB (`couchdb-livesync`, `couchdb:3`) bound to the
tailnet IP `100.101.235.117:5984`. Two scenarios:

- **If this instance already holds the live vault data** (e.g. it was the
  active LiveSync target): verify and proceed.
  ```bash
  curl -s http://100.101.235.117:5984/_all_dbs | python3 -m json.tool
  ```
- **If it is a fresh instance**: recreate the vault databases via the
  `huible-vault-create` Kestra flow after Kestra is up (§3.4), or manually
  provision per `scripts/vault_create.py`.

In both cases, run the credential rotation immediately (the exposed admin
password from HU-1500 must be neutralized):
```bash
# On .245, against the local CouchDB:
export COUCH_ADMIN_PASS='<current live password>'
COUCH_URL=http://localhost:5984 COUCH_ADMIN_USER=obsidian \
  bash scripts/rotate_couch_admin_pass.sh
```

### 3.4 Kestra

Kestra is **not** in the app `docker-compose.yml`. Stand it up on `.245`:

1. Deploy Kestra (Docker is the fastest path on `.245`):
   ```bash
   # Pull the official Kestra image.
   docker pull kestra/kestra:latest-full
   # Run with a data volume and the tailnet-visible port.
   docker run -d --name kestra \
     --restart unless-stopped \
     -p 8080:8080 \
     -v kestra_data:/app/storage \
     -v /root/repos/huible/flows:/app/flows:ro \
     kestra/kestra:latest-full server
   ```
2. Recreate the Kestra env file with fresh secrets (the old
   `/etc/kestra/kestra.env` is stranded on `.243`):
   ```bash
   # /root/repos/huible/.kestra.env  (chmod 600)
   # COUCH_ADMIN_PASS=<rotated value from §3.3>
   # GITHUB_TOKEN=<token with repo scope to patbhakta>
   ```
3. Register the Huible flows from `flows/*.yaml` (namespace `huible`) via the
   Kestra CLI or UI at `http://208.84.102.245:8080`.

**Verify:**
```bash
curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/   # expect 200/302
```

### 3.5 Tailscale / DNS cutover

The standby's tailnet identity is `ip-208-84-102-245` (`100.101.235.117`) —
different from the prod nodes. Update anything that hardcodes the old tailnet
IPs (`100.109.142.4`, `100.75.34.75`):

- Kestra webhook triggers (`flows/*.yaml` webhook keys stay the same; only the
  public ingress URL changes).
- Any external integrations that point at `.243`.

If the public DNS for `HUIBLE_DOMAIN` pointed at `.243`, repoint it to
`.245` (`208.84.102.245`). Caddy will auto-provision a new TLS cert.

---

## 4. Post-cutover verification

Run the full launch-verification suite before declaring the failover complete:

```bash
bash scripts/verify_vps_recovery.sh   # will need VPS_PUBLIC=208.84.102.245 override
bash scripts/verify_prod_external.sh
bash scripts/verify_prod_hardening.sh
```

> Note: `verify_vps_recovery.sh` defaults to `.243`. Override the env vars to
> target `.245` when verifying the failover:
> ```bash
> VPS_PUBLIC=208.84.102.245 VPS_TS_IP=100.101.235.117 \
>   bash scripts/verify_vps_recovery.sh
> ```

---

## 5. Rollback (if `.243` returns)

If the primary VPS comes back online after the failover:

1. **Do not** automatically flip back. Decide deliberately (data divergence
   risk).
2. Sync any new data written on `.245` back to `.243` (or designate `.245` as
   the new primary and decommission `.243`).
3. If reverting: stop services on `.245`, repoint DNS to `.243`, run
   `verify_vps_recovery.sh` against `.243`, then run the HU-1500 credential
   rotation on `.243`.

---

## 6. Open items

- **Kestra config backup:** `/etc/kestra/kestra.env` on `.243` is not captured
  in this repo or in `docs/09` §9 backups. Add it to the backup strategy so the
  secrets needed to rebuild Kestra are not stranded on a single host.
- **CouchDB data backup:** verify whether `couchdb-livesync` on `.245` has a
  replication/backoff relationship with the `.243` instance, or is independent.
- **DNS automation:** the cutover currently requires a manual DNS repoint;
  consider a lower-TTL record or a floating IP for faster future failovers.
