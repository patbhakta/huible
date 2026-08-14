"""Tests for ``scripts/scrub_git_history.sh`` (HU-1503 defense-in-depth step).

This is the THIRD step of the HU-1501 recovery trio — it purges the exposed
CouchDB admin literal from public git history once HU-1500 has rotated the live
credential. It is the most destructive tool in the trio (rewrites all refs), so
the properties that matter most are:

- **guard correctness**: refuses to do anything without a target literal, on a
  too-short near-wildcard literal, on a dirty tree, before rotation is
  confirmed, or under DRY_RUN;
- **rewrite correctness**: the primary ``git filter-repo`` path removes every
  occurrence and the post-check (HU-1503 acceptance #1) proves it;
- **secret safety**: the target literal is never printed — only its length and a
  sha256 prefix (so a transcript is safe to paste into HU-1503 as evidence).

The tests build throwaway git fixtures in ``tmp_path`` (no network, the real
repo is never touched) and exercise the script end-to-end via ``subprocess``.
``git filter-repo`` is the production tool (installed on the recovery host) and
is the path exercised here; the built-in ``filter-branch`` fallback is only
reachable when filter-repo is absent and is not covered in isolation.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "scrub_git_history.sh"

REPLACEMENT = "***REMOVED***"


def _make_repo(repo: Path, literal: str | None, *, dirty: bool = False) -> Path:
    """Init a throwaway git repo at ``repo`` with optional secret-bearing file.

    Two commits so history has depth. When ``dirty`` is True an uncommitted file
    is left in the working tree to trip the clean-tree guard.
    """
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@huible.local"], check=True
    )
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    (repo / "README.md").write_text("# project\n", encoding="utf-8")
    if literal is not None:
        secret = f"COUCH_ADMIN_PASS={literal}\nOTHER=keep\n"
        (repo / "config.env").write_text(secret, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "initial"], check=True)
    (repo / "doc.txt").write_text("no secrets here\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "add docs"], check=True)
    if dirty:
        (repo / "uncommitted.txt").write_text("pending change\n", encoding="utf-8")
    return repo


def _run(cwd: Path, script_env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Invoke the scrubber with a clean-ish env + the given overrides."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        **script_env,
    }
    return subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=120,
    )


def _history_has_literal(repo: Path, literal: str) -> bool:
    """Mirror the script's own detection: is the literal reachable in any ref?"""
    res = subprocess.run(
        ["git", "-C", str(repo), "log", "--all", "-p", "-S", literal, "--format="],
        capture_output=True,
        text=True,
        check=True,
    )
    return literal in res.stdout


# ─── Guard / preflight tests ─────────────────────────────────────────────────


def test_preflight_aborts_without_secret_literal(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "repo", "some-real-secret-1234")
    result = _run(repo, {})
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "SCRUB_FAILED" in combined
    assert "no target literal" in combined


def test_rejects_too_short_literal(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "repo", "some-real-secret-1234")
    result = _run(repo, {"SECRET_LITERAL": "short"})
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "SCRUB_FAILED" in combined
    assert "too short" in combined
    assert "refusing near-wildcard" in combined


def test_secret_literal_file_missing_fails(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "repo", "some-real-secret-1234")
    missing = tmp_path / "does-not-exist.txt"
    result = _run(repo, {"SECRET_LITERAL_FILE": str(missing)})
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "SCRUB_FAILED" in combined
    assert "not found" in combined


def test_clean_tree_guard_rejects_dirty_repo(tmp_path: Path) -> None:
    sentinel = "SENTINEL-SCRUB-dirtytree-9a3f"
    repo = _make_repo(tmp_path / "repo", sentinel, dirty=True)
    result = _run(repo, {"SECRET_LITERAL": sentinel, "ROTATION_CONFIRMED": "1"})
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "SCRUB_FAILED" in combined
    assert "uncommitted changes" in combined


# ─── Scope / non-destructive mode tests ──────────────────────────────────────


def test_history_clean_when_literal_absent(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "repo", literal=None)
    result = _run(repo, {"SECRET_LITERAL": "absent-secret-abcd1234"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RESULT: HISTORY_CLEAN" in result.stdout
    assert "nothing to scrub" in result.stdout


def test_plan_only_without_rotation_confirmed(tmp_path: Path) -> None:
    sentinel = "SENTINEL-SCRUB-planonly-7c1e"
    repo = _make_repo(tmp_path / "repo", sentinel)
    result = _run(repo, {"SECRET_LITERAL": sentinel})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RESULT: PLAN_ONLY" in result.stdout
    assert "Rotation not confirmed" in result.stdout
    # Critical: no rewrite happened — literal is still fully reachable.
    assert _history_has_literal(repo, sentinel)


def test_dry_run_overrides_rotation_confirmed(tmp_path: Path) -> None:
    sentinel = "SENTINEL-SCRUB-dryrun-22ad"
    repo = _make_repo(tmp_path / "repo", sentinel)
    result = _run(repo, {"SECRET_LITERAL": sentinel, "ROTATION_CONFIRMED": "1", "DRY_RUN": "1"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PLAN_ONLY (DRY_RUN)" in result.stdout
    # DRY_RUN must win — history is untouched.
    assert _history_has_literal(repo, sentinel)


# ─── Rewrite + post-check (HU-1503 acceptance #1) ────────────────────────────


def test_full_rewrite_removes_literal(tmp_path: Path) -> None:
    sentinel = "SENTINEL-SCRUB-rewrite-4c8e2a17"
    repo = _make_repo(tmp_path / "repo", sentinel)
    assert _history_has_literal(repo, sentinel)  # precondition

    result = _run(repo, {"SECRET_LITERAL": sentinel, "ROTATION_CONFIRMED": "1"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RESULT: REWRITTEN_LOCALLY" in result.stdout
    assert "Acceptance #1 SATISFIED" in result.stdout

    # Acceptance #1: literal gone from ALL refs after rewrite.
    assert not _history_has_literal(repo, sentinel)

    # The blob now carries the replacement token, not the literal.
    cfg = (repo / "config.env").read_text(encoding="utf-8")
    assert REPLACEMENT in cfg
    assert sentinel not in cfg
    # Unrelated content survives the rewrite.
    assert "OTHER=keep" in cfg

    # Safety backup bundle was written before the rewrite (reversible).
    bundles = list((repo / ".git" / "scrub-backups").glob("pre-scrub-*.bundle"))
    assert bundles, "expected a pre-scrub backup bundle"


# ─── Secret safety ───────────────────────────────────────────────────────────


def test_secret_safety_transcript_redacts_literal(tmp_path: Path) -> None:
    """The target literal must never appear in the script transcript."""
    sentinel = "SENTINEL-SCRUB-leakcheck-5b6d"
    repo = _make_repo(tmp_path / "repo", sentinel)
    # Plan-only path still runs detection over the literal without printing it.
    result = _run(repo, {"SECRET_LITERAL": sentinel})
    combined = result.stdout + result.stderr
    assert "RESULT: PLAN_ONLY" in combined  # reached the planning stage
    # The operator-facing fingerprint is length + sha256 prefix only.
    assert "sha256:" in combined
    assert sentinel not in combined, "secret literal leaked into transcript"
