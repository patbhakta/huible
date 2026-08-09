# Huible Phase 0 — Obsidian LiveSync bridge & validator

Bulletproof Obsidian LiveSync (CouchDB) ↔ filesystem sync for the Huible onboarding
pipeline. This is **Phase 0** — the gate every downstream onboarding stage depends on.

## Why this exists

The bundled `self-hosted-livesync` CLI's `sync` command is broken: its LiveSync
replication wrapper returns `false` immediately after the CouchDB milestone
handshake, so files Pat drops in Obsidian never reliably reach the server
filesystem. Rather than fight the bug, this bridge replaces only the broken
**transport** layer with standard PouchDB ↔ CouchDB replication and keeps the CLI
for what it does correctly (encrypt/chunk on write, decrypt/assemble on mirror).

## Quick start

```bash
cd scripts/livesync
npm install                       # one-time (pouchdb + leveldb/http adapters)

./huible-livesync status          # local vs remote doc counts
./huible-livesync sync            # replicate both ways + mirror to FS
./huible-livesync validate        # PROOF: 10 consecutive full round-trips
```

## Files

| File                         | Purpose                                                     |
| ---------------------------- | ----------------------------------------------------------- |
| `bridge.mjs`                 | PouchDB ↔ CouchDB replication + CLI mirror orchestrator     |
| `validator.mjs`              | Deterministic N-pass probe round-trip validator             |
| `huible-livesync`            | Single command entrypoint (sync/pull/push/mirror/daemon/…)  |
| `huible-livesync.service`    | systemd unit for the continuous sync daemon                 |
| `RECOVERY.md`                | Operator runbook: diagnostics, re-seed, known limitations   |

## Continuous sync (production)

```bash
cp scripts/livesync/huible-livesync.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now huible-livesync
journalctl -u huible-livesync -f
```

See `RECOVERY.md` for config, diagnostics, and recovery procedures.
