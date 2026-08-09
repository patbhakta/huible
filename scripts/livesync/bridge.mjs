#!/usr/bin/env node
/**
 * huible-livesync bridge — bulletproof Obsidian LiveSync (CouchDB) <-> filesystem.
 *
 * Phase 0 fix: the bundled `self-hosted-livesync` CLI's `sync` command is broken
 * (its LiveSync replication wrapper returns false after the milestone handshake,
 * see notes/RECOVERY.md). This bridge replaces ONLY the broken transport layer
 * with battle-tested PouchDB <-> CouchDB replication. The CLI is still used for
 * what it does correctly: encrypt/chunk (push/rm) and decrypt/assemble (mirror).
 *
 * Data flow:
 *   remote CouchDB  <--PouchDB replicate-->  local LevelDB  <--CLI mirror/push-->  filesystem
 *
 * Config is read from the same LiveSync settings.json the CLI uses, so credentials
 * are never duplicated here. Override any field with the HUIBLE_LIVESYNC_* env vars
 * documented below.
 *
 * Commands:
 *   sync      One bidirectional replication cycle (remote <-> local), then mirror DB -> FS.
 *   pull      One remote -> local replication cycle, then mirror DB -> FS.
 *   push      Replicate local -> remote (flush agent write-back to CouchDB).
 *   mirror    CLI mirror only (DB -> FS). No network.
 *   daemon    Loop: replicate + mirror every $INTERVAL seconds (default 5).
 *   status    Print local/remote doc counts and last-replicated sequence.
 *
 * Env:
 *   HUIBLE_LIVESYNC_SETTINGS   settings.json path (default /root/repos/brain/.livesync/.livesync/settings.json)
 *   HUIBLE_LIVESYNC_DB_PREFIX  local LevelDB parent dir (default /root/repos/brain/.livesync/)
 *   HUIBLE_LIVESYNC_DB_NAME    local LevelDB name (default headless-vault-livesync-v2)
 *   HUIBLE_LIVESYNC_VAULT      vault root mirrored to FS (default /root/repos/brain)
 *   LIVESYNC_CLI               path to the livesync CLI dist (default see CLI_DEFAULT)
 *   INTERVAL                   daemon poll seconds (default 5)
 *   HUIBLE_LIVESYNC_BATCH      replication batch_size (default 200)
 */

import PouchDB from "pouchdb";
import Leveldb from "pouchdb-adapter-leveldb";
import Http from "pouchdb-adapter-http";
import { readFileSync, existsSync, openSync, closeSync, unlinkSync, statSync, writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";

PouchDB.plugin(Leveldb).plugin(Http);

const CLI_DEFAULT = "/root/repos/livesync-cli-build/src/apps/cli/dist/index.cjs";
const SETTINGS = process.env.HUIBLE_LIVESYNC_SETTINGS || "/root/repos/brain/.livesync/.livesync/settings.json";
const DB_PREFIX = process.env.HUIBLE_LIVESYNC_DB_PREFIX || "/root/repos/brain/.livesync/";
const DB_NAME = process.env.HUIBLE_LIVESYNC_DB_NAME || "headless-vault-livesync-v2";
const VAULT = process.env.HUIBLE_LIVESYNC_VAULT || "/root/repos/brain";
const CLI = process.env.LIVESYNC_CLI || CLI_DEFAULT;
const INTERVAL = Number(process.env.INTERVAL || 5);
const BATCH = Number(process.env.HUIBLE_LIVESYNC_BATCH || 200);
// Cross-process lock: both PouchDB and the CLI open the same LevelDB, which allows
// only one opener. Every operation below that touches the local DB acquires this
// lock so the daemon and any concurrent manual/validator command serialize instead
// of crashing on `LOCK: Resource temporarily unavailable`. Implemented with a
// PID-stamped O_EXCL lockfile (dependency-free) with staleness reclaim.
const LOCK_FILE = `${DB_PREFIX.replace(/\/$/, "")}/${DB_NAME}.bridge-lock`;
const LOCK_STALE_MS = 120000;

function log(level, msg) {
  const ts = new Date().toISOString();
  process.stderr.write(`[${ts}] [${level}] ${msg}\n`);
}
const info = (m) => log("INFO", m);
const warn = (m) => log("WARN", m);
const err = (m) => log("ERROR", m);

function readSettings() {
  if (!existsSync(SETTINGS)) throw new Error(`settings not found: ${SETTINGS}`);
  return JSON.parse(readFileSync(SETTINGS, "utf8"));
}

function remoteUrl(s) {
  const uri = String(s.couchDB_URI || "").replace(/\/+$/, "");
  const db = s.couchDB_DBNAME;
  const user = encodeURIComponent(s.couchDB_USER || "");
  const pass = encodeURIComponent(s.couchDB_PASSWORD || "");
  if (!uri || !db) throw new Error("couchDB_URI / couchDB_DBNAME missing from settings");
  return `${uri.replace("://", `://${user}:${pass}@`)}/${db}`;
}

function openDBs() {
  const s = readSettings();
  const local = new PouchDB(DB_NAME, { adapter: "leveldb", prefix: DB_PREFIX });
  const remote = new PouchDB(remoteUrl(s), { adapter: "http" });
  return { local, remote, settings: s };
}

async function retry(label, fn, attempts = 4, backoffMs = 800) {
  let lastErr;
  for (let i = 1; i <= attempts; i++) {
    try {
      return await fn();
    } catch (e) {
      lastErr = e;
      if (i < attempts) {
        warn(`${label} failed (attempt ${i}/${attempts}): ${e.message}; retrying in ${backoffMs}ms`);
        await new Promise((r) => setTimeout(r, backoffMs));
        backoffMs *= 2;
      }
    }
  }
  throw lastErr;
}

async function replicate(local, remote, direction) {
  // direction: "both" | "from" | "to"
  const opts = { batch_size: BATCH, batches_limit: 10 };
  const out = { pulled: 0, pushed: 0, errors: [] };
  const doFrom = async () => {
    const r = await local.replicate.from(remote, opts);
    out.pulled = r.docs_written || 0;
    if (r.errors && r.errors.length) out.errors.push(...r.errors);
    return r;
  };
  const doTo = async () => {
    const r = await local.replicate.to(remote, opts);
    out.pushed = r.docs_written || 0;
    if (r.errors && r.errors.length) out.errors.push(...r.errors);
    return r;
  };
  if (direction === "from") await retry("replicate.from", doFrom);
  else if (direction === "to") await retry("replicate.to", doTo);
  else {
    await retry("replicate.from", doFrom);
    await retry("replicate.to", doTo);
  }
  return out;
}

function runCli(...args) {
  // Mirror/push/rm go through the CLI (it owns encrypt+chunk+decrypt+assemble).
  return execFileSync("node", [CLI, DB_PREFIX.replace(/\/$/, ""), "--vault", VAULT, ...args], {
    encoding: "utf8",
    timeout: 180000,
    stdio: ["ignore", "pipe", "pipe"],
  });
}

function mirrorToFS() {
  const out = runCli("mirror");
  const completed = (out.match(/Synchronisation completed: (\d+)\/(\d+)/) || [])[0];
  return completed || "mirror ran (summary not parsed)";
}

async function cmdStatus() {
  const { local, remote } = await openDBs();
  const [li, ri] = await Promise.all([local.info(), remote.info()]);
  process.stdout.write(
    JSON.stringify(
      {
        local: { name: li.db_name, doc_count: li.doc_count, update_seq: li.update_seq },
        remote: { name: ri.db_name, doc_count: ri.doc_count, update_seq: ri.update_seq },
        in_sync: String(li.doc_count) === String(ri.doc_count),
        vault: VAULT,
      },
      null,
      2
    ) + "\n"
  );
  await local.close();
}

// Replicate only. Opens/closes the local LevelDB so the lock is released before
// the CLI (mirror/push) needs it. Returns the replication summary.
async function doReplicate(direction) {
  const { local, remote } = await openDBs();
  try {
    return await replicate(local, remote, direction);
  } finally {
    await local.close();
  }
}

// Serialize every local-DB-touching critical section across processes (daemon +
// manual sync/validate). Uses a PID-stamped O_EXCL lockfile with staleness
// reclaim, so a crashed holder's lock is taken over after LOCK_STALE_MS.
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function processAlive(pid) {
  try { process.kill(pid, 0); return true; } catch { return false; }
}
async function acquireLock(label) {
  const start = Date.now();
  for (;;) {
    try {
      const fd = openSync(LOCK_FILE, "wx"); // O_EXCL: fails if it exists
      writeFileSync(fd, String(process.pid));
      closeSync(fd);
      return;
    } catch (e) {
      if (e.code === "EEXIST") {
        // reclaim if stale (holder dead or lock older than LOCK_STALE_MS)
        try {
          const content = readFileSync(LOCK_FILE, "utf8").trim();
          const pid = Number(content);
          const mtime = statSync(LOCK_FILE).mtimeMs;
          const stale = (Date.now() - mtime > LOCK_STALE_MS) || (pid && !processAlive(pid));
          if (stale) { unlinkSync(LOCK_FILE); continue; }
        } catch { /* race: someone else removed it */ }
        if (Date.now() - start > 120000) throw new Error(`${label}: timed out acquiring DB lock ${LOCK_FILE}`);
        await sleep(250);
        continue;
      }
      throw e;
    }
  }
}
function releaseLock() {
  try { unlinkSync(LOCK_FILE); } catch {}
}
async function withLock(label, fn) {
  await acquireLock(label);
  try {
    return await fn();
  } finally {
    releaseLock();
  }
}

async function cmdSync(direction) {
  return withLock("sync", async () => {
    const t0 = Date.now();
    const res = await doReplicate(direction);
    const summary = mirrorToFS(); // CLI opens the LevelDB itself — same lock keeps it serialized
    const dt = ((Date.now() - t0) / 1000).toFixed(1);
    info(`sync done in ${dt}s — pulled ${res.pulled}, pushed ${res.pushed}, errors ${res.errors.length}. ${summary}`);
    if (res.errors.length) {
      err(`${res.errors.length} replication errors; first: ${JSON.stringify(res.errors[0])}`);
      return 1;
    }
    return 0;
  });
}

// Run a raw livesync CLI command (push/rm/...) under the DB lock, so the daemon
// and the validator's push/rm never contend on the LevelDB.
async function cmdCli(cliArgs) {
  return withLock("cli", async () => runCli(...cliArgs));
}

// Hard-delete a vault path (file doc + its encrypted chunk children) from the
// local DB and remote CouchDB. LiveSync's `rm` only sets a soft `deleted` field
// that does not propagate as a real CouchDB deletion; this removes the docs for
// real so probes leave no litter in the client (Pat's) vault.
async function cmdPurge(relPath) {
  return withLock("purge", async () => {
    const prefix = DB_PREFIX.endsWith("/") ? DB_PREFIX : DB_PREFIX + "/";
    const local = new PouchDB(DB_NAME, { adapter: "leveldb", prefix });
    const remote = new PouchDB(remoteUrl(readSettings()), { adapter: "http" });
    try {
      const id = relPath.toLowerCase();
      const toDel = [];
      for (const db of [local, remote]) {
        try {
          const d = await db.get(id);
          toDel.push({ _id: d._id, _rev: d._rev, _deleted: true });
          for (const c of d.children || []) {
            try {
              const cd = await db.get(c);
              toDel.push({ _id: cd._id, _rev: cd._rev, _deleted: true });
            } catch {}
          }
        } catch {}
      }
      let n = 0;
      if (toDel.length) {
        await local.bulkDocs(toDel);
        const rr = await remote.bulkDocs(toDel);
        n = rr.filter((x) => x.ok).length;
      }
      info(`purge ${relPath}: removed ${n} docs (file + chunks) from local+remote`);
      return n;
    } finally {
      await local.close();
    }
  });
}

async function cmdDaemon() {
  info(`daemon started: replicate+mirror every ${INTERVAL}s (vault=${VAULT})`);
  let failures = 0;
  while (true) {
    const t0 = Date.now();
    try {
      await withLock("daemon", async () => {
        const res = await doReplicate("both");
        if (res.pulled || res.pushed) {
          info(`daemon cycle: pulled ${res.pulled}, pushed ${res.pushed}`);
          mirrorToFS();
        }
      });
      failures = 0;
    } catch (e) {
      failures++;
      err(`daemon cycle failed (#${failures}): ${e.message}`);
      if (failures >= 10) {
        err("daemon: 10 consecutive failures — emitting recovery guidance and exiting");
        process.exit(2);
      }
    }
    const elapsed = Date.now() - t0;
    const wait = Math.max(0, INTERVAL * 1000 - elapsed);
    await new Promise((r) => setTimeout(r, wait));
  }
}

async function main() {
  const cmd = process.argv[2];
  try {
    switch (cmd) {
      case "sync":
        return process.exit(await cmdSync("both"));
      case "pull":
        return process.exit(await cmdSync("from"));
      case "push":
        return process.exit(await cmdSync("to"));
      case "mirror":
        return process.exit(await withLock("mirror", async () => { info(mirrorToFS()); return 0; }));
      case "cli":
        return process.exit(await cmdCli(process.argv.slice(3)).then(() => 0));
      case "purge":
        if (!process.argv[3]) throw new Error("purge requires a vault-relative path");
        return process.exit(await cmdPurge(process.argv[3]).then(() => 0));
      case "status":
        return await cmdStatus();
      case "daemon":
        return await cmdDaemon();
      default:
        process.stdout.write(
          `usage: bridge.mjs <sync|pull|push|mirror|daemon|status>\n` +
            `Env: HUIBLE_LIVESYNC_SETTINGS, HUIBLE_LIVESYNC_VAULT, INTERVAL, ...\n`
        );
        return process.exit(cmd ? 2 : 0);
    }
  } catch (e) {
    err(`${cmd || "(no command)"} failed: ${e.message}`);
    if (process.env.DEBUG) console.error(e.stack);
    process.exit(1);
  }
}

main();
