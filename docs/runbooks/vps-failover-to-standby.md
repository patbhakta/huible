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
| `127.0.0.1:5432` | system PostgreSQL (host-installed, pid `postgres`) | **Resolved.** Committed override `docker-compose.failover.yml` remaps the compose PG host binding to `127.0.0.1:5433` (validated via `docker compose config` on 2026-08-14; `!override` replaces the base mapping, so no collision). System PG keeps 5432 — Kestra persistence depends on it. |
| `127.0.0.1:5984` + `100.101.235.117:5984` | `couchdb-livesync` container (`couchdb:3`, up) | **Already satisfies the CouchDB requirement** — see §3.3. |
| `127.0.0.1:2019` | system Caddy (admin API) | ⚠ Revisit at cutover: system Caddy already binds `80`/`443`, so the compose Caddy service **cannot** also bind them. Either stop system Caddy or proxy through it. |
| `*:8080` | **Kestra already running** (systemd `kestra.service`, java) | **Already satisfies the Kestra requirement** — see §3.4. |
| `80` / `443` | system Caddy (`caddy`, pid on host) | Not free — compose Caddy conflicts. Prefer the existing system Caddy (add a site block proxying to `127.0.0.1:8000`) instead of the compose Caddy service. |

### Already-running production services on `.245` (verified 2026-08-14)

The standby is further along than a bare host — these are live right now:

| Service | State on `.245` | Detail |
|---------|-----------------|--------|
| Kestra | **running** (systemd `kestra.service`) | `:8080` responds (307 → UI); API auth-protected; standalone mode with Postgres persistence (`/root/.kestra/config.yml`, starter `/opt/kestra/start.sh`); env `/opt/kestra/kestra.env` (rotated credential, mode 600) |
| CouchDB | **running** (container `couchdb-livesync`) | v3.5.2, admin `obsidian`, holds the **live vault store** `obsidian-livesync` — see §3.3 |
| Caddy | **running** (system service) | binds `:80`/`:443`; system Caddy, not compose |
| PostgreSQL | **running** (system) | `127.0.0.1:5432`; used by Kestra persistence |

**Remaining gap for full failover is the Huible app stack only** (compose
`app` on `127.0.0.1:8000` + its pgvector Postgres on `127.0.0.1:5433`).

### Docker images already cached on `.245`

`pgvector/pgvector:pg17` and `couchdb:3` are present locally, so the first
bring-up does not need a registry pull (useful if the outage is
network-related).

---

## 2. Pre-cutover prep (safe to do anytime)

These steps do not move traffic or start production services. They reduce the
post-approval execution time from hours to minutes.

1. **Confirm the repo is current** on `.245` — ✅ done 2026-08-14 (clean at the
   failover-runbook commit).
2. **Stage a failover `.env`** — ✅ done 2026-08-14: `.env.failover` staged on
   `.245` (gitignored, mode 600) with a **fresh** `POSTGRES_PASSWORD` (openssl
   rand; never existed on `.243`). **One input still missing:** the real
   `HUIBLE_DOMAIN` — the value was stranded in the `.243` `.env`, so the
   board/operator must supply it before cutover (DNS repoint needs it too).
   `LLM_PROVIDER` intentionally left `fake` pending board approval `74a0ff8b`.
3. **Resolve the PG port conflict** — ✅ done 2026-08-14: committed
   `docker-compose.failover.yml` remaps compose PG to `127.0.0.1:5433`.
   Merge-validated via `docker compose config` (base + override + failover env):
   PG host port `5433`, app `127.0.0.1:8000`, no collision with system PG.

Bring-up command at cutover becomes:

```bash
cd /root/repos/huible
ln -sf .env.failover .env   # compose requires the literal .env (env_file: .env)
docker compose -f docker-compose.yml -f docker-compose.failover.yml up -d
```

---

## 3. Cutover procedure (board-authorized only)

Execute in order. Each step has a verification gate; do not proceed if a check
fails.

### 3.1 Start the application stack

```bash
cd /root/repos/huible
ln -sf .env.failover .env   # compose requires the literal .env (env_file: .env)
docker compose -f docker-compose.yml -f docker-compose.failover.yml up -d
```

⚠ **Caddy note:** system Caddy already owns `80`/`443` on `.245`. Do **not**
let the compose `caddy` service bind them (conflict). Either start only the
needed services (`docker compose ... up -d app postgres`) and add a site block
to the system Caddy proxying `HUIBLE_DOMAIN` → `127.0.0.1:8000`, or stop system
Caddy first. Prefer the site-block path — Kestra's ingress on `.245` may already
depend on system Caddy.

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

### 3.3 CouchDB — ✅ already live on `.245`, independent of `.243` (verified 2026-08-14)

Definitive finding (closes the §6 open question): the `couchdb-livesync`
container on `.245` holds the **live vault store**:

- Database `obsidian-livesync`: **4160 docs / 14.4 MB on disk** (update_seq
  4362), containing the real vault content (`vps/*`, `work-extra/*`, …).
- `_replicator` has **0 docs** and `_scheduler/jobs` is empty → **no
  replication relationship with `.243`**, configured or past. This instance is
  the independent live target, not a mirror.
- The exposed admin credential from HU-1500 has **already been rotated here**
  (2026-08-14, verified complete under HU-1500; the rotated value lives in
  `/opt/kestra/kestra.env` on `.245`, mode 600, and Kestra is consuming it).

Cutover action for CouchDB reduces to: verify the DB responds and doc count is
sane — no restore, no rotation, no re-seed needed.

```bash
source /opt/kestra/kestra.env
curl -s -u "obsidian:$COUCH_ADMIN_PASS" http://localhost:5984/obsidian-livesync \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print("docs:", d["doc_count"])'
# expect ~4160
```

### 3.4 Kestra — ✅ already running on `.245` (verified 2026-08-14)

Kestra does **not** need to be stood up. It runs as a systemd service on the
standby:

- Unit: `kestra.service` (`loaded active running`), standalone mode, Postgres
  persistence, config `/root/.kestra/config.yml`, starter `/opt/kestra/start.sh`.
- `:8080` responds (307 → UI); the API is auth-protected.
- Env `/opt/kestra/kestra.env` carries the rotated `COUCH_ADMIN_PASS` (HU-1500
  verified Kestra is consuming the new credential).
- Live flows `huible/huible-vault-create` and `huible/huible-vault-archive`
  source the credential via `{{ envs.COUCH_ADMIN_PASS }}` — a dead hardcoded
  `b756e723…` literal (pre-rotation hotfix divergence from the repo files)
  was purged from live flow sources on 2026-08-14 (HU-1501, revisions 14 / 8;
  verified: env credential → HTTP 200, dead literal → HTTP 401). Repo
  `flows/vault-{create,archive}.yaml` were already clean and match this
  intent; live revisions still differ in shape from the repo files, so
  reconcile before any bulk re-apply.

Cutover action reduces to **verify + flows check**:

```bash
systemctl is-active kestra.service            # expect: active
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8080/   # expect 200/307
# Confirm the huible-namespace flows are present (UI or authenticated API);
# re-apply flows/*.yaml if any are missing after the standby restart.
```

### 3.5 Tailscale / DNS cutover

The standby's tailnet identity is `ip-208-84-102-245` (`100.101.235.117`) —
different from the prod nodes. Update anything that hardcodes the old tailnet
IPs (`100.109.142.4`, `100.75.34.75`):

- Kestra webhook triggers (`flows/*.yaml` webhook keys stay the same; only the
  public ingress URL changes).
- Any external integrations that point at `.243`.

If the public DNS for `HUIBLE_DOMAIN` pointed at `.243`, repoint it to `.245`
(`208.84.102.245`). ⚠ The real `HUIBLE_DOMAIN` value is stranded in the `.243`
`.env` — obtain it from the board/operator before this step. TLS: the system
Caddy on `.245` will auto-provision a cert for the domain once the site block
exists.

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

- ~~**CouchDB data backup:** verify whether `couchdb-livesync` on `.245` has a
  replication relationship with the `.243` instance, or is independent.~~
  **Closed 2026-08-14:** independent live store — no `_replicator` docs, no
  scheduler jobs; 4160 docs of real vault data. See §3.3.
- **Kestra config backup:** `.243`'s `/etc/kestra/kestra.env` remains stranded
  (host down), but `.245` now carries a working, rotated env at
  `/opt/kestra/kestra.env`. Fold that file into the `docs/09` §9 backup
  strategy (secret-safe location) so it is not itself a single-host SPOF.
- **DNS automation:** the cutover still requires a manual DNS repoint, and the
  real `HUIBLE_DOMAIN` value is stranded on `.243` — the board/operator must
  supply it before cutover. Consider a lower-TTL record or a floating IP for
  faster future failovers.
- **System Caddy vs compose Caddy (new, 2026-08-14):** `.245`'s system Caddy
  binds `80`/`443`. Decide the proxy path (site block vs stopping system
  Caddy) before starting the compose stack.
