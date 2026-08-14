"""Regression gate for ``scripts/verify_kill_switch_rollback.py`` (HU-1462 plan §4.3).

This harness proves the Stage-0.7 hard kill switch
(``PERSONA_CHAT_REAL_USER_TRAFFIC``) is the instant, verifiable rollback path
independent of key revocation. It is the MANDATORY pre-Stage-1-advance dry-run
whose transcript the operator attaches as rollback-dry-run evidence (plan §5).

The test runs the harness end-to-end and asserts ``main()`` returns 0 — i.e.
every §4.3 check still passes at the current HEAD. No network, no secrets, no
external services.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "verify_kill_switch_rollback.py"


def _load_main():
    spec = importlib.util.spec_from_file_location("_kill_switch_harness", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.main


def test_kill_switch_rollback_harness_passes(capsys):
    main = _load_main()
    rc = main()
    captured = capsys.readouterr()
    assert rc == 0, f"kill-switch rollback harness failed (rc={rc}):\n{captured.out}"
    assert "ALL §4.3 CHECKS PASS" in captured.out
