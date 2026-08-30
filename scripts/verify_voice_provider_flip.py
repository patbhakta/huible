#!/usr/bin/env python3
"""Voice-axis provider-flip rehearsal for HU-1461 (PM partial unblock on HU-1434).

This is the reproducible operator runbook for the **voice-axis activation
flip** the PM authorized as a zero-risk partial unblock while the board
spend approval (74a0ff8b) and the VPS power-on (HU-1501) are pending. It
proves the single proposition the PM needs staged: **activating the real
persona-voice provider is a pure environment flip, with no code change and
no path to accidental keyless real-user traffic.**

It exercises the exact wiring the live app uses (`app.py:503`):
``build_llm_client(resolved_settings.to_llm_config())``. Every posture below
is driven through ``Settings`` → ``to_llm_config()`` → ``build_llm_client()``,
so a pass is evidence about the production activation path, not a synthetic
shortcut. The OpenRouter posture uses an ``httpx.MockTransport`` so the real
``/chat/completions`` request/response shape is exercised with zero network
and zero spend — the hosted path is fully wired, just env-gated.

Postures checked:

  (A) default            — no LLM env → FakeLLMClient, deterministic persona
                           digest (placeholder voice; the current live posture)
  (B) explicit fake      — LLM_PROVIDER=fake → FakeLLMClient
  (C) unknown provider   — LLM_PROVIDER=<garbage> → FakeLLMClient (a
                           misconfiguration can never silently wire a hosted
                           endpoint)
  (D) openrouter, no key — LLM_PROVIDER=openrouter WITHOUT OPENROUTER_API_KEY
                           → LLMConfigError at construction (the safety
                           property: no accidental keyless real-user traffic)
  (E) openrouter, keyed  — LLM_PROVIDER=openrouter + key + mock transport →
                           persona voice served through the real
                           /chat/completions request shape (the activation
                           flip; the only delta from (A) is two env vars)
  (F) settings bridge    — Settings.to_llm_config() maps env → config for the
                           openrouter posture (proves app.py:503 passes
                           provider + key through unchanged)
  (G) rollback           — flip LLM_PROVIDER back to fake → FakeLLMClient
                           (one-knob rollback)

Key-free and deterministic: no secrets, no network, no real model. Re-runnable
post-approval as the activation sanity check (point it at the provisioned key).

Run:    python3 scripts/verify_voice_provider_flip.py
Exit:   0 when every posture passes, 1 otherwise.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

import httpx

from huible.api.settings import Settings
from huible.llm.client import (
    LLMBudgetExceededError,
    LLMConfigError,
    LLMProvider,
    build_llm_client,
)

# The persona-voiced reply the OpenRouter mock returns — what the hosted path
# would surface once a real key is provisioned. Deterministic for the rehearsal.
OPENROUTER_PERSONA_REPLY = "Oh, the lake. Best mornings of my life."
OPENROUTER_API_KEY = "or-rehearsal-key"  # placeholder; never a real secret


# ── Transcript helpers (mirror verify_canary_flip.py) ────────────────────────


class Transcript:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.failures: list[str] = []

    def add(self, *xs: str) -> None:
        for x in xs:
            self.lines.extend(x.split("\n"))

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        mark = "PASS" if ok else "FAIL"
        self.add(f"  [{mark}] {name}{(' — ' + detail) if detail else ''}")
        if not ok:
            self.failures.append(name)

    def section(self, title: str) -> None:
        self.add("", title)

    def dump(self) -> str:
        return "\n".join(self.lines) + "\n"


def _openrouter_mock_transport() -> httpx.MockTransport:
    """MockTransport returning a chat completion with the persona reply."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-rehearsal",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": OPENROUTER_PERSONA_REPLY},
                    }
                ],
            },
        )

    return httpx.MockTransport(handler)


def _provider_label(client: Any) -> str:
    return getattr(client, "provider", type(client).__name__)


# ── Postures ─────────────────────────────────────────────────────────────────


def posture_a_default_fake(t: Transcript) -> None:
    t.section("(A) Default — no LLM env → FakeLLMClient (placeholder voice)")
    # The default posture must be evaluated free of ambient configuration:
    # a staged .env (e.g. LLM_PROVIDER=zai on the prod box) or exported LLM_*
    # vars would otherwise turn this into a re-test of the live provider
    # posture instead of the key-free default the check exists to prove.
    # _env_file=None disables the .env load; scrubbed vars isolate the process
    # env (restored in the finally so later postures see the real posture).
    _llm_env_keys = [
        k for k in os.environ if k.startswith(("LLM_", "OPENROUTER_", "ZAI_", "GENERATOR_"))
    ]
    _saved_env = {k: os.environ[k] for k in _llm_env_keys}
    for k in _llm_env_keys:
        del os.environ[k]
    try:
        cfg = Settings(_env_file=None).to_llm_config()
        client = build_llm_client(cfg)
        reply = asyncio.run(client.generate("tell me about the lake"))
    finally:
        os.environ.update(_saved_env)
    t.check(
        "default Settings → FakeLLMClient (provider label 'fake')",
        _provider_label(client) == LLMProvider.FAKE.value,
        f"label={_provider_label(client)!r}",
    )
    t.check(
        "placeholder voice served (deterministic digest, non-empty)",
        bool(reply) and reply.startswith("[fake-llm:"),
        f"reply={reply!r}",
    )


def posture_b_explicit_fake(t: Transcript) -> None:
    t.section("(B) Explicit LLM_PROVIDER=fake → FakeLLMClient")
    cfg = Settings(llm_provider="fake").to_llm_config()
    client = build_llm_client(cfg)
    t.check(
        "LLM_PROVIDER=fake → FakeLLMClient",
        _provider_label(client) == LLMProvider.FAKE.value,
        f"label={_provider_label(client)!r}",
    )


def posture_c_unknown_falls_back(t: Transcript) -> None:
    t.section("(C) Unknown provider → fake fallback (misconfig never wires hosted)")
    cfg = Settings(llm_provider="definitely-not-a-real-provider").to_llm_config()
    client = build_llm_client(cfg)
    t.check(
        "LLM_PROVIDER=<garbage> → FakeLLMClient (safe default)",
        _provider_label(client) == LLMProvider.FAKE.value,
        f"label={_provider_label(client)!r}",
    )


def posture_d_openrouter_no_key(t: Transcript) -> None:
    t.section("(D) LLM_PROVIDER=openrouter WITHOUT key → LLMConfigError (safety property)")
    cfg = Settings(llm_provider="openrouter", openrouter_api_key="").to_llm_config()
    raised = False
    try:
        build_llm_client(cfg)
    except LLMConfigError:
        raised = True
    t.check(
        "openrouter without OPENROUTER_API_KEY → LLMConfigError at construction",
        raised,
        "no keyless real-user traffic path exists",
    )


def posture_e_openrouter_keyed(t: Transcript) -> None:
    t.section("(E) LLM_PROVIDER=openrouter + key → persona voice via real /chat/completions")
    cfg = Settings(
        llm_provider="openrouter",
        openrouter_api_key=OPENROUTER_API_KEY,
    ).to_llm_config()
    transport = _openrouter_mock_transport()
    client = build_llm_client(cfg, transport=transport)
    reply = asyncio.run(
        client.generate("tell me about the lake", system_prompt="Warm Texas storyteller.")
    )
    t.check(
        "openrouter + key → OpenRouterLLMClient (provider label 'openrouter')",
        _provider_label(client) == LLMProvider.OPENROUTER.value,
        f"label={_provider_label(client)!r}",
    )
    t.check(
        "persona voice served through the hosted request shape (mock-gated)",
        reply == OPENROUTER_PERSONA_REPLY,
        f"reply={reply!r}",
    )


def posture_f_settings_bridge(t: Transcript) -> None:
    t.section("(F) Settings.to_llm_config() maps env → config (app.py:503 wiring)")
    cfg = Settings(
        llm_provider="openrouter",
        openrouter_api_key=OPENROUTER_API_KEY,
        openrouter_model="google/gemini-3-flash-preview",
    ).to_llm_config()
    t.check(
        "bridge selects provider=openrouter",
        cfg.provider is LLMProvider.OPENROUTER,
        f"provider={cfg.provider!r}",
    )
    t.check(
        "bridge passes OPENROUTER_API_KEY through unchanged",
        cfg.openrouter_api_key == OPENROUTER_API_KEY,
        "key round-trips Settings → LLMConfig",
    )


def posture_g_rollback(t: Transcript) -> None:
    t.section("(G) Rollback — flip LLM_PROVIDER back to fake → FakeLLMClient (one knob)")
    cfg = Settings(llm_provider="fake").to_llm_config()
    client = build_llm_client(cfg)
    t.check(
        "LLM_PROVIDER=fake after openrouter → FakeLLMClient (one-knob rollback)",
        _provider_label(client) == LLMProvider.FAKE.value,
        f"label={_provider_label(client)!r}",
    )


def posture_h_budget_wall(t: Transcript) -> None:
    t.section("(H) $50/mo hard cap — budget exhaustion blocks the hosted call pre-network")
    import tempfile
    from pathlib import Path

    from huible.llm.client import LLMConfig, OpenRouterLLMClient

    hits: list = []

    def handler(request):  # pragma: no cover - mock only
        hits.append(request)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-budget",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "One more story."}}
                ],
                "usage": {"cost": 1.0},
            },
        )

    with tempfile.TemporaryDirectory() as tmp:
        cfg = LLMConfig(
            provider=LLMProvider.OPENROUTER,
            openrouter_api_key=OPENROUTER_API_KEY,
            openrouter_monthly_budget_usd=1.0,
            openrouter_spend_state_path=str(Path(tmp) / "spend.json"),
        )
        client = OpenRouterLLMClient(cfg, transport=httpx.MockTransport(handler))
        first = asyncio.run(client.generate("one more story"))
        t.check("budgeted call 1 succeeds (accrues usage.cost)", first == "One more story.")
        blocked = False
        try:
            asyncio.run(client.generate("and another"))
        except LLMBudgetExceededError:
            blocked = True
        t.check("call 2 at cap → LLMBudgetExceededError", blocked)
        t.check("transport fired exactly once (cap blocks pre-network)", len(hits) == 1)


def posture_i_budget_env(t: Transcript) -> None:
    t.section("(I) Budget env plumbing — default $50 (board-approved), override honored")
    from huible.llm.client import LLMConfig

    default_cfg = LLMConfig.from_env({"LLM_PROVIDER": "openrouter"})
    t.check(
        "default OPENROUTER_MONTHLY_BUDGET_USD == 50 (board decision 2026-08-18)",
        default_cfg.openrouter_monthly_budget_usd == 50.0,
        f"value={default_cfg.openrouter_monthly_budget_usd}",
    )
    override_cfg = LLMConfig.from_env(
        {"LLM_PROVIDER": "openrouter", "OPENROUTER_MONTHLY_BUDGET_USD": "25"}
    )
    t.check(
        "OPENROUTER_MONTHLY_BUDGET_USD=25 override parsed",
        override_cfg.openrouter_monthly_budget_usd == 25.0,
        f"value={override_cfg.openrouter_monthly_budget_usd}",
    )


def section_config(t: Transcript) -> None:
    t.add("=" * 78)
    t.add("HUIBLE — Voice-axis provider-flip rehearsal (HU-1461, PM partial unblock)")
    t.add("=" * 78)
    t.add("activation path: Settings.to_llm_config() -> build_llm_client()  [app.py:503]")
    t.add("openrouter posture uses httpx.MockTransport — zero network, zero spend")
    t.add("the live posture remains LLM_PROVIDER=fake until approval 74a0ff8b grants")


def main() -> int:
    t = Transcript()
    section_config(t)
    posture_a_default_fake(t)
    posture_b_explicit_fake(t)
    posture_c_unknown_falls_back(t)
    posture_d_openrouter_no_key(t)
    posture_e_openrouter_keyed(t)
    posture_f_settings_bridge(t)
    posture_g_rollback(t)
    posture_h_budget_wall(t)
    posture_i_budget_env(t)

    t.add("", "=" * 78)
    if t.failures:
        t.add(f"RESULT: FAIL — {len(t.failures)} posture(s) failed: {', '.join(t.failures)}")
        t.add("=" * 78)
        print(t.dump())
        return 1
    t.add("RESULT: ALL POSTURES PASS — voice-axis flip is a pure env knob.")
    t.add("=" * 78)
    print(t.dump())
    return 0


if __name__ == "__main__":
    sys.exit(main())
