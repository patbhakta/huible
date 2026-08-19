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

## ⚠ Canonical addresses (single source of truth — HU-1777)

Since the 2026-08-15 cutover (HU-1715), **production is served from
`208.84.102.245`** (this standby host; reverse DNS
`ip-208-84-102-245.my-advin.com`, tailnet `100.101.235.117`).

- **Prod edge public IP: `208.84.102.245`** — Caddy :80/:443, SSH :22,
  ufw allowlist 22/80/443 (HU-1672). Health pin: HTTP `:80` → `308`.
- Old prod `.243` (`208.84.102.243`) is **decommissioned/dark** — do not probe
  it for prod posture.
- Huible owns **no OVH infrastructure** and no host in `158.69.0.0/16`. On
  2026-08-16 an incident (HU-1777) was opened from probes against
  `158.69.122.245` — a third-party OVH box — while the real edge was green the
  entire time.

When probing prod, take the IP from this section verbatim. If a probe target
came from anywhere else, verify it against this list before declaring an
outage.

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
| `127.0.0.1:2019` | system Caddy (admin API) | Resolved 2026-08-14: compose Caddy is excluded on this host (`compose-caddy` profile in `docker-compose.failover.yml`); system Caddy keeps `80`/`443` and fronts the app via the §3.1b site block. |
| `*:8080` | **Kestra already running** (systemd `kestra.service`, java) | **Already satisfies the Kestra requirement** — see §3.4. |
| `80` / `443` | system Caddy (`caddy`, pid on host) | Not free — compose Caddy conflicts. Prefer the existing system Caddy (add a site block proxying to `127.0.0.1:8000`) instead of the compose Caddy service. |
| `127.0.0.1:9090` / `127.0.0.1:9100` | Huible monitoring (`huible-prometheus`, `huible-node-exporter`, HU-1742) | No conflict — loopback-only. Started via the compose `monitoring` profile (`docker compose -f docker-compose.yml -f docker-compose.failover.yml --profile monitoring up -d`); §8 disk alert `HuibleDiskFreeLow` evaluates here. No public listener added. |

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

**Caddy path (decided 2026-08-14):** the compose `caddy` service is
**excluded on this host** — `docker-compose.failover.yml` pins it behind the
`compose-caddy` profile, so the default `up -d` starts only `app` + `postgres`
(merge-validated: no `80`/`443` binding conflict with the system Caddy).
Public ingress is the **system Caddy site block** installed in §3.1b. Do not
stop the system Caddy — Kestra's ingress on `.245` depends on it.

**Verify:**
```bash
docker compose ps                         # app, postgres = Up (caddy excluded on standby)
curl -s http://127.0.0.1:8000/api/v1/health
# expect: {"data":{"status":"ok","version":"0.1.0"}}
docker compose exec postgres pg_isready -U huible
```

Run Alembic migrations if the PG volume is fresh:
```bash
docker compose exec app alembic upgrade head
```

### 3.1b Install the system-Caddy site block (public ingress)

The prepared block lives in the repo:
[`deploy/caddy-standby/huible-site.caddy`](../../deploy/caddy-standby/huible-site.caddy)
— it proxies `{$HUIBLE_DOMAIN}` → `127.0.0.1:8000` over the same route surface
as the compose Caddyfile (`/api/v1/health`, `/api/v1/*`, `/static/*`, else
404). `HUIBLE_DOMAIN` executes at `localhost` parity for this cutover (no
public domain exists yet — see §6 DNS item); public ingress is a post-cutover
follow-up on the pending launch-readiness decision.

```bash
# On .245 directly (the agent host IS .245 — see note below):
cp deploy/caddy-standby/huible-site.caddy /etc/caddy/huible-site.caddy
grep -q 'import huible-site.caddy' /etc/caddy/Caddyfile || \
    echo 'import huible-site.caddy' >> /etc/caddy/Caddyfile
HUIBLE_DOMAIN=<domain> caddy validate --config /etc/caddy/Caddyfile  # MUST pass
```

**Activation reload (domain → running daemon, zero-downtime):**

```bash
# `systemctl reload caddy` does NOT pick up HUIBLE_DOMAIN — env expansion happens
# in the adapting process, and the daemon's env is fixed at start. The CLI reload
# adapts the config in a fresh process (shell env applies) and pushes the JSON to
# the running daemon — no restart, other sites untouched:
HUIBLE_DOMAIN=<domain> caddy reload --config /etc/caddy/Caddyfile
```

**Reboot durability:** a staged systemd drop-in makes the env survive restarts
(done 2026-08-19, HU-1743 pre-staging):

```bash
# /etc/systemd/system/caddy.service.d/huible-domain.conf  (already staged on .245)
[Service]
Environment=HUIBLE_DOMAIN=huible.bhakta.us
```

**Verify (after DNS repoint, §3.6):** `curl -s https://$HUIBLE_DOMAIN/api/v1/health`
returns the ok payload. TLS is auto-provisioned by Caddy for the domain once
DNS resolves to `.245`.

> Operational note (updated 2026-08-14, pre-flight): **the Paperclip agent host
> IS `.245`** (`hostname -f` = `ip-208-84-102-245.my-advin.com`; public
> `208.84.102.245`, tailnet `100.101.235.117`). All runbook commands run
> **locally** — no SSH/scp hop, and root SSH to self is key-denied anyway
> (`.245` does not run the Tailscale SSH server). The `/etc/caddy` layout is
> now confirmed: **single-file `/etc/caddy/Caddyfile`** with inline site blocks
> (`paperclip.bhakta.us`, `investinme.club`, `edu.investinme.club`,
> `doit.investinme.club`, `school.bhakta.us`, `golf.bhakta.us`,
> `kestra.bhakta.us`, `brain.bhakta.us`) and no existing imports. The
> `import huible-site.caddy` append + `caddy validate` pattern was
> **dry-run validated green** against a copy of the live Caddyfile on
> 2026-08-14 (`Valid configuration`, with `HUIBLE_DOMAIN=localhost`).

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
- **2026-08-15 (HU-1706):** these two flows and their scripts are **retired**
  with the CouchDB stack — repo sources deleted; HU-1681 S5 deletes the
  deployed flows, drops `COUCH_ADMIN_PASS` from `kestra.env`, and restarts
  `kestra.service`. Future client provisioning moves to FNS REST
  ([HU-1707](/HU/issues/HU-1707)).

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
(`208.84.102.245`). Reassessed 2026-08-14: **no public DNS record existed for
the app** (see §6 DNS item) — this step is a no-op for this cutover; public
ingress stays gated on the pending launch-readiness board decision. TLS: the
system Caddy on `.245` will auto-provision a cert for the domain once a real
domain is chosen, DNS resolves to `.245`, and the site block is reloaded.

---

## 4. Post-cutover verification

Run the full launch-verification suite before declaring the failover complete:

```bash
bash scripts/verify_vps_recovery.sh   # defaults target .245 (current prod) since HU-1823
bash scripts/verify_prod_external.sh
bash scripts/verify_prod_hardening.sh
```

> Note (updated 2026-08-16, HU-1823): `verify_vps_recovery.sh` now **defaults
> to the current prod targets** (`.245` / `100.101.235.117` /
> `ip-208-84-102-245`) and skips the retired Kestra/CouchDB checks (notes, not
> failures — HU-1706/HU-1681), adding the edge `:80 → 308` pin instead. A bare
> run is the correct standby verification. Probing the decommissioned `.243`
> requires `PROBE_LEGACY_243=1` and exits 2 without it — that guard exists
> because defaulting to `.243` opened the HU-1823 false incident while prod was
> green. Legacy command, kept for archaeology only:
> ```bash
> PROBE_LEGACY_243=1 VPS_PUBLIC=208.84.102.243 VPS_TS_IP=100.109.142.4 \
> KESTRA_TS_IP=100.75.34.75 TS_NODE_VPS=ip-208-84-102-243 TS_NODE_KESTRA=kestra-on-vps \
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
- ~~**Kestra config backup:** `.243`'s `/etc/kestra/kestra.env` remains stranded
  (host down), but `.245` now carries a working, rotated env at
  `/opt/kestra/kestra.env`. Fold that file into the `docs/09` §9 backup
  strategy (secret-safe location) so it is not itself a single-host SPOF.~~
  **Closed 2026-08-14 (local layer):** `scripts/backup_kestra_config.sh` now
  snapshots the rotated env + server config daily (cron 03:30 UTC) to
  `/backups/kestra-config/` — 0600, sha256-sealed, 30-day retention, restore
  procedure in `docs/09` §9.2e. **Residual (tracked):** off-host copy is
  deliberately disabled until the board names a secret-safe destination
  (approval `5e713a10`, same decision that names the second operator /
  credential deposit). No plaintext credential ever enters git — that is the
  HU-1500 leak class.
- **DNS automation:** ~~the cutover still requires a manual DNS repoint, and
  the real `HUIBLE_DOMAIN` value is stranded on `.243` — the board/operator
  must supply it before cutover.~~ **Reassessed 2026-08-14 (pre-flight):** no
  public DNS record or domain ever existed for the Huible app — the vault
  infrastructure table (`VPS/infrastructure.md`) lists domains only for
  paperclip/kestra/investinme/brain, and the docs/09 §8 checklist item
  "HUIBLE_DOMAIN is set to a real domain with DNS A record" was still an open
  pre-launch item (tracked by [HU-1464] sign-off / launch readiness). Prod on
  `.243` served the app on `127.0.0.1:8000` behind compose Caddy, reached over
  the tailnet. **Cutover therefore executes at domain parity
  (`HUIBLE_DOMAIN=localhost`, as staged in `.env.failover`) with no DNS
  repoint** — public ingress/TLS remains gated on the pre-existing
  launch-readiness board decision, unaffected by this failover. Consider a
  lower-TTL record or a floating IP once a real domain is chosen, for faster
  future failovers.
- ~~**System Caddy vs compose Caddy (new, 2026-08-14):** `.245`'s system Caddy
  binds `80`/`443`. Decide the proxy path (site block vs stopping system
  Caddy) before starting the compose stack.~~
  **Closed 2026-08-14 — site-block path, prepared:** compose caddy excluded on
  the standby via the `compose-caddy` profile in `docker-compose.failover.yml`
  (merge-validated: default stack = `app` + `postgres` only, ports
  `127.0.0.1:8000` / `127.0.0.1:5433`, no `80`/`443` binding). Prepared site
  block at `deploy/caddy-standby/huible-site.caddy` + install/reload procedure
  in §3.1b. System Caddy is never stopped (Kestra ingress depends on it).
  Grounding probe: system Caddy answers `:80` with 308→https (auto-HTTPS),
  `:443` TLS only for its named sites — consistent with the site-block model.
- **Operational caveat (new, 2026-08-14):** Kestra **script-task** executions
  (`io.kestra.plugin.scripts.shell.Script`) hang `RUNNING` with no task logs on
  `.245`'s standalone Kestra (observed with both the default Docker runner and
  `Process`). Flow CRUD/API access works fine. Do **not** build cutover-time
  verification on Kestra script tasks on this host; use direct SSH/compose
  commands. Probe flow + executions were deleted (namespace = the 2 prod flows
  only).
