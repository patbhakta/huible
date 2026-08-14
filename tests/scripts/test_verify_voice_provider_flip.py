"""Regression gate for ``scripts/verify_voice_provider_flip.py`` (HU-1461).

This harness proves the single proposition the PM needs staged for the
voice-axis activation flip: **activating the real persona-voice provider is a
pure environment flip, with no code change and no path to accidental keyless
real-user traffic.** It exercises the exact production wiring
(``Settings.to_llm_config()`` → ``build_llm_client()``) through seven postures
(default fake, explicit fake, unknown fallback, keyless safety guard, keyed
mock-hosted, settings bridge, one-knob rollback).

The test runs the harness end-to-end and asserts ``main()`` returns 0. No
network, no secrets, no real model — the OpenRouter posture uses
``httpx.MockTransport``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "verify_voice_provider_flip.py"


def _load_main():
    spec = importlib.util.spec_from_file_location("_voice_flip_harness", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.main


def test_voice_provider_flip_harness_passes(capsys):
    main = _load_main()
    rc = main()
    captured = capsys.readouterr()
    assert rc == 0, f"voice-provider-flip harness failed (rc={rc}):\n{captured.out}"
    assert "ALL POSTURES PASS" in captured.out
