# Runbook: Retire LiveSync/CouchDB → fast-note-sync-service (FNS)

Issue: HU-1681 (CEO decision Aug 14). All commands run **locally on .245**
(the agent host IS `.245`; see `docs/runbooks/vps-failover-to-standby.md` §note).

## Constraint

**Do not collide with HU-1644 prod cutover** (gate 2026-08-15 12:00 UTC).
Any step that touches shared ingress on .245 (Caddy reload, ufw, Docker
network changes beyond the isolated FNS container) waits until HU-1644
reaches a terminal state.

## Current verified state (2026-08-14 ~23:10 UTC)

| Item | State |
|------|-------|
| CouchDB `obsidian-livesync` | up (couchdb:3, v3.5.2), 4,160 docs, port 5984 tailnet+loopback only |
| Full backup dump | **DONE** — `/data/backups/couchdb-livesync/obsidian-livesync-fulldump-20260814T231213Z.json.gz` (7.4 MB, sha256 `a14d7c57ecf27458044e118e5c69658abb8a8662cbbc8eb5f7309e85c02e86e4`, 4160/4160 rows, 0 deleted, 0 attachments) |
| FNS container | up — `haierkeys/fast-note-sync-service:latest` (v3.6.1), launched **manually from an interactive session** 2026-08-14T23:09Z, stock config (`/data/fast-note-sync/config/config.yaml`), health OK, SQLite connected |
| FNS exposure | 9000 bound to `100.101.235.117` + `127.0.0.1` only — tailnet-scoped, not public |
| FNS auth | FNS internal token auth (config auto-generated `auth-token-key`; never copy this into docs/git) |
| Caddy | `brain.bhakta.us` block still proxies :5984 (untouched) |
| Declarative deploy | `deploy/fast-note-sync/docker-compose.yml` (committed; mirrors the manual run) |

## Remaining steps (in order)

### S2. Adopt FNS under compose management (no functional change)

The running container was started by hand. Adopt it declaratively:

    cd /root/repos/huible/deploy/fast-note-sync
    docker stop fast-note-sync && docker rm fast-note-sync
    docker compose up -d
    curl -s http://127.0.0.1:9000/api/health   # expect status:true, database:connected

Data survives: state lives in `/data/fast-note-sync/{config,storage}` host volumes,
so a container replace is non-destructive. **Coordinate first** — Pat was
interactively testing FNS at deploy time; confirm the session is done before
touching the container.

### S3. Pat installs `obsidian-fast-note-sync` plugin on devices

Plugin pairs with `http://100.101.235.117:9000` (tailnet) or Caddy subdomain
once DNS exists (see S4 note). Validate end-to-end: edit on phone → server
`storage/` git auto-push → vault clone updated.

### S4. (Optional) Caddy subdomain — post-gate only

No public DNS record exists for a new hostname (HU-1644 pre-flight
reassessment). Until DNS is decided, tailnet access suffices. If/when Pat
wants a public URL: add block proxying to `127.0.0.1:9000`, DNS first,
reload Caddy — **only after HU-1644 is terminal**.

### S5. Retire the old stack — only after S3 validated on all devices

1. `docker stop couchdb-livesync && docker rm couchdb-livesync` (image `couchdb:3` may stay cached for rollback)
2. Remove `brain.bhakta.us` block from `/root/repos/huible/Caddyfile` + reload
   (keep the dump + any file-level backup for 30+ days before deleting)
3. Kestra — **decided: retire** (HU-1706, option A; repo files already removed):
   - delete the deployed flows `huible/huible-vault-create` and
     `huible/huible-vault-archive` via UI/API (repo sources removed in the
     HU-1706 branch; live revisions were the only remaining copies)
   - remove `COUCH_ADMIN_PASS` from `/opt/kestra/kestra.env` (it is the only
     var in that file) and `systemctl restart kestra.service`
   - update `scripts/execute_failover.sh` CouchDB checks (env-var grep +
     doc-count curls + container checks) to treat the retired stack as
     healthy/skip — NOT before S5 executes; the script must stay intact for
     the HU-1644 cutover window
   - optional host debris: untracked `scripts/livesync/node_modules/`
     (PouchDB-era leftovers, never committed) may be deleted
   - client provisioning rewrite against FNS REST is deferred until a real
     client onboards — [HU-1707](/HU/issues/HU-1707)

## Rollback

Restore CouchDB docs from the dump if LiveSync must come back:

    zcat obsidian-livesync-fulldump-*.json.gz | jq -c '.rows[].doc' > docs.ndjson
    # then POST each doc to http://127.0.0.1:5984/obsidian-livesync (obsidian user)
    # (bulk _bulk_docs with {"new_edits": true} preserves revs; CouchDB container
    #  is retired only in S5, so the target exists until then)

Vault git remote stays source of truth for TencentDB L1 ingestion throughout —
FNS's git auto-push must target the same remote (verify in S3).
