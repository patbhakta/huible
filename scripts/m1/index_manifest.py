#!/usr/bin/env python3
"""M1.2 — index-manifest.json: prove the full corpus is really indexed (HU-2732).

Answers the ``m0_fake_embeddings`` acceptance ("real embeddings, model/version
+ source refs, over the full corpus") against the LIVE deployment on .245:

* provider + model + library version actually running in the app container;
* per-column vector dims in the pgvector schema vs the provider contract;
* embedding coverage over every stored memory (the "full corpus" claim);
* corpus source refs (CSV sha256 + row/line counts) and repo revision.

Exit semantics: exit 0 writes the manifest AND asserts the M1.2 index
contract (``local_onnx`` provider, 100% coverage, dims == provider dim).
Exit 2 means the contract is violated or the deployment is unreachable —
the manifest is then written with ``"contract_satisfied": false`` so the
failure is inspectable, never silent.

Usage:
    python3 -m scripts.m1.index_manifest
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from huible.embeddings import DEFAULT_EMBEDDINGS_MODEL, provider_dim  # noqa: E402
from scripts.m1.replay_m0_baseline import (  # noqa: E402
    APP_CONTAINER,
    OUT_DIR,
    RunnerError,
    _docker_env,
    _psql,
    measure_corpus,
    measure_git,
)

MANIFEST_PATH = OUT_DIR / "index-manifest.json"


def measure_index() -> dict:
    env = _docker_env()
    provider = env.get("EMBEDDING_PROVIDER") or ""
    model = env.get("EMBEDDINGS_MODEL") or DEFAULT_EMBEDDINGS_MODEL
    try:
        fastembed_version = subprocess.run(
            [
                "docker",
                "exec",
                APP_CONTAINER,
                "python3",
                "-c",
                "import fastembed,sys;sys.stdout.write(fastembed.__version__)",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout.strip()
    except subprocess.SubprocessError:
        fastembed_version = None

    coverage = _psql(
        "SELECT count(*), count(embedding_content), "
        "count(*) - count(embedding_content) FROM memories"
    )[0]
    dims_content = _psql(
        "SELECT coalesce(max(vector_dims(embedding_content)), -1), "
        "count(embedding_content) FROM memories"
    )
    dims_sensory = _psql(
        "SELECT coalesce(max(vector_dims(embedding_sensory)), -1), "
        "count(embedding_sensory) FROM memories"
    )
    dims_affect = _psql(
        "SELECT coalesce(max(vector_dims(embedding_affect)), -1), "
        "count(embedding_affect) FROM memories"
    )
    freshness = _psql(
        "SELECT to_char(max(created_at),'YYYY-MM-DD\"T\"HH24:MI:SSZ'), "
        "count(DISTINCT persona_id) FROM memories"
    )[0]
    per_persona = _psql(
        "SELECT persona_id, count(*), count(embedding_content) "
        "FROM memories GROUP BY 1 ORDER BY 2 DESC"
    )

    return {
        "provider": provider,
        "model": model,
        "model_source": "container env EMBEDDINGS_MODEL (settings.embeddings_model)",
        "fastembed_version": fastembed_version or None,
        "expected_provider_dim": provider_dim(provider) if provider else None,
        "vector_dims_observed": {
            "embedding_content": int(dims_content[0][0]) if dims_content else None,
            "embedding_sensory": int(dims_sensory[0][0]) if dims_sensory else None,
            "embedding_affect": int(dims_affect[0][0]) if dims_affect else None,
        },
        # Rows populated per vector column. The dim contract (HU-1435) binds
        # columns IN USE; a fully empty column is a documented schema state
        # (sensory vectors are not produced by the current pipeline), reported
        # here rather than silently assumed.
        "vector_columns_populated": {
            "embedding_content": int(dims_content[0][1]) if dims_content else 0,
            "embedding_sensory": int(dims_sensory[0][1]) if dims_sensory else 0,
            "embedding_affect": int(dims_affect[0][1]) if dims_affect else 0,
        },
        "memories_total": int(coverage[0]),
        "memories_embedded": int(coverage[1]),
        "memories_missing_embedding": int(coverage[2]),
        "coverage_fraction": (
            round(int(coverage[1]) / int(coverage[0]), 6) if int(coverage[0]) else 0.0
        ),
        "personas_indexed": {
            pid: {"memories": int(total), "embedded": int(embedded)}
            for pid, total, embedded in per_persona
        },
        "index_freshness": {
            "newest_memory_created_at": freshness[0],
            "persona_count": int(freshness[1]),
        },
    }


def main() -> int:
    try:
        git = measure_git()
        index = measure_index()
        corpus = measure_corpus()
    except RunnerError as exc:
        print(f"INDEX MANIFEST ERROR: {exc}", file=sys.stderr)
        return 2

    violations: list[str] = []
    if index["provider"] != "local_onnx":
        violations.append(
            f"EMBEDDING_PROVIDER={index['provider']!r} is not 'local_onnx' — "
            "the vault is not semantically read (m0_fake_embeddings class)"
        )
    if index["memories_missing_embedding"] != 0:
        violations.append(
            f"{index['memories_missing_embedding']} memories have no content "
            "embedding — the 'full corpus' claim fails"
        )
    # The live chat retrieval path (app.py → ContextBuilder.build) passes ONLY
    # a content query embedding — so the binding contract column is
    # embedding_content. Other vector columns are contract-checked only when
    # populated AND dim-mismatched rows would silently break future queries;
    # a mismatch there is recorded as an advisory finding, not a violation.
    content_dim = index["vector_dims_observed"]["embedding_content"]
    if index["vector_columns_populated"]["embedding_content"] == 0:
        violations.append("embedding_content is empty — no memory is semantically retrievable")
    elif content_dim != index["expected_provider_dim"]:
        violations.append(
            f"embedding_content dim {content_dim} != provider dim "
            f"{index['expected_provider_dim']} (HU-1435 dim contract)"
        )
    advisory_findings: list[str] = []
    for column in ("embedding_sensory", "embedding_affect"):
        populated = index["vector_columns_populated"][column]
        observed = index["vector_dims_observed"][column]
        if populated == 0:
            advisory_findings.append(
                f"{column}: 0 rows populated (unused by the current pipeline; "
                "dim recorded as observed, not assumed)"
            )
        elif observed != index["expected_provider_dim"]:
            advisory_findings.append(
                f"{column}: {populated} rows at dim {observed} != provider dim "
                f"{index['expected_provider_dim']} — not on the live chat "
                "retrieval path (content-only); the HU-1435 dim-skip guard "
                "drops mismatched vectors if ever queried"
            )

    manifest = {
        "artifact": "M1.2 index manifest — full-corpus real-embedding proof (HU-2732)",
        "generated_at": datetime.now(UTC).isoformat(),
        "repo": str(REPO_ROOT),
        "git": git,
        "index": index,
        "corpus_source": corpus,
        "contract": {
            "provider": "local_onnx",
            "model": index["model"],
            "dim": index["expected_provider_dim"],
            "coverage": "100% of stored memories carry a content embedding",
        },
        "contract_satisfied": not violations,
        "violations": violations,
        "advisory_findings": advisory_findings,
        "accepts_m0_cases": ["m0_fake_embeddings (index half; trace half in m12-traces)"],
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n")

    for finding in advisory_findings:
        print(f"INDEX ADVISORY: {finding}")
    for violation in violations:
        print(f"INDEX CONTRACT VIOLATION: {violation}", file=sys.stderr)
    label = "SATISFIED" if not violations else "VIOLATED"
    print(
        f"INDEX MANIFEST: contract {label} — wrote {MANIFEST_PATH.relative_to(REPO_ROOT)} "
        f"(provider={index['provider']} model={index['model']} "
        f"coverage={index['memories_embedded']}/{index['memories_total']} "
        f"dims={index['vector_dims_observed']})"
    )
    return 0 if not violations else 2


if __name__ == "__main__":
    raise SystemExit(main())
