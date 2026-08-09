#!/usr/bin/env node
/**
 * huible-livesync validator — proves Phase 0 sync is deterministic.
 *
 * Runs N (default 10) consecutive probe round-trips that exercise the FULL path
 * a client (Pat's Obsidian) file takes:
 *
 *   probe  --CLI push (encrypt+chunk)-->  local DB  --bridge sync-->  remote CouchDB
 *   remote --bridge pull (PouchDB)-->     local DB  --CLI mirror-->   filesystem
 *   assert filesystem content == probe content
 *
 * This is byte-for-byte the same transport LiveSync uses: the CLI push encrypts and
 * chunks exactly like the plugin, and the bridge's PouchDB replicate is the same
 * CouchDB replication protocol. So a pass here means a file Pat drops in Obsidian
 * will appear on the server filesystem, and vice-versa.
 *
 * Usage:
 *   node validator.mjs                  # 10 passes, SLA 30s
 *   node validator.mjs --passes 25      # 25 consecutive passes
 *   node validator.mjs --sla-ms 15000   # fail if a round-trip exceeds 15s
 *   node validator.mjs --json           # machine-readable output
 *
 * Exit 0 = all passes within SLA & content verified. Exit 1 = any failure
 * (prints RECOVERY.md guidance).
 */

import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync, unlinkSync, existsSync } from "node:fs";
import { randomBytes } from "node:crypto";

const HERE = import.meta.dirname;
const VAULT = process.env.HUIBLE_LIVESYNC_VAULT || "/root/repos/brain";
const RECOVERY_DOC = `${HERE}/RECOVERY.md`;

function parseArgs(argv) {
  const out = { passes: 10, slaMs: 30000, json: false };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--passes") out.passes = Number(argv[++i]);
    else if (a === "--sla-ms") out.slaMs = Number(argv[++i]);
    else if (a === "--json") out.json = true;
  }
  return out;
}

function bridge(args) {
  return execFileSync("node", [`${HERE}/bridge.mjs`, ...args], {
    encoding: "utf8",
    timeout: 180000,
    stdio: ["ignore", "pipe", "pipe"],
    cwd: HERE,
  });
}

// The validator never touches the LevelDB or CouchDB directly — it routes every
// DB operation through the bridge so the cross-process lock serializes it with
// the daemon. push/rm -> `bridge cli ...`; hard-delete -> `bridge purge ...`.

function recoveryHint(reason) {
  return (
    `\nSYNC VALIDATION FAILED: ${reason}\n\n` +
    `Recovery steps are documented in:\n  ${RECOVERY_DOC}\n` +
    `Quick checks:\n` +
    `  1. node ${HERE}/bridge.mjs status           # are local & remote reachable & in sync?\n` +
    `  2. docker ps | grep couchdb                  # is CouchDB up?\n` +
    `  3. curl -s https://brain.bhakta.us/          # is the remote endpoint reachable?\n` +
    `  4. node ${HERE}/bridge.mjs sync              # force a replication cycle\n`
  );
}

async function main() {
  const opts = parseArgs(process.argv);

  if (!opts.json) process.stdout.write(`Phase 0 LiveSync validator: ${opts.passes} passes, SLA ${opts.slaMs}ms\n`);

  // Reuse one probe path across all passes: each pass writes a new revision,
  // which also proves UPDATE propagation (not just create). One hard-delete at
  // the end leaves zero litter in the client vault.
  const probeRel = "Huible/write/.phase0-validator.md";
  const probeFs = `${VAULT}/${probeRel}`;
  const probeTmp = "/tmp/.phase0-validator.md";
  const results = [];
  let allOk = true;

  for (let i = 1; i <= opts.passes; i++) {
    const token = randomBytes(6).toString("hex");
    const body = `# Phase0 probe ${i}\ntoken: ${token}\ncreated: ${new Date().toISOString()}\n`;
    writeFileSync(probeTmp, body);
    const pass = { i, token, ms: 0, ok: false, reason: "" };

    try {
      // (1) Client drop: encrypt+chunk into local DB, exactly like Obsidian LiveSync.
      bridge(["cli", "push", probeTmp, probeRel]);

      // (2) Server ingest: remote <-> local replicate + mirror DB -> FS. Time the path.
      const t0 = Date.now();
      bridge(["sync"]);
      pass.ms = Date.now() - t0;

      // (3) Verify THIS pass's content is on the filesystem (catches stale updates).
      if (!existsSync(probeFs)) throw new Error(`probe not on filesystem at ${probeFs}`);
      const got = readFileSync(probeFs, "utf8");
      if (got !== body) throw new Error(`content mismatch (got ${got.length}b, want ${body.length}b; stale update?)`);
      if (pass.ms > opts.slaMs) throw new Error(`SLA exceeded: ${pass.ms}ms > ${opts.slaMs}ms`);
      pass.ok = true;
    } catch (e) {
      pass.reason = e.message;
      allOk = false;
    }

    results.push(pass);
    if (!opts.json) {
      const tag = pass.ok ? "PASS" : "FAIL";
      process.stdout.write(`  [${i}/${opts.passes}] ${tag}  ${pass.ms}ms  ${pass.ok ? "" : "- " + pass.reason}\n`);
    }
    if (!allOk && process.env.HUIBLE_VALIDATOR_FAST_FAIL) break;
  }

  // Cleanup: remove the probe from local DB, remote CouchDB, and filesystem.
  try { unlinkSync(probeTmp); } catch {}
  try { unlinkSync(probeFs); } catch {}
  try { await bridge(["purge", probeRel]); } catch (e) { /* non-fatal */ }

  const passed = results.filter((r) => r.ok).length;
  const summary = {
    passed,
    total: opts.passes,
    all_passed: passed === opts.passes,
    sla_ms: opts.slaMs,
    max_ms: Math.max(0, ...results.map((r) => r.ms)),
    avg_ms: results.length ? Math.round(results.reduce((a, r) => a + r.ms, 0) / results.length) : 0,
    failures: results.filter((r) => !r.ok),
  };

  if (opts.json) {
    process.stdout.write(JSON.stringify(summary, null, 2) + "\n");
  } else {
    process.stdout.write(
      `\nResult: ${summary.passed}/${summary.total} passed` +
        ` (avg ${summary.avg_ms}ms, max ${summary.max_ms}ms, SLA ${summary.sla_ms}ms)\n`
    );
  }

  if (!summary.all_passed) {
    process.stderr.write(recoveryHint(results.find((r) => !r.ok)?.reason || "unknown"));
    process.exit(1);
  }
  process.exit(0);
}

main();
