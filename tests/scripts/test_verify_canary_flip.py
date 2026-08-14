"""Regression gate for ``scripts/verify_canary_flip.py`` (HU-1436 rollout plan §4).

This harness IS the live real-user path: it boots the FastAPI app in the
PM-ratified Stage-1 canary config and exercises every §4 acceptance item
(go-live turn, §7.4 guardrails, /metrics, kill-switch drill, paging drill)
against the deterministic FakeLLMClient. The PM posts its transcript as the
go-live evidence record, so a silent breakage here would invalidate the
launch gate.

The test simply runs the harness end-to-end and asserts ``main()`` returns
0 — i.e. every §4 check still passes at the current HEAD. No network, no
secrets, no external services.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "verify_canary_flip.py"


def _load_main():
    spec = importlib.util.spec_from_file_location("_canary_flip_harness", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.main


def test_canary_flip_harness_passes(capsys):
    main = _load_main()
    rc = main()
    captured = capsys.readouterr()
    assert rc == 0, f"canary-flip harness failed (rc={rc}):\n{captured.out}"
    assert "ALL §4 CHECKS PASS" in captured.out
