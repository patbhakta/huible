"""Shared setup for the PageIndex eval harness (HU-2723).

Runs all LLM traffic on the z.ai lane (glm-5.3-flash) per the standing
rule while the Google relay is down (HU-2701): PageIndex's LiteLLM lane
is pointed at the OpenAI-compatible z.ai coding endpoint by exporting
OPENAI_API_KEY / OPENAI_BASE_URL before the SDK is imported.

The Google-relay rerun later only needs env switching (GEMINI lane),
not code changes.
"""
import os
import sys
import time
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]  # /root/repos/huible
EVAL_DIR = Path(__file__).resolve().parents[1]  # experiments/pageindex-eval

ZAI_BASE_URL = "https://api.z.ai/api/coding/paas/v4"
FLASH_MODEL = "openai/glm-5.3-flash"
# HU-2726: Gemini flash-ladder arm (boss-approved flash-only ladder, same
# model id the vault VLM lane uses). Direct egress to generativelanguage
# .googleapis.com verified from this box 2026-09-05 (403 identity error =
# reachable); SOCKS5 relay (pat-w11pc:1080) available as fallback but not
# required for the text-only tree-gen lane.
GEMINI_FLASH_MODEL = "gemini/gemini-3.8-flash"


def load_repo_env() -> None:
    """Load /root/repos/huible/.env without leaking values to stdout."""
    try:
        from dotenv import load_dotenv
        load_dotenv(REPO / ".env")
    except ImportError:
        pass


def set_zai_lane() -> None:
    """Point the SDK's OpenAI-compatible lane at z.ai (glm-5.3-flash)."""
    load_repo_env()
    key = os.environ.get("ZAI_API_KEY")
    if not key:
        sys.exit("ZAI_API_KEY not set; cannot use the z.ai lane")
    # Our values win over whatever .env carried for the generic OpenAI lane.
    os.environ["OPENAI_API_KEY"] = key
    os.environ["OPENAI_BASE_URL"] = os.environ.get("ZAI_BASE_URL", ZAI_BASE_URL)


def set_gemini_lane() -> str:
    """HU-2726: point litellm's native gemini provider at the flash ladder.

    Requires GEMINI_API_KEY in the repo .env (litellm reads it at call
    time). Leaves OPENAI_* untouched so the gemini/ prefix routes to
    Google, not the z.ai lane. Returns the resolved model id.
    """
    load_repo_env()
    if not os.environ.get("GEMINI_API_KEY"):
        sys.exit("GEMINI_API_KEY not set; cannot use the Gemini lane "
                 "(request provisioning via the blocker issue on HU-2726)")
    return GEMINI_FLASH_MODEL


class UsageLedger:
    """Deterministic per-call usage capture for the SDK's LLM lane.

    litellm custom success_callbacks proved unreliable under the SDK's
    asyncio path (1 of ~20 calls recorded), so usage is counted in the
    throttle wrapper instead: token_counter(prompt) + token_counter(reply)
    via litellm's offline tokenizer. Same methodology for every arm —
    consistent $/page and $/query comparisons.
    """

    def __init__(self, model: str | None = None):
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.t_first = time.time()
        self.model = model or FLASH_MODEL
        self.records: list[dict] = []

    def record(self, model: str, prompt: str, reply: str, latency_s: float):
        import litellm

        pt = litellm.token_counter(model="gpt-4o", text=prompt or "")
        ct = litellm.token_counter(model="gpt-4o", text=reply or "")
        self.calls += 1
        self.prompt_tokens += pt
        self.completion_tokens += ct
        self.records.append({
            "model": model, "prompt_tokens": pt,
            "completion_tokens": ct, "latency_s": round(latency_s, 2),
        })

    def summary(self) -> dict:
        return {
            "model": self.model,
            "llm_calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "wall_s": round(time.time() - self.t_first, 1),
        }

    def save(self, path: Path) -> dict:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"summary": self.summary(), "calls": self.records}
        path.write_text(json.dumps(payload, indent=2))
        return payload["summary"]


def apply_zai_throttle(max_concurrency: int = 1, ledger: "UsageLedger | None" = None) -> None:
    """Serialize SDK LLM calls for the concurrency-limited z.ai coding lane.

    The SDK defaults to 64 simultaneous summary calls, which the coding
    endpoint rejects immediately. Two harness-side knobs, no SDK fork:
      1. SUMMARY_CONCURRENCY lowered to max_concurrency.
      2. llm_acompletion wrapped with a global lock + exponential backoff
         on 429 (the SDK's own ladder retries 10x1s, too hot/fast).
    A threading lock (not asyncio) is deliberate: the SDK spins a fresh
    event loop per submit_document, so a loop-bound Semaphore would break
    on the second document. Pass ledger= to capture per-call usage here
    (litellm callbacks proved unreliable under asyncio).
    """
    import threading
    import pageindex.utils as pu

    pu.SUMMARY_CONCURRENCY = max_concurrency
    lock = threading.Lock()
    orig = pu.llm_acompletion

    async def throttled(model, prompt):
        for attempt in range(6):
            try:
                with lock:
                    t0 = time.time()
                    reply = await orig(model, prompt)
                    if ledger is not None:
                        ledger.record(model, prompt, reply or "",
                                      time.time() - t0)
                    return reply
            except pu.LLMRetriesExhausted as e:
                if e.status_code != 429 or attempt == 5:
                    raise
                wait_s = 5 * (2 ** attempt)
                print(f"[throttle] 429, backing off {wait_s}s "
                      f"(attempt {attempt + 1}/6)")
                time.sleep(wait_s)
        raise AssertionError("unreachable")

    pu.llm_acompletion = throttled


def new_client(storage_path: Path):
    """Local-mode PageIndex client on the z.ai flash lane."""
    from pageindex import PageIndexLocalClient

    return PageIndexLocalClient(
        storage_path=str(storage_path),
        index_model=FLASH_MODEL,
    )
