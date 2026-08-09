# Huible Phase 0 — LiveSync recovery & operations

> **tl;dr** The bundled `self-hosted-livesync` CLI's `sync` command is broken
> (its LiveSync replication wrapper returns `false` right after the CouchDB
> milestone handshake). Phase 0 routes replication through **PouchDB's
> battle-tested CouchDB replication** instead, and keeps the CLI only for what it
> does correctly: `mirror` (DB → filesystem, decrypt/assemble) and `push`/`rm`
> (filesystem → DB, encrypt/chunk). This document is the operator runbook.

## What the fix is

```
remote CouchDB  <-- PouchDB replicate (bridge.mjs) -->  local LevelDB  <-- CLI mirror/push -->  filesystem
https://brain.bhakta.us/obsidian-livesync              /root/repos/brain/.livesync/            /root/repos/brain/
```

- **Transport:** `scripts/livesync/bridge.mjs` — PouchDB ↔ CouchDB replication
  (bidirectional, with retries). Replaces the CLI `sync`.
- **Crypto + chunking:** unchanged, still done by the official livesync CLI
  (`mirror` decrypts/assembles; `push`/`rm` encrypt/chunk). Encryption keys live
  in `/root/repos/brain/.livesync/.livesync/settings.json` (`passphrase`).
- **One command:** `scripts/livesync/huible-livesync <sync|pull|push|mirror|daemon|status|validate>`.
- **Continuous:** `huible-livesync.service` (systemd) runs the daemon, 5s poll,
  `Restart=always`.
- **Proof:** `huible-livesync validate` runs N consecutive full round-trips
  (push → replicate → mirror → verify byte-for-byte) and reports pass/fail + timing.

## Daily operations

```bash
cd /root/repos/huible/scripts/livesync

./huible-livesync status      # local vs remote doc counts; in_sync?
./huible-livesync sync        # one-shot: replicate both ways + mirror
./huible-livesync validate    # prove sync: 10 consecutive round-trips
systemctl status huible-livesync
journalctl -u huible-livesync -f
```

## Config (no secrets in the repo)

The bridge reads connection info from the same settings the CLI uses, so
credentials are never duplicated:

- `HUIBLE_LIVESYNC_SETTINGS`  (default `/root/repos/brain/.livesync/.livesync/settings.json`)
  — contains `couchDB_URI`, `couchDB_USER`, `couchDB_PASSWORD`, `couchDB_DBNAME`,
  `passphrase`.
- `HUIBLE_LIVESYNC_DB_PREFIX` (default `/root/repos/brain/.livesync/`)
- `HUIBLE_LIVESYNC_DB_NAME`   (default `headless-vault-livesync-v2`)
- `HUIBLE_LIVESYNC_VAULT`     (default `/root/repos/brain`)
- `LIVESYNC_CLI`              (default `/root/repos/livesync-cli-build/src/apps/cli/dist/index.cjs`)
- `INTERVAL`                  daemon poll seconds (default 5)

## Diagnosing failures

1. **`huible-livesync status`** — if `in_sync` is false or it errors, the
   transport is broken. Continue below.
2. **CouchDB down?** `docker ps | grep couchdb` then
   `docker start couchdb-livesync`. CouchDB must listen on `5984` and Caddy must
   serve `https://brain.bhakta.us/`.
3. **Remote unreachable?** `curl -s https://brain.bhakta.us/` must return the
   CouchDB welcome JSON. If not, check Caddy (`systemctl status caddy`) and DNS.
4. **Wrong passphrase?** Decryption silently produces garbage and `mirror` prints
   `File X seems to be corrupted! Writing prevented. (a != b)`. Confirm
   `passphrase`/`encrypt` in settings match Pat's Obsidian LiveSync plugin. A few
   such warnings on legacy files are tolerable; widespread corruption means a key
   mismatch.
5. **LevelDB lock?** The CLI and the bridge both open the LevelDB. The bridge
   always closes it before invoking the CLI; if you see
   `Self-hosted LiveSync cannot be initialised`, a stale `LOCK` file remains.
   Stop the daemon, ensure no `node` CLI process is running, then remove
   `/root/repos/brain/.livesync/headless-vault-livesync-v2/LOCK` and restart.

## Re-seeding the local DB from scratch

If the local LevelDB is corrupted or lost, it can be rebuilt entirely from
CouchDB (the remote is the source of truth):

```bash
systemctl stop huible-livesync
rm -rf /root/repos/brain/.livesync/headless-vault-livesync-v2
node /root/repos/huible/scripts/livesync/bridge.mjs pull   # full re-pull + mirror
node /root/repos/huible/scripts/livesync/huible-livesync validate
systemctl start huible-livesync
```

## Known limitations

- **A handful of legacy files** (e.g. `scripts/livesync-safe.sh`,
  `scripts/vault-access.py`) trip the CLI's corruption check during `mirror`
  (`a != b` where `a ≈ 0.55·b`). These are pre-existing stale-size entries in
  CouchDB; the files are already correct on the filesystem and the warning only
  *prevents overwriting* them, so it is non-fatal. Tracked for cleanup.
- **The CLI `sync`/`daemon` commands remain broken** in this build (config
  migration + milestone handshake loop). Do **not** use them; use this bridge.
  Upgrading the `self-hosted-livesync-cli` package may restore them.
- **Kestra file-watch triggers must not be trusted** until `huible-livesync
  validate` passes (per the onboarding spec §1). Trigger onboarding from
  Paperclip issue assignment instead.
