#!/usr/bin/env python3
"""M1.1 — capture the failing user path + executable baseline (HU-2732).

Replay runner for the M-0 founder-failure cases (``docs/evidence/m1/m0-cases.jsonl``).
It MEASURES the live deployment in place on .245 and emits two machine-readable
artifacts:

* ``baseline.json``     — deployed revision, configuration, corpus inventory,
                          and measured production workload.
* ``replay-results.json`` — one assertion per m0-cases.jsonl entry with the
                          observed evidence and what M1.1 actually establishes.

Exit semantics (roadmap acceptance): the runner exits 0 when it ran to
completion — RED product assertions are expected at M1.1 and are DATA here,
never reported as passing. Exit 2 means the runner itself could not execute
(missing deployment, database, or corpus) and produced no valid baseline.

No case in replay-results.json ever carries status ``pass``: live-replay
classes are ``deferred`` to the spend-gated v2 harness probes (H1/H2/H3) or a
later milestone; probe classes are ``observed`` with the raw value recorded.
Config-level observations (e.g. ``EMBEDDING_PROVIDER=local_onnx``) do NOT
prove product behavior — that proof belongs to M1.2+ live-path traces.

Usage:
    python3 -m scripts.m1.replay_m0_baseline
"""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.v2_harness.common import (  # noqa: E402
    CORPUS_CSV,
    measure_corpus_baselines,
)

CASES_PATH = REPO_ROOT / "docs/evidence/m1/m0-cases.jsonl"
OUT_DIR = REPO_ROOT / "docs/evidence/m1"
APP_BASE_URL = "http://127.0.0.1:8000"
WORKING_MEMORY_RELAY_URL = "http://172.19.0.1:8420/health"
APP_CONTAINER = "huible-app"
DB_CONTAINER = "huible-postgres"


class RunnerError(RuntimeError):
    """Runner could not execute (infra); no valid baseline produced."""


def _sh(cmd: list[str]) -> str:
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RunnerError(f"{' '.join(cmd)} failed: {out.stderr.strip()[:300]}")
    return out.stdout.strip()


def _psql(sql: str) -> list[list[str]]:
    raw = _sh(
        [
            "docker",
            "exec",
            DB_CONTAINER,
            "psql",
            "-U",
            "huible",
            "-d",
            "huible",
            "-tAF",
            "\t",
            "-c",
            sql,
        ]
    )
    return [line.split("\t") for line in raw.splitlines() if line.strip()]


def _http_get(url: str, timeout: float = 8.0) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")[:600]
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RunnerError(f"GET {url} failed: {exc}") from exc


def _docker_env() -> dict[str, str]:
    raw = _sh(["docker", "exec", APP_CONTAINER, "env"])
    env: dict[str, str] = {}
    for line in raw.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            env[key] = value
    return env


def measure_git() -> dict:
    head = _sh(["git", "rev-parse", "HEAD"])
    branch = _sh(["git", "branch", "--show-current"])
    remote = subprocess.run(
        ["git", "ls-remote", "--heads", "origin", branch],
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout.split()
    behind_ahead = _sh(["git", "rev-list", "--left-right", "--count", "origin/main...HEAD"]).split()
    dirty = sum(
        1
        for line in _sh(["git", "status", "--porcelain"]).splitlines()
        if line.strip() and not line.startswith("??")
    )
    return {
        "repo": str(REPO_ROOT),
        "head": head,
        "branch": branch,
        "pushed": bool(remote) and remote[0] == head,
        "remote_branch_sha": remote[0] if remote else None,
        "behind_origin_main": int(behind_ahead[0]),
        "ahead_of_origin_main": int(behind_ahead[1]),
        "tracked_dirty_files": dirty,
        "origin_main_head": _sh(["git", "rev-parse", "origin/main"]),
    }


def measure_deployment() -> dict:
    env = _docker_env()
    status, health = _http_get(APP_BASE_URL + "/health")
    relay_status, _relay_body = _http_get(WORKING_MEMORY_RELAY_URL)
    relay_unit = _sh(["systemctl", "is-active", "huible-tdai-relay"])
    return {
        "host": ".245 (this host; .243 off-limits per roadmap)",
        "app_container": APP_CONTAINER,
        "app_image_created": _sh(
            ["docker", "image", "inspect", APP_CONTAINER, "--format", "{{.Created}}"]
        ),
        "app_container_started": _sh(
            ["docker", "container", "inspect", APP_CONTAINER, "--format", "{{.State.StartedAt}}"]
        ),
        "app_health": {"http": status, "body": health},
        "config_observed": {
            "EMBEDDING_PROVIDER": env.get("EMBEDDING_PROVIDER"),
            "EMBEDDING_MODEL_container_env": env.get("EMBEDDING_MODEL"),
            "WORKING_MEMORY_ENABLED": env.get("WORKING_MEMORY_ENABLED"),
            "WORKING_MEMORY_BASE_URL": env.get("WORKING_MEMORY_BASE_URL"),
            "WORKING_MEMORY_SERVICE_ID": env.get("WORKING_MEMORY_SERVICE_ID"),
            "note": (
                "EMBEDDING_MODEL is a legacy unused var name; the engine reads "
                "EMBEDDINGS_MODEL (settings.embeddings_model, default "
                "BAAI/bge-small-en-v1.5). Recorded as observed config drift."
            ),
        },
        "working_memory_relay": {
            "systemd_unit": relay_unit,
            "http": relay_status,
            "bind_expectation": "172.19.0.1:8420 bridge-only (runbook)",
            "runbook": "docs/runbooks/working-memory-relay.md",
        },
    }


def measure_database() -> dict:
    coverage = _psql(
        "SELECT count(*), count(embedding_content), "
        "count(*) - count(embedding_content) FROM memories"
    )[0]
    per_persona = _psql("SELECT persona_id, count(*) FROM memories GROUP BY 1 ORDER BY 2 DESC")
    dims = _psql(
        "SELECT attname, typname FROM pg_attribute a JOIN pg_type t ON t.oid=a.atttypid "
        "WHERE a.attrelid='memories'::regclass AND t.typname='vector'"
    )
    turns_total = _psql("SELECT count(*), count(DISTINCT conversation_id) FROM conversation_turns")[
        0
    ]
    by_day = _psql(
        "SELECT to_char(date(created_at),'YYYY-MM-DD'), count(*) FROM conversation_turns "
        "WHERE created_at > now() - interval '14 days' GROUP BY 1 ORDER BY 1"
    )
    return {
        "memories_total": int(coverage[0]),
        "memories_with_content_embedding": int(coverage[1]),
        "memories_missing_content_embedding": int(coverage[2]),
        "memories_per_persona": {p: int(n) for p, n in per_persona},
        "vector_columns": {col: typ for col, typ in dims},
        "conversation_turns_lifetime": int(turns_total[0]),
        "conversations_lifetime": int(turns_total[1]),
        "turns_by_day_last_14d": {d: int(n) for d, n in by_day},
    }


def measure_corpus() -> dict:
    raw = CORPUS_CSV.read_bytes()
    with CORPUS_CSV.open(newline="") as fh:
        total_rows = sum(1 for _ in csv.DictReader(fh))
    return {
        "corpus_csv": str(CORPUS_CSV.relative_to(REPO_ROOT)),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "csv_rows_total": total_rows,
        "measured_baselines": measure_corpus_baselines(),
    }


def probe_code() -> dict:
    grep = subprocess.run(
        ["grep", "-rn", "character_sheet", "src/huible/", "--include=*.py"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    from huible.api.schemas import ChatTrace

    return {
        "character_sheet_refs_in_engine_src": grep.stdout.strip() or None,
        "chat_trace_has_trace_id": "trace_id" in ChatTrace.model_fields,
        "chat_route": "POST /api/v1/chat/{persona_id} (src/huible/api/app.py)",
    }


def run() -> tuple[int, dict, dict]:
    git = measure_git()
    deployment = measure_deployment()
    database = measure_database()
    corpus = measure_corpus()
    code = probe_code()

    baseline = {
        "artifact": "M1.1 baseline capture (HU-2732)",
        "generated_at": datetime.now(UTC).isoformat(),
        "convention": (
            "Measured in place on the live deployment; no invented values. "
            "Config observations are not product-behavior proof (M1.2+ owns that)."
        ),
        "git": git,
        "deployment": deployment,
        "database": database,
        "corpus": corpus,
        "code": code,
    }

    cases = [json.loads(line) for line in CASES_PATH.read_text().splitlines() if line.strip()]
    evidence = {
        "config": deployment["config_observed"],
        "database": database,
        "relay": deployment["working_memory_relay"],
        "code": code,
    }
    deferred_runners = {
        "m0_fullname_self_intro": "scripts/v2_harness/h1_m0_calibration.py",
        "m0_python_syntax_answer": "scripts/v2_harness/h1_m0_calibration.py",
        "m0_reply_length_violation": "scripts/v2_harness/h1_m0_calibration.py",
        "m0_assistant_register_drift": "scripts/v2_harness/h2_ai_tell_probes.py",
        "m0_one_way_conversation": "scripts/v2_harness/h2_ai_tell_probes.py",
        "m0_unproven_per_reply_grounding": "scripts/v2_harness/h3_grounding_ledger.py",
        "m0_character_sheet_prompting": "M1.2 (traces: vault reads reach generation)",
        "m0_fake_embeddings": "M1.2 (index-manifest.json + retrieval traces)",
        "m0_arm_a_not_ported": "M1.2 (Arm A reads visible in production traces)",
        "m0_no_tool_calls": "M1.4 (context-tool-replay.json)",
    }
    results = []
    for case in cases:
        cid = case["case_id"]
        results.append(
            {
                "case_id": cid,
                "defect_class": case["defect_class"],
                "sources": case["sources"],
                "expected_at_m1": deferred_runners.get(cid, "deferred"),
                "status": "deferred",
                "observed": evidence if case.get("attach_baseline_evidence") else None,
                "note": (
                    "M1.1 records the baseline state only; no product case is "
                    "reported passing. Live proof is owed by the runner/milestone above."
                ),
            }
        )

    replay = {
        "artifact": "M1.1 M-0 replay results (HU-2732)",
        "generated_at": baseline["generated_at"],
        "cases_path": str(CASES_PATH.relative_to(REPO_ROOT)),
        "case_count": len(results),
        "statuses": sorted({r["status"] for r in results}),
        "gate": (
            "Runner-level success only. Product verdicts stay with H1-H4 "
            "(spend-gated) and M1.2+ acceptance; red assertions expected at M1.1."
        ),
        "cases": results,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "baseline.json").write_text(json.dumps(baseline, indent=1) + "\n")
    (OUT_DIR / "replay-results.json").write_text(json.dumps(replay, indent=1) + "\n")
    return 0, baseline, replay


def main() -> int:
    try:
        code, _baseline, replay = run()
    except RunnerError as exc:
        print(f"M1.1 RUNNER ERROR: {exc}", file=sys.stderr)
        return 2
    print(
        f"M1.1 replay complete: {replay['case_count']} cases "
        f"(statuses={replay['statuses']}); baseline.json + replay-results.json "
        f"written to {OUT_DIR.relative_to(REPO_ROOT)}/"
    )
    return code


if __name__ == "__main__":
    sys.exit(main())
